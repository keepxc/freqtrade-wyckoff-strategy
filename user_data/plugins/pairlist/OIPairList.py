"""
OIPairList — 动态 pairlist，基于 Binance OI 变化率排序
按 OI 增量的相对变化率降序排列，取 top N。

实现方式：
1. GET fapi/v1/ticker/24hr → 所有永续合约，按 quoteVolume 粗筛 top 50
2. 并行查 futures/data/openInterestHist 取 OI 历史值
3. 用最近两点的 OI 计算变化率（无需文件快照）
4. 按变化率排序，取 top N
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from freqtrade.constants import Config
from freqtrade.exceptions import OperationalException
from freqtrade.exchange import Exchange
from freqtrade.plugins.pairlist.IPairList import IPairList, PairlistParameter, SupportsBacktesting
from freqtrade.util import FtTTLCache, dt_now

logger = logging.getLogger(__name__)

PROXIES = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


class OIPairList(IPairList):
    is_pairlist_generator = True
    supports_backtesting = SupportsBacktesting.NO

    # Binance API 端点
    TICKERS_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    OI_HIST_URL = "https://fapi.binance.com/futures/data/openInterestHist"

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

    @property
    def needstickers(self) -> bool:
        return False

    def short_desc(self) -> str:
        return (
            f"{self.name} - top {self._number_pairs} Binance USDT perpetual pairs "
            f"by OI change rate (15m period)."
        )

    @staticmethod
    def description() -> str:
        return (
            "Provides dynamic pair list based on Binance Open Interest "
            "change rate (15m openInterestHist)."
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
        """获取所有 Binance USDT 永续对，按成交量排序返回候选集"""
        try:
            resp = requests.get(self.TICKERS_URL, proxies=PROXIES, timeout=15)
            resp.raise_for_status()
            tickers = resp.json()
        except Exception as e:
            logger.warning(f"OIPairList: Failed to fetch Binance tickers: {e}")
            return []

        if not isinstance(tickers, list):
            logger.warning(f"OIPairList: Unexpected tickers response type: {type(tickers)}")
            return []

        candidates = []
        for t in tickers:
            symbol: str = t.get("symbol", "")
            # 只保留 USDT 永续（币安永续格式: BTCUSDT）
            if not symbol.endswith("USDT"):
                continue
            # 过滤现货（现货 BTCUSDT 也有，但永续配对没有"USDT"结尾的现货……好吧，binance 永续都是 BTCUSDT 格式）
            # 实际上 Binance fapi/ticker/24hr 只返回永续合约，所以这里不需要过滤现货
            try:
                vol = float(t.get("quoteVolume", 0))
            except (ValueError, TypeError):
                continue
            if vol <= 0:
                continue
            candidates.append({"symbol": symbol, "quoteVolume": vol})

        # 按成交量降序取候选池
        candidates.sort(key=lambda x: x["quoteVolume"], reverse=True)
        top = candidates[:self.CANDIDATE_POOL_SIZE]
        logger.info(f"OIPairList: Candidate pool: {len(top)} Binance pairs by volume")
        return top

    def _fetch_single_oi(self, symbol: str) -> tuple[str, float | None, float | None]:
        """
        查单个币种的 OI 历史（15m 周期），返回 (symbol, current_oi, prev_oi)。
        用 openInterestHist 的最新两条记录计算变化率。
        """
        try:
            params = {
                "symbol": symbol,
                "period": "15m",
                "limit": 2,
            }
            resp = requests.get(self.OI_HIST_URL, params=params, proxies=PROXIES, timeout=10)
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list) or len(rows) < 2:
                # 不足两条 → 只有当前 OI 无变化率
                if len(rows) == 1:
                    return symbol, float(rows[0]["sumOpenInterest"]), None
                return symbol, None, None

            cur_oi = float(rows[-1]["sumOpenInterest"])
            prev_oi = float(rows[0]["sumOpenInterest"])
            return symbol, cur_oi, prev_oi
        except Exception as e:
            logger.debug(f"OIPairList: OI fetch failed for {symbol}: {e}")
            return symbol, None, None

    def _rank_by_oi_change(self, candidates: list[dict]) -> list[dict]:
        """
        并行查 OI 历史，用最近两点的变化率排序。
        首次或数据不足时用 OI 绝对值排序作为 fallback。
        """
        symbols = [c["symbol"] for c in candidates]
        logger.info(f"OIPairList: Fetching OI for {len(symbols)} candidates (parallel)...")

        all_results: list[dict] = []
        has_change = False  # 是否有变化率数据

        with ThreadPoolExecutor(max_workers=self.PARALLEL_WORKERS) as executor:
            fut_map = {executor.submit(self._fetch_single_oi, sym): sym for sym in symbols}
            for fut in as_completed(fut_map):
                sym, cur_oi, prev_oi = fut.result()
                if cur_oi is None or cur_oi <= 0:
                    continue
                if prev_oi and prev_oi > 0:
                    change_pct = (cur_oi - prev_oi) / prev_oi
                    has_change = True
                else:
                    change_pct = 0.0
                all_results.append({
                    "symbol": sym,
                    "oi": cur_oi,
                    "change_pct": change_pct,
                })

        if not all_results:
            logger.warning("OIPairList: No OI data returned.")
            return []

        # 有变化率 → 按变化率降序（变化率相同按 OI 绝对值）
        # 无变化率 → 全部按 OI 绝对值降序
        if has_change:
            all_results.sort(key=lambda x: (x["change_pct"], x["oi"]), reverse=True)
        else:
            all_results.sort(key=lambda x: x["oi"], reverse=True)

        logger.info(
            f"OIPairList: Ranked {len(all_results)} pairs. "
            f"Top: {all_results[0]['symbol']} "
            f"(change: {all_results[0]['change_pct']:+.4%}, OI: {all_results[0]['oi']:.0f})"
        )
        return all_results

    def _binance_to_freqtrade(self, symbol: str) -> str | None:
        """BTCUSDT → BTC/USDT:USDT"""
        if not symbol.endswith("USDT"):
            return None
        base = symbol[:-4]  # 去掉 "USDT"
        return f"{base}/USDT:USDT"

    def gen_pairlist(self, tickers: dict) -> list[str]:
        """生成按 OI 变化率排序的 pairlist"""
        pairlist = self._pair_cache.get("pairlist")
        if pairlist:
            return pairlist.copy()

        t_start = time.time()

        # 阶段1: 获取候选池（按成交量 top 50）
        candidates = self._fetch_candidates()
        if not candidates:
            logger.warning("OIPairList: No candidates available.")
            return []

        # 阶段2: 并行查 OI，算变化率
        ranked = self._rank_by_oi_change(candidates)
        if not ranked:
            return []

        # 取 top N
        top_n = ranked[:self._number_pairs]

        # 转换格式
        freq_pairs = []
        for item in top_n:
            ft_pair = self._binance_to_freqtrade(item["symbol"])
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
