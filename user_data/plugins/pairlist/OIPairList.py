"""
OIPairList — 动态 pairlist，基于 OKX OI 变化率排序
按 OI 增量的相对变化率降序排列，取 top N。

实现方式：
1. GET /api/v5/market/tickers?instType=SWAP → 所有永续合约，按 volCcy24h 粗筛 top 50
2. 并行查 current open-interest 取当前 OI
3. 与文件缓存的上次 OI 快照对比计算变化率
4. 按变化率排序，取 top N

首次运行时无历史快照，按 OI 绝对值排序作为 fallback。
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from freqtrade.constants import Config
from freqtrade.exceptions import OperationalException
from freqtrade.exchange import Exchange
from freqtrade.plugins.pairlist.IPairList import IPairList, PairlistParameter, SupportsBacktesting
from freqtrade.util import FtTTLCache, dt_now

logger = logging.getLogger(__name__)


class OIPairList(IPairList):
    is_pairlist_generator = True
    supports_backtesting = SupportsBacktesting.NO

    # OKX API 端点
    TICKERS_URL = "https://www.okx.com/api/v5/market/tickers"
    OI_URL = "https://www.okx.com/api/v5/public/open-interest"

    INST_TYPE = "SWAP"

    # OKX 永续对 → freqtrade 格式
    OKX_SUFFIX = "-SWAP"
    FREQTRADE_SUFFIX = "/USDT:USDT"

    # 配置
    CANDIDATE_POOL_SIZE = 50      # 粗筛候选数量
    PARALLEL_WORKERS = 20         # 并行查 OI 的线程数

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        if "number_assets" not in self._pairlistconfig:
            raise OperationalException(
                "OIPairList requires `number_assets` to be set in config."
            )

        self._number_pairs: int = int(self._pairlistconfig["number_assets"])
        self._refresh_period = self._pairlistconfig.get("refresh_period", 1800)

        # pair 缓存
        self._pair_cache: FtTTLCache = FtTTLCache(maxsize=1, ttl=self._refresh_period)
        self._stake_currency: str = self._config["stake_currency"]

        # OI 快照文件（持久化，重启后仍能计算变化率）
        snapshot_dir = Path(self._config["user_data_dir"]) / "pairlist_cache"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._snapshot_file: Path = snapshot_dir / "oi_snapshot.json"

    @property
    def needstickers(self) -> bool:
        return False

    def short_desc(self) -> str:
        return (
            f"{self.name} - top {self._number_pairs} OKX USDT perpetual pairs "
            f"by OI change rate (15m since last scan)."
        )

    @staticmethod
    def description() -> str:
        return (
            "Provides dynamic pair list based on OKX Open Interest "
            "change rate (cached snapshot comparison)."
        )

    @staticmethod
    def available_parameters() -> dict[str, PairlistParameter]:
        return {
            "number_assets": {
                "type": "number",
                "default": 10,
                "description": "Number of assets",
                "help": "Number of assets to select from the pairlist.",
            },
            **IPairList.refresh_period_parameter(),
        }

    def _fetch_candidates(self) -> list[dict]:
        """获取所有 OKX USDT 永续对，按成交量排序返回候选集"""
        params = {"instType": self.INST_TYPE}
        try:
            resp = requests.get(self.TICKERS_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"OIPairList: Failed to fetch OKX tickers: {e}")
            return []

        if data.get("code") != "0":
            logger.warning(f"OIPairList: OKX API error code: {data.get('code')}")
            return []

        tickers = data.get("data", [])
        candidates = []
        for t in tickers:
            inst_id: str = t.get("instId", "")
            # 只保留 USDT 保证金永续
            if not inst_id.endswith("USDT-SWAP"):
                continue
            vol = t.get("volCcy24h", "0")
            try:
                vol = float(vol)
            except (ValueError, TypeError):
                continue
            if vol <= 0:
                continue
            candidates.append({"symbol": inst_id, "quoteVolume": vol})

        # 按成交量降序取候选池
        candidates.sort(key=lambda x: x["quoteVolume"], reverse=True)
        top = candidates[:self.CANDIDATE_POOL_SIZE]
        logger.info(f"OIPairList: Candidate pool: {len(top)} OKX pairs by volume")
        return top

    def _fetch_single_oi(self, symbol: str) -> tuple[str, float | None]:
        """查单个币种的当前 OI（以 USD 计价），返回 (symbol, oi_usd)"""
        try:
            params = {
                "instType": self.INST_TYPE,
                "instId": symbol,
            }
            resp = requests.get(self.OI_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                return symbol, None
            rows = data.get("data", [])
            if not rows:
                return symbol, None
            # 用 oiUsd（美元计价）便于跨币种对比
            return symbol, float(rows[0]["oiUsd"])
        except Exception:
            return symbol, None

    def _load_saved_snapshot(self) -> dict[str, float]:
        """从文件加载上次 OI 快照"""
        try:
            if self._snapshot_file.exists():
                with open(self._snapshot_file) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_snapshot(self, snapshot: dict[str, float]):
        """保存 OI 快照到文件"""
        try:
            with open(self._snapshot_file, "w") as f:
                json.dump(snapshot, f)
        except Exception as e:
            logger.warning(f"OIPairList: Failed to save OI snapshot: {e}")

    def _rank_by_oi_change(self, candidates: list[dict]) -> list[dict]:
        """
        并行查当前 OI，与缓存快照对比计算变化率，返回排序结果。
        首次运行时用 OI 绝对值排序（无变化率）。
        """
        symbols = [c["symbol"] for c in candidates]
        logger.info(f"OIPairList: Fetching OI for {len(symbols)} candidates (parallel)...")

        # 并行查当前 OI
        oi_current: dict[str, float] = {}

        with ThreadPoolExecutor(max_workers=self.PARALLEL_WORKERS) as executor:
            fut_map = {executor.submit(self._fetch_single_oi, sym): sym for sym in symbols}
            for fut in as_completed(fut_map):
                sym, oi_usd = fut.result()
                if oi_usd is not None and oi_usd > 0:
                    oi_current[sym] = oi_usd

        logger.info(f"OIPairList: Got OI for {len(oi_current)}/{len(symbols)} candidates.")

        # 加载上次快照
        saved_snapshot = self._load_saved_snapshot()

        results = []
        for c in candidates:
            sym = c["symbol"]
            cur_oi = oi_current.get(sym)
            if cur_oi is None or cur_oi <= 0:
                continue
            old_oi = saved_snapshot.get(sym)
            if old_oi and old_oi > 0:
                change_pct = (cur_oi - old_oi) / old_oi
            else:
                change_pct = 0.0  # 首次出现，无变化率
            results.append({
                "symbol": sym,
                "oi": cur_oi,
                "change_pct": change_pct,
            })

        # 按变化率降序（变化率相同的按 OI 绝对值降序）
        results.sort(key=lambda x: (x["change_pct"], x["oi"]), reverse=True)

        # 保存当前 OI 到快照文件（供下次使用）
        self._save_snapshot(oi_current)

        if results:
            logger.info(
                f"OIPairList: Ranked {len(results)} pairs. "
                f"Top: {results[0]['symbol']} (change: {results[0]['change_pct']:+.4%})"
            )
        return results

    def _okx_to_freqtrade(self, okx_symbol: str) -> str | None:
        """OKX BTC-USDT-SWAP → freqtrade BTC/USDT:USDT"""
        if not okx_symbol.endswith(self.OKX_SUFFIX):
            return None
        base_quote = okx_symbol[: -len(self.OKX_SUFFIX)]  # "BTC-USDT"
        parts = base_quote.split("-")
        if len(parts) != 2:
            return None
        base, quote = parts
        return f"{base}/{quote}:{quote}"

    def gen_pairlist(self, tickers: dict) -> list[str]:
        """生成按 OI 变化率排序的 pairlist"""
        pairlist = self._pair_cache.get("pairlist")
        if pairlist:
            return pairlist.copy()

        t_start = time.time()

        # 阶段1: 获取候选池
        candidates = self._fetch_candidates()
        if not candidates:
            logger.warning("OIPairList: No candidates available.")
            return []

        # 阶段2: 并行查 OI，算变化率
        ranked = self._rank_by_oi_change(candidates)
        if not ranked:
            logger.warning("OIPairList: No OI data returned.")
            return []

        # 取 top N
        top_n = ranked[:self._number_pairs]

        # 转换格式
        freq_pairs = []
        for item in top_n:
            ft_pair = self._okx_to_freqtrade(item["symbol"])
            if ft_pair:
                freq_pairs.append(ft_pair)

        # 黑名单过滤
        freq_pairs = self.verify_blacklist(freq_pairs, logger.info)

        elapsed = time.time() - t_start
        self._pair_cache["pairlist"] = freq_pairs.copy()
        logger.info(
            f"OIPairList: Generated {len(freq_pairs)} pairs in {elapsed:.1f}s. "
            f"Top: {freq_pairs[0] if freq_pairs else 'N/A'} "
            f"(change: {top_n[0]['change_pct']:+.4%})"
        )
        return freq_pairs

    def filter_pairlist(self, pairlist: list[str], tickers: dict) -> list[str]:
        return self.gen_pairlist(tickers)
