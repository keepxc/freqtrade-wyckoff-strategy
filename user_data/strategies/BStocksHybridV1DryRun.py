"""
BStocksHybridV1DryRun — BStocksHybridV1 + 美股时段执行层限制（dry-run 专用）
=============================================================
仅在 BStocksHybridV1 已回测验证的信号/仓位逻辑之外，加一层执行层过滤：
只允许在美股常规交易时段（NYSE/Nasdaq，周一至周五 13:30-20:00 UTC，
即 9:30-16:00 ET，不含盘前盘后）开新仓；不限制退出（通道退出与
-50% 回撤止损必须能随时触发，用户已确认："按你的建议"）。

用户确认口径（2026-08-07）：
  1. 实现方式 = confirm_trade_entry 限制入场时段，confirm_trade_exit 不限制
  2. 节假日排除，交给 agent 自行判断实现（"我觉得不复杂"）
  3. timeframe 维持 1d 不变（dry-run 阶段不做 4H/1H 粒度迁移，见 TASK109 附注）

**不修改 BStocksHybridV1.py 本体**（该文件的回测结果已写入 TASK109 并
作为采纳基线归档）——本文件继承并仅追加执行层过滤，保持"改动范围不
过度扩大"（CLAUDE.md 三层校验第三层前禁止顺便扩大改动范围的同一纪律
延伸到这里：信号/仓位逻辑与执行层限制是两件事，不应该混在同一次改动里）。

节假日实现说明：当前 Docker 镜像（freqtradeorg/freqtrade:stable）不含
pandas_market_calendars，未见项目自定义 Dockerfile 可加依赖，因此采用
硬编码 NYSE 假期表（2024-2027，含因周末顺延的观察日）。此表需要每年
人工维护更新；若未来接入 pandas_market_calendars（需自定义镜像），
应替换为该库的官方日历，避免依赖手工表的维护风险。此限制仅影响
dry-run/实盘执行层，不影响已封板的回测结论。

**重要：本文件不适合用于回测，只用于 live/dry-run。** 已实测验证：
用本文件跑 `backtesting` 命令会得到 0 笔交易，这是预期行为而非 bug——
回测在 1d 时间框架下只在每根蜡烛收盘时刻（00:00 UTC）评估一次信号，
不模拟一天内反复轮询，而 00:00 UTC 必然落在 13:30-20:00 UTC 窗口外，
导致 confirm_trade_entry 每次都返回 False。live/dry-run 模式下机器人
按 `internals.process_throttle_secs` 持续轮询，只要入场信号仍然有效
（尚未到下一根日线收盘产生新信号），就会反复尝试 confirm_trade_entry，
直到某次轮询落在美股开盘时段内才成交——这才是本过滤器的实际工作方式。
回测/统计结论仍以不含此过滤器的 `BStocksHybridV1.py`（TASK109 基线）
为准，本文件只影响实盘执行时机，不改变已验证的信号与仓位逻辑。
"""
from datetime import datetime, time
from typing import Optional

from freqtrade.persistence import Trade

from BStocksHybridV1 import BStocksHybridV1

# NYSE/Nasdaq 全天休市日（UTC 日期，含周末顺延观察日），需逐年维护
NYSE_HOLIDAYS = {
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
    "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

MARKET_OPEN_UTC = time(13, 30)
MARKET_CLOSE_UTC = time(20, 0)


class BStocksHybridV1DryRun(BStocksHybridV1):

    def _is_market_open(self, current_time: datetime) -> bool:
        if current_time.weekday() >= 5:  # 5=Sat, 6=Sun
            return False
        if current_time.strftime("%Y-%m-%d") in NYSE_HOLIDAYS:
            return False
        t = current_time.time()
        return MARKET_OPEN_UTC <= t < MARKET_CLOSE_UTC

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        return self._is_market_open(current_time)

    # confirm_trade_exit 不覆盖，保持父类/框架默认行为（不限制退出时段），
    # 通道退出与 -50% 回撤止损必须能随时触发。
