"""
BStockPairList —— 币安 bStock 专用 pairlist 过滤器

用途：VolumePairList 动态发现全部 USDT 交易对后，只保留 bStock（正股代币）。
bStock 特征：base 币名以 'B' 结尾（PYPLB、MUB、SNDKB、SPYB、QQQB...）。

用法（config pairlists 顺序）：
[
    {"method": "VolumePairList", "number_assets": 300, "sort_key": "quoteVolume", "min_value": 100000},
    {"method": "BStockPairList"}
]
"""
import logging
from freqtrade.exchange.exchange_types import Tickers
from freqtrade.plugins.pairlist.IPairList import IPairList

logger = logging.getLogger(__name__)


class BStockPairList(IPairList):
    is_pairlist_generator = False  # 过滤器，不是生成器

    def __init__(self, exchange, pairlistmanager, config, pairlistconfig, pairlist_pos):
        super().__init__(exchange, pairlistmanager, config, pairlistconfig, pairlist_pos)

        # 可配置：白名单/黑名单补充（可选）
        self._extra_whitelist = self._pairlistconfig.get("extra_whitelist", [])
        self._blacklist = self._pairlistconfig.get("blacklist", [])

    @property
    def needstickers(self) -> bool:
        """这个过滤器本身不需要 tickers（只过滤名字）"""
        return False

    def gen_pairlist(self, tickers: Tickers) -> list[str]:
        """生成器模式（如果放在第一位）——这里仅过滤，不生成"""
        raise NotImplementedError("BStockPairList 是过滤器，不能放在第一位")

    def filter_pairlist(self, pairlist: list[str], tickers: Tickers) -> list[str]:
        """只保留 bStock：base 以 B 结尾 + 不在黑名单"""
        filtered = []
        for pair in pairlist:
            if pair in self._blacklist:
                continue
            base = pair.split("/")[0]
            # bStock: base 名以 B 结尾（PYPLB → PYPL + B）
            if base.endswith("B"):
                filtered.append(pair)

        # 加入额外白名单（允许手动补充）
        for pair in self._extra_whitelist:
            if pair not in filtered:
                filtered.append(pair)

        if self._pairlistconfig.get("log", False):
            logger.info(f"BStockPairList: {len(pairlist)} → {len(filtered)} pairs")
        return filtered
