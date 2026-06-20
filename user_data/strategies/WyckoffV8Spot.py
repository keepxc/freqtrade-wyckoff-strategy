"""
WyckoffV8Spot — 仅用于多头引擎样本外（OOS）验证的薄子类
============================================================
目的：在 spot 数据（BTC/USDT，2018 起）上单独验证 V8 多头引擎，
覆盖 2018-12 SC 底 + 2020-03 COVID SC 底（参数从未见过的真实积累结构）。

唯一改动：can_short=False（spot 不能做空，隔离出纯多头结果）。
不改任何入场/出场/指标逻辑——结构判断完全复用 WyckoffV8。

⚠️ 这是验证工具，不是策略变体。不参与封板基线，不进 dry run。
"""

from WyckoffV8 import WyckoffV8


class WyckoffV8Spot(WyckoffV8):
    can_short = False
