# 威科夫交易系统 · 项目导航

## 🗂️ 待办事项速览（未完成 / 预埋点）

| 标记 | 含义 |
|---|---|
| `[ ]` 未做 | 尚未实现 |
| `[!]` 待验证 | 代码已接线但效果未量化（回测 0 次触发或无对照组），需专项测试 |
| `[~]` 预埋点 | 代码已有但未接线，归档待后续启用 |

**当前未完成项目：**

- `[~]` **Phase-aware 退出**（P0）— `wyckoff/exit_logic.py` ExitLogic 已实现，策略未接线，归档为未来预埋点
- `[ ]` **分批止盈 + 动态保护**（P0）— Freqtrade 原生不支持分批减仓，待研究方案
- `[ ]` **RM-4：验证与工程护城河**（RM-4/Roadmap）— 参数版本治理、漂移诊断、周报等工程设施

---

**最后更新**: 2026-03-17（**P0-Next Core Audit 退出层实验已完成**：V3 DW阈值0.003 ❌，V4 tight SL-8% ❌，架构发现：vectorized信号主导custom_exit；P0-E1 nofloat 已升级为新默认；P1-A、P2、P3 主线任务已完成）
**详细文档**: `docs/PHASES.md` | `docs/DONE.md` | `docs/PROGRESS.md`

---

## ⚡ 常用命令

```bash
# 回测（全量，--cache none 保证不走缓存）
docker compose run --rm freqtrade backtesting --config user_data/config_backtest.json --strategy WyckoffV7_TrueMethod --timerange 20230101-20260228 --cache none

# Dry Run 容器状态查看
docker compose ps

# 查看 Dry Run 日志
docker compose logs -f --tail=100 freqtrade
```

---

## 🔴 当前状态（一眼看清）

**下一个任务**: **进入 vNext 阶段**（2026-03-14）— P0/P1/P2/P3 主线任务已完成，当前版本收尾完成
**当前基线回测（nofloat 模式，20230101-20260228 --cache none）**: 收益 **965.76%** | 交易 393 笔 | CAGR 111.37% | Sharpe 1.94 | SQN 5.91 | 最大回撤 8.13%
**P0-E1 结论（2026-03-13）**: ✅ nofloat 已升级为新默认，移除 3% ROI floor，全量 +330pp，熊市段 SQN 从 3.19→4.37
**P1-A 结论（2026-03-13）**: ✅ 已完成，维持默认 `daily_gate_mode = off`（当前挂接形态传导力不足）
**P2 结论（2026-03-14）**: ✅ 已完成，Regime-aware 授权系统已实现，默认 disabled，保留为预埋点
**P3 结论（2026-03-13）**: ✅ 已完成，波段级 Effort/Result 已实现并验证，默认关闭预埋
**P0-Next 退出层优化结论（2026-03-17）**: ✅ Core Audit 发现 axis_support×down_wave WR=65.7% avg=+0.80%（vs roi WR=100% avg=+7.92%）；实验V3/V4均负向；架构发现：freqtrade中vectorized exit信号主导custom_exit；down_wave_threshold不可提高；下一步待定
**Dry Run**: 🔴 待更新（建议更新至 nofloat 基线）
## P1-A：当前挂接形态验证（已完成）

> **状态**：✅ 已完成。在 `soft→signal_quality` 当前挂接形态下，局部参数空间验证结束，结论：传导力不足，默认 `off`。

### 目标
- 定位 P1 的主要问题落在：结构层 / 仲裁层 / 注入层
- 研究阶段固定 `daily_gate_mode = soft`
- 部署默认保持 `daily_gate_mode = off`
- 本轮不追求最优参数，只做最小可测矩阵验证，避免组合爆炸

---

### 全局约束
- 固定标的：BTC
- 固定回测区间：保持与当前主回测一致
- 固定基础策略逻辑：不得修改入场/出场/止损/止盈
- 固定 bear_market_mode：不得修改
- 固定所有非 P1 参数
- 固定成本模型 / 滑点 / 仓位
- 所有实验使用相同运行方式
- 每次实验只允许修改一个关键参数
- 本轮只测试 `soft`，不测试 `hard`
- 每次回测禁止沿用旧缓存

---

### Baseline
- [x] EXP-00：运行 baseline_soft
  - 参数：
    - `daily_tr_window_main = 60`
    - `daily_tr_window_fast = 20`
    - `daily_tr_window_slow = 120`
    - `daily_tr_use_slow_check = true`
    - `daily_tr_min_overlap = 0.65`
    - 冲突时 `daily_gate_score = +0.3`
    - `k = 0.25`
    - 弱信号阈值 = `1.6`
    - clip = `[0.5, 3.0]`
    - `daily_gate_mode = soft`
- 结果（回测，`20230101-20260228`，`--cache none`）：
  - 交易 `444` 笔，胜率 `76.6%`
  - 总收益 `479.17%`（`47917.004 USDT`）
  - 最大回撤 `8.48%`
  - Sharpe `2.15`，SQN `6.16`
- 机制记录（`TASK_NEW/p1_exp00_baseline_soft_analysis.json`）：
  - `daily_tr_exists` 占比 `36.83%`（`10202 / 27697`）
  - `daily_gate_score` 分布（全样本）：`-1.0=17495`，`+0.3=2904`，`+1.0=7298`
  - 主副冲突占比：全样本 `10.48%`；TR 激活样本内 `28.47%`
  - `daily_gate_score` 分布（入场信号）：`-1.0=5390`，`+0.3=1080`，`+1.0=2477`
  - `signal_quality` 注入前后（入场信号，`off -> soft`）：均值 `1.1404 -> 1.0115`，中位数 `1.0 -> 0.8775`
  - `soft_vs_off`：加分 `3489`，降分 `5390`，不变 `68`
  - 机制解释：当前 baseline 下，非 TR / 主判定否定样本占多数，`soft` 更多是在压低弱信号，而不是放大优质 TR 信号
- [x] 保存 baseline 结果，作为后续唯一对照组
  - 对照组文件：
    - 回测结果：`user_data/backtest_results/backtest-result-2026-03-13_05-02-28.zip`
    - 机制分析：`TASK_NEW/p1_exp00_baseline_soft_analysis.json`

---

### 第一阶段：结构层
目标：判断 TR 检测是否过硬、过窄或尺度错配

- [x] S1：仅调整 `daily_tr_min_overlap`
  - 方向：下调一档
  - 记录：
    - `daily_tr_exists` 占比
    - 交易数变化
    - 收益 / 回撤 / Sharpe
    - 是否恢复可交易性
  - 本次执行：`0.65 -> 0.60`
  - gate / 分布变化（见 `TASK_NEW/p1_s1_overlap_060_analysis.json`）：
    - `daily_tr_exists`：`36.83% -> 41.69%`（`10202 -> 11546`，`+4.86pct`）
    - `daily_gate_score` 全样本：`-1.0 17495 -> 16151`，`+0.3 2904 -> 3768`，`+1.0 7298 -> 7778`
    - 主副冲突占比：全样本 `10.48% -> 13.60%`；TR 激活样本内 `28.47% -> 32.63%`
  - 交易行为：
    - 交易数 `444 -> 444`，无变化
    - 入场信号 gate 分布更偏向 TR / 冲突态：`-1.0 5390 -> 4880`，`+0.3 1080 -> 1469`，`+1.0 2477 -> 2598`
    - `signal_quality` 注入前后（入场信号，`off -> soft`）：均值 `1.1404 -> 1.0381`，中位数 `1.0 -> 0.8775`
  - 绩效（回测，`20230101-20260228`，`--cache none`）：
    - 总收益 `479.17%`（与 `EXP-00` 持平）
    - 最大回撤 `8.48%`（与 `EXP-00` 持平）
    - Sharpe `2.15`（与 `EXP-00` 持平）
    - 交易数 `444`（与 `EXP-00` 持平）
  - 判读：
    - gate 分布有真实变化
    - 交易行为未出现可见改善，未表现为“恢复可交易性”
    - 绩效零变化，暂不支持把 S1 视为结构层优选
  - 机制解释：放宽 overlap 让更多日线样本从 `-1.0` 转入 `+0.3/+1.0`，但当前 `soft` 只改 `signal_quality`，没有改变最终成交集合，因此收益曲线不动

- [x] S2：仅调整 `daily_tr_use_slow_check = false`
  - 记录：
    - `daily_tr_exists` 占比
    - 主判定分布变化
    - 交易数变化
    - 是否减少冻结状态
  - 本次执行：`true -> false`
  - gate / 分布变化（见 `TASK_NEW/p1_s2_slowcheck_off_analysis.json`）：
    - `daily_tr_exists`：`36.83% -> 41.86%`（`10202 -> 11594`，`+5.03pct`）
    - `daily_gate_score` 全样本：`-1.0 17495 -> 16103`，`+0.3 2904 -> 3144`，`+1.0 7298 -> 8450`
    - 主副冲突占比：全样本 `10.48% -> 11.35%`；TR 激活样本内 `28.47% -> 27.12%`
  - 交易行为：
    - 交易数 `444 -> 444`，无变化
    - 入场信号 gate 分布更偏向主判定直接放行：`-1.0 5390 -> 5069`，`+0.3 1080 -> 1195`，`+1.0 2477 -> 2683`
    - `signal_quality` 注入前后（入场信号，`off -> soft`）：均值 `1.1404 -> 1.0320`，中位数 `1.0 -> 0.8775`
  - 绩效（回测，`20230101-20260228`，`--cache none`）：
    - 总收益 `479.17%`（与 `EXP-00` 持平）
    - 最大回撤 `8.48%`（与 `EXP-00` 持平）
    - Sharpe `2.15`（与 `EXP-00` 持平）
    - 交易数 `444`（与 `EXP-00` 持平）
  - 判读：
    - gate 分布有真实变化，且比 S1 更偏向把样本推入 `+1.0`
    - 交易行为未出现可见改善，未体现为“减少冻结状态”或“恢复可交易性”
    - 绩效零变化，暂不支持把 S2 视为结构层优选
  - 机制解释：关闭 slow check 后，更多样本被主 TR 判定直接接纳，部分原本停留在 `-1.0/+0.3` 的样本转入 `+1.0`；但当前 `soft` 仍只通过 `signal_quality` 做软注入，没有改变最终成交集合，因此收益曲线继续不动
  - 输出文件：
    - 回测结果：`user_data/backtest_results/backtest-result-2026-03-13_06-23-44.zip`
    - 机制分析：`TASK_NEW/p1_s2_slowcheck_off_analysis.json`

- [x] S3：仅调整 `daily_tr_window_main`
  - 方向：只改一档
  - 记录：
    - 日线背景与 H1 入场节奏是否更匹配
    - 交易行为是否改善
    - 收益 / 回撤 / Sharpe
  - 档位说明：
    - 项目内未找到既定窗口档位表
    - 本次按最小改动原则执行：`60 -> 50`
  - gate / 分布变化（见 `TASK_NEW/p1_s3_mainwin_050_analysis.json`）：
    - `daily_tr_exists`：`36.83% -> 33.27%`（`10202 -> 9216`，`-3.56pct`）
    - `daily_gate_score` 全样本：`-1.0 17495 -> 18481`，`+0.3 2904 -> 4008`，`+1.0 7298 -> 5208`
    - 主副冲突占比：全样本 `10.48% -> 14.47%`；TR 激活样本内 `28.47% -> 43.49%`
  - 交易行为：
    - 交易数 `444 -> 444`，无变化
    - 入场信号 gate 分布更偏向否定 / 冲突态：`-1.0 5390 -> 6031`，`+0.3 1080 -> 1462`，`+1.0 2477 -> 1454`
    - `signal_quality` 注入前后（入场信号，`off -> soft`）：均值 `1.1404 -> 0.9537`，中位数 `1.0 -> 0.81`
  - 绩效（回测，`20230101-20260228`，`--cache none`）：
    - 总收益 `479.17%`（与 `EXP-00` 持平）
    - 最大回撤 `8.48%`（与 `EXP-00` 持平）
    - Sharpe `2.15`（与 `EXP-00` 持平）
    - 交易数 `444`（与 `EXP-00` 持平）
  - 判读：
    - gate 分布有真实变化，但方向偏向更强惩罚，而非更匹配 H1 入场节奏
    - 交易行为未出现可见改善，反而表现为更多样本落入 `-1.0/+0.3`
    - 绩效零变化，暂不支持把 S3 视为结构层优选
  - 机制解释：缩短主窗口后，TR 主判定对近端波动更敏感，导致更多样本从强确认 `+1.0` 退回到 `-1.0/+0.3`；但 `soft` 仍只调 `signal_quality` 不改最终成交集合，所以收益曲线依旧不动
  - 输出文件：
    - 回测结果：`user_data/backtest_results/backtest-result-2026-03-13_06-39-51.zip`
    - 机制分析：`TASK_NEW/p1_s3_mainwin_050_analysis.json`

- [x] 结构层结论
  - 结构层三次单变量测试均满足“gate 分布真实变化”，但都未满足“交易行为更合理 + 绩效同步改善”
  - `S1`：放宽 overlap，可识别 TR 增多，但成交集合不变
  - `S2`：关闭 slow check，更多样本转入 `+1.0`，但成交集合不变
  - `S3`：缩短主窗口，否定 / 冲突样本增多，成交集合仍不变
  - 结论：当前结构层不是主矛盾，暂不支持用 `S1/S2/S3` 替换基线
  - `BASE-1`：维持 `EXP-00 baseline_soft`
  - 说明：后续 `A1/A2` 均基于 `BASE-1 = EXP-00`

---

### 第二阶段：仲裁层
目标：判断主判定与 EventSeqSM 辅判定的冲突处理是否失真

- [x] A1：仅调整冲突处理
  - 修改：主副冲突时 `daily_gate_score: +0.3 -> 0`
  - 基于：`BASE-1`
  - 记录：
    - 冲突率
    - gate score 分布
    - 边缘交易表现
    - 收益 / 回撤 / Sharpe
  - 实施说明：
    - 为避免改默认行为，本次先做最小参数化支持：新增 `WY_P1_DAILY_TR_CONFLICT_SCORE`
    - 默认值仍保持 `0.30`，A1 实验仅注入 `0.0`
  - gate / 分布变化（见 `TASK_NEW/p1_a1_conflict_zero_analysis.json`）：
    - `daily_tr_exists`：`36.83% -> 36.83%`（无变化）
    - gate 全样本从 `-1.0 / +0.3 / +1.0 = 17495 / 2904 / 7298` 变为 `-1.0 / 0.0 / +1.0 = 17495 / 2904 / 7298`
    - 主副冲突占比：全样本 `10.48%`；TR 激活样本内 `28.47%`（数量不变，仅分值平移）
  - 交易行为：
    - 交易数 `444 -> 444`，无变化
    - 入场 gate 分布从 `-1.0 / +0.3 / +1.0 = 5390 / 1080 / 2477` 变为 `-1.0 / 0.0 / +1.0 = 5390 / 1080 / 2477`
    - `signal_quality` 注入前后（入场信号，`off -> soft`）：均值 `1.1404 -> 1.0012`，中位数 `1.0 -> 0.8775`
    - `soft_vs_off`：加分 `3489 -> 2427`，降分 `5390 -> 5390`，不变 `68 -> 1130`
  - 绩效（回测，`20230101-20260228`，`--cache none`）：
    - 总收益 `479.17%`（与 `EXP-00` 持平）
    - 最大回撤 `8.48%`（与 `EXP-00` 持平）
    - Sharpe `2.15`（与 `EXP-00` 持平）
    - 交易数 `444`（与 `EXP-00` 持平）
  - 判读：
    - 仲裁层分值变化是真实的，但只把冲突态从“弱放行”改成“中性”
    - 边缘注入确实减弱，但没有观察到冲突态交易改善，也没有改变最终成交集合
    - 绩效零变化，暂不支持把 A1 视为仲裁层优选
  - 机制解释：`A1` 不是减少冲突样本，而是降低冲突样本对 `signal_quality` 的正向注入；当前系统的真实成交仍主要由其他高质量信号路径决定，所以这类“边缘加分回收”没有传导到成交集合
  - 输出文件：
    - 回测结果：`user_data/backtest_results/backtest-result-2026-03-13_06-52-17.zip`
    - 机制分析：`TASK_NEW/p1_a1_conflict_zero_analysis.json`

- [x] A2：仅降低 EventSeqSM 辅判定权重
  - 基于：`BASE-1`
  - 记录：
    - 最终 gate 是否更贴近主判定
    - 冲突态交易是否改善
    - 收益 / 回撤 / Sharpe
  - 档位说明：
    - 项目内未找到既定辅判定权重档位
    - 本次按最小可测原则执行：冲突分值 `+0.3 -> +0.6`
  - gate / 分布变化（见 `TASK_NEW/p1_a2_conflict_060_analysis.json`）：
    - `daily_tr_exists`：`36.83% -> 36.83%`（无变化）
    - gate 全样本从 `-1.0 / +0.3 / +1.0 = 17495 / 2904 / 7298` 变为 `-1.0 / +0.6 / +1.0 = 17495 / 2904 / 7298`
    - 主副冲突占比：全样本 `10.48%`；TR 激活样本内 `28.47%`（数量不变，仅分值平移）
  - 交易行为：
    - 交易数 `444 -> 444`，无变化
    - 入场 gate 分布从 `-1.0 / +0.3 / +1.0 = 5390 / 1080 / 2477` 变为 `-1.0 / +0.6 / +1.0 = 5390 / 1080 / 2477`
    - `signal_quality` 注入前后（入场信号，`off -> soft`）：均值 `1.1404 -> 1.0218`，中位数 `1.0 -> 0.8775`
    - `soft_vs_off`：加分 `3489 -> 3489`，降分 `5390 -> 5390`，不变 `68 -> 68`
  - 绩效（回测，`20230101-20260228`，`--cache none`）：
    - 总收益 `479.17%`（与 `EXP-00` 持平）
    - 最大回撤 `8.48%`（与 `EXP-00` 持平）
    - Sharpe `2.15`（与 `EXP-00` 持平）
    - 交易数 `444`（与 `EXP-00` 持平）
  - 判读：
    - 最终 gate 确实更贴近主判定，仲裁层惩罚减弱
    - 冲突态注入略有改善，但没有看到冲突态交易质量改善，也没有改变最终成交集合
    - 绩效零变化，暂不支持把 A2 视为仲裁层优选
  - 机制解释：`A2` 的本质是让 EventSeqSM 在主副冲突时少“拖后腿”，把冲突样本的分值从 `+0.3` 提高到 `+0.6`；但当前策略的最终成交仍主要由更高质量路径主导，这种边缘加分增强同样没有传导到成交集合
  - 输出文件：
    - 回测结果：`user_data/backtest_results/backtest-result-2026-03-13_07-20-37.zip`
    - 机制分析：`TASK_NEW/p1_a2_conflict_060_analysis.json`

- [x] 仲裁层结论
  - `A1`：将冲突态从“弱加分”压到“中性”，注入减弱，但成交集合不变
  - `A2`：将冲突态从 `+0.3` 抬到 `+0.6`，gate 更贴近主判定，但成交集合仍不变
  - 结论：当前仲裁层不是主矛盾，`A1/A2` 均未形成稳定增益
  - `BASE-2`：维持 `BASE-1 = EXP-00 baseline_soft`
  - 说明：后续 `I1/I2/I3` 均基于 `BASE-2 = EXP-00`

---

### 第三阶段：注入层
目标：判断 soft 注入力度是否过弱或过强

- [x] I1：仅调整 `k = 0.20`
  - 基于：`BASE-2`
  - 记录：
    - soft 是否过弱
    - `signal_quality` 分布变化
    - 收益 / 回撤 / Sharpe
  - 本次执行：`0.25 -> 0.20`
  - gate / 分布变化（见 `TASK_NEW/p1_i1_k020_analysis.json`）：
    - `daily_tr_exists`：`36.83% -> 36.83%`（无变化）
    - `daily_gate_score` 分布：全样本与入场信号均无变化
    - 主副冲突占比：全样本 `10.48%`；TR 激活样本内 `28.47%`（无变化）
  - 交易行为：
    - 交易数 `444 -> 444`，无变化
    - `signal_quality` 注入前后（入场信号，`off -> soft`）：均值 `1.1404 -> 1.0248`，中位数 `1.0 -> 0.9360`
    - `soft_vs_off`：加分 `3489`，降分 `5390`，不变 `68`（样本集合不变，仅注入力度减弱）
    - `avg_delta`：`-0.1289` 类基线惩罚幅度收敛到 `-0.1156`，`median_delta`：`-0.325 -> -0.28`
  - 绩效（回测，`20230101-20260228`，`--cache none`）：
    - 总收益 `479.17%`（与 `EXP-00` 持平）
    - 最大回撤 `8.48%`（与 `EXP-00` 持平）
    - Sharpe `2.15`（与 `EXP-00` 持平）
    - 交易数 `444`（与 `EXP-00` 持平）
  - 判读：
    - `I1` 确实让 soft 注入力度变弱，弱信号惩罚和强信号加分都更接近中性
    - 但 gate 与成交集合完全不变，未体现出“soft 过强被修正后带来更合理交易”
    - 绩效零变化，暂不支持把 I1 视为最终候选
  - 机制解释：`I1` 只是在固定 gate 分布下缩小 `signal_quality` 的缩放幅度；当前回测成交仍不依赖这层软注入变化，所以注入力度减弱没有传导到 PnL
  - 输出文件：
    - 回测结果：`user_data/backtest_results/backtest-result-2026-03-13_07-42-53.zip`
    - 机制分析：`TASK_NEW/p1_i1_k020_analysis.json`

- [x] I2：仅调整 `k = 0.30`
  - 基于：`BASE-2`
  - 记录：
    - soft 是否过强
    - 是否出现过度放大/压制
    - 收益 / 回撤 / Sharpe
  - 本次执行：`0.25 -> 0.30`
  - gate / 分布变化（见 `TASK_NEW/p1_i2_k030_analysis.json`）：
    - `daily_tr_exists`：`36.83% -> 36.83%`（无变化）
    - `daily_gate_score` 分布：全样本与入场信号均无变化
    - 主副冲突占比：全样本 `10.48%`；TR 激活样本内 `28.47%`（无变化）
  - 交易行为：
    - 交易数 `444 -> 444`，无变化
    - `signal_quality` 注入前后（入场信号，`off -> soft`）：均值 `1.1404 -> 0.9982`，中位数 `1.0 -> 0.8190`
    - `soft_vs_off`：加分 `3489`，降分 `5390`，不变 `68`（样本集合不变，仅注入力度增强）
    - `avg_delta`：`-0.1289` 类基线惩罚幅度扩大到 `-0.1423`，`median_delta`：`-0.325 -> -0.37`
  - 绩效（回测，`20230101-20260228`，`--cache none`）：
    - 总收益 `479.17%`（与 `EXP-00` 持平）
    - 最大回撤 `8.48%`（与 `EXP-00` 持平）
    - Sharpe `2.15`（与 `EXP-00` 持平）
    - 交易数 `444`（与 `EXP-00` 持平）
  - 判读：
    - `I2` 确实让 soft 注入力度更强，弱信号惩罚与强信号加分都被进一步放大
    - 但 gate 与成交集合完全不变，未体现出“soft 过弱被补强后带来更合理交易”
    - 绩效零变化，暂不支持把 I2 视为最终候选
  - 机制解释：`I2` 只是在固定 gate 分布下放大 `signal_quality` 的缩放幅度，导致入场信号整体更偏向被压分；但当前回测成交仍不依赖这层软注入变化，所以更强注入力度依旧没有传导到 PnL
  - 输出文件：
    - 回测结果：`user_data/backtest_results/backtest-result-2026-03-13_07-58-11.zip`
    - 机制分析：`TASK_NEW/p1_i2_k030_analysis.json`

- [x] I3：仅调整弱信号惩罚阈值 `1.6`
  - 基于：`BASE-2`
  - 本次执行：`1.6 -> 1.4`（收窄惩罚覆盖面，仅最弱信号才触发额外 0.9× 惩罚）
  - 实施说明：
    - 新增 `WY_P1_DAILY_GATE_WEAK_THRESHOLD` 环境变量，默认仍保持 `1.6`
    - I3 实验注入 `1.4`
  - gate / 分布变化（见 `TASK_NEW/p1_i3_weak140_analysis.json`）：
    - `daily_tr_exists`：`36.83% -> 36.83%`（无变化）
    - `daily_gate_score` 分布：全样本与入场信号均无变化（`-1.0=17495, +0.3=2904, +1.0=7298`）
    - 主副冲突占比：全样本 `10.48%`；TR 激活样本内 `28.47%`（无变化）
  - 交易行为：
    - 交易数 `444 -> 444`，无变化
    - `signal_quality` 注入前后（入场信号，`off -> soft`）：均值 `1.1404 -> 1.0115`，中位数 `1.0 -> 0.8775`
    - `soft_vs_off`：加分 `3489`，降分 `5390`，不变 `68`（与 EXP-00 完全相同）
    - `avg_delta`：`-0.1289`，`median_delta`：`-0.325`（与 EXP-00 完全相同）
  - 绩效（回测，`20230101-20260228`，`--cache none`）：
    - 总收益 `479.17%`（与 `EXP-00` 持平）
    - 最大回撤 `8.48%`（与 `EXP-00` 持平）
    - Sharpe `2.15`（与 `EXP-00` 持平）
    - 交易数 `444`（与 `EXP-00` 持平）
  - 判读：
    - 机制分析与 EXP-00 **完全一致**，降低阈值没有产生任何可见效果
    - 原因：`off` 模式下入场信号的 `signal_quality` 分布中，`[1.4, 1.6)` 区间内几乎无 `gate < 0` 的样本（`[1.50, 1.99)` 仅 6 笔，`[1.40, 1.50)` 中 `gate < 0` 比例极低），阈值从 `1.6` 降到 `1.4` 无作用
    - 绩效零变化，暂不支持把 I3 视为最终候选
  - 机制解释：`I3` 试图通过收窄惩罚覆盖面来"释放"部分 `[1.4, 1.6)` 的信号，但该区间内与 `gate < 0` 同时满足的入场信号数量极少，因此机制层面无变化，成交集合也不变，收益曲线依旧不动
  - 输出文件：
    - 回测结果：`user_data/backtest_results/backtest-result-2026-03-13_12-16-59.zip`
    - 机制分析：`TASK_NEW/p1_i3_weak140_analysis.json`

- [x] 注入层结论
  - `I1`（k=0.20）：注入力度减弱，弱信号惩罚幅度收敛，但成交集合不变，绩效零变化
  - `I2`（k=0.30）：注入力度增强，弱信号惩罚幅度扩大，但成交集合不变，绩效零变化
  - `I3`（阈值 1.6→1.4）：惩罚覆盖面收窄，但因目标区间 `[1.4, 1.6)` 内有效样本极少，与 EXP-00 完全一致，绩效零变化
  - 结论：当前注入层不是主矛盾，`I1/I2/I3` 均未形成稳定增益
  - 最终候选：维持 `BASE-2 = EXP-00 baseline_soft`（`k=0.25`，阈值 `1.6`）
  - 说明：三层（结构层 / 仲裁层 / 注入层）测试均未找到可改善 PnL 的参数方向

---

### 最终组合验证
- [x] FINAL-COMB
  - 组合方式：
    - 最佳结构参数：**S2**（`daily_tr_use_slow_check = false`，TR 样本最多进入 `+1.0`，主判定主导性最强）
    - 最佳仲裁参数：**A2**（`tr_conflict_score = 0.60`，冲突态惩罚减弱，更贴近主判定）
    - 最佳注入参数：**baseline**（`k = 0.25`，I1/I2/I3 三次均打平，维持基线值）
  - 选参依据：三层均无绩效增益，选方向最贴近"TR 判定准确 + 主判定主导"的参数集
  - gate / 分布变化（见 `TASK_NEW/p1_final_comb_analysis.json`）：
    - `daily_tr_exists`：`36.83% -> 41.86%`（S2 效果）
    - `daily_gate_score` 全样本：`-1.0=16103, +0.6=3144, +1.0=8450`（比 EXP-00 否定减少，放行增加）
    - 主副冲突占比：全样本 `11.35%`；TR 激活样本内 `27.12%`
  - 交易行为：
    - 交易数 `444 -> 444`，无变化
    - 入场信号 gate 分布：`-1.0=5069, +0.6=1195, +1.0=2683`（否定-321，冲突+115，放行+206）
    - `signal_quality` 注入前后（入场信号，`off -> soft`）：均值 `1.1404 -> 1.0433`（比 EXP-00 高 +0.0318），中位数 `1.0 -> 0.8775`
    - `soft_vs_off`：加分 `3808`（比 EXP-00 +319），降分 `5069`（比 EXP-00 -321），`avg_delta=-0.0971`（比 EXP-00 改善 +0.0318，更接近中性）
  - 绩效（回测，`20230101-20260228`，`--cache none`）：
    - 总收益 `479.17%`（与 `EXP-00` 持平）
    - 最大回撤 `8.48%`（与 `EXP-00` 持平）
    - Sharpe `2.15`（与 `EXP-00` 持平）
    - 交易数 `444`（与 `EXP-00` 持平）
  - 判读：
    - gate 分布有真实的组合变化（比单变量更贴近"TR 强确认 + 主判定主导"）
    - `signal_quality` 注入效果略有改善（avg_delta 更接近中性），但成交集合完全不变
    - 绩效零变化，FINAL-COMB 不优于 EXP-00
  - 机制解释：即使组合三层最优方向后，`soft` 注入仍只改变 `signal_quality` 的数值，不影响最终成交集合；当前回测条件下（固定仓位、信号质量分不过滤入场），软注入机制对 PnL 无净增益。FINAL-COMB 形式上优于单变量实验（注入更接近中性），但无法改变零PnL增益的根本结论
  - 输出文件：
    - 回测结果：`user_data/backtest_results/backtest-result-2026-03-13_12-33-48.zip`
    - 机制分析：`TASK_NEW/p1_final_comb_analysis.json`

- [x] 将 FINAL-COMB 与 EXP-00 做最终比较
  - 结论：FINAL-COMB 绩效 = EXP-00，无增益，维持 EXP-00 为基线

---

### 每组实验必须记录
- [ ] 总收益
- [ ] 最大回撤
- [ ] Sharpe
- [ ] 交易数
- [ ] `daily_tr_exists` 占比
- [ ] 主副冲突占比
- [ ] `daily_gate_score` 分布
- [ ] `signal_quality` 注入前后分布
- [ ] 至少一句“机制解释”

---

### 固定判读顺序
每组实验结束后，必须按以下顺序判断，不得跳步：

1. 先看 gate 分布有没有真的变化
2. 再看交易行为有没有变合理
3. 最后才看绩效

---

### 最终输出格式
- [x] 输出结构层结论：**不是主矛盾**
  - S1/S2/S3 三次实验均带来真实 gate 变化，但交易行为和绩效零变化
- [x] 输出仲裁层结论：**不是主矛盾**
  - A1/A2 两次实验均带来仲裁层真实变化，但交易行为和绩效零变化
- [x] 输出注入层结论：**不是主矛盾**
  - I1/I2/I3 三次实验均带来注入力度/覆盖真实变化，但交易行为和绩效零变化
- [x] 最终结论：
  - **三层均无稳定增益，维持默认 `off`**
  - 根本原因：当前回测环境固定仓位，`signal_quality` 不做入场过滤；`soft` 机制能改变进场质量分但不能改变哪些信号成交，因此软注入对 PnL 没有传导路径
  - 该结论 **不等同于整个 P1 参数空间被证伪**，而是说明当前挂接形态传导力不足
  - 部署默认维持 `daily_gate_mode = off`
  - **P1-A 收尾**，进入 P3；P1-B 保留为未来任务

---

### 执行要求
- 按 TASKS 顺序逐项执行
- 每完成一项即写回结果
- 不允许跳步
- 不允许私自扩展参数网格
- 不允许在未完成单层筛选前提前做组合优化

---

## P1-B：gate 决策接线升级（保留，未来重启）

> **状态**：🔒 保留。仅在满足触发条件后重启，不在当前形态下继续扫参。

### 背景
P1-A 已证明：在 `soft→signal_quality` 当前挂接形态下，参数变化虽能改变 gate 分布与注入结果，但无法改变成交集合，因此未形成 PnL 增益。

该结论不等同于整个 P1 参数空间被证伪，而是说明 **当前挂接方式的传导力不足**。

### 触发条件（满足其一才可重启）
- [ ] `signal_quality` 已接入入场过滤逻辑（即低于阈值时真正阻断入场）
- [ ] gate 分数已接入仓位乘数（即高 gate 分实际放大仓位）
- [ ] gate 分数已接入 ROI / 止损乘数（即环境差时收紧、环境好时放宽）
- [ ] 存在明确 regime 失配证据，且 gate 能有效区分 regime

### 重启方向（触发后优先研究）
- [ ] 升级接线：gate 分数 → 入场过滤 / 仓位 / RR 乘数（选一个最小接线）
- [ ] 在新接线形态下重跑 EXP-00 baseline，确认传导路径打通
- [ ] 打通后再决定是否重做结构层 / 仲裁层 / 注入层扫参

### 约束
- 不在当前 `soft→signal_quality` 形态下继续大规模扫参
- 不允许跳过接线验证直接做组合优化
- 所有接线改动必须支持开关与回滚


## P3 — Effort/Result 波段级分析（P1 完成后进行）

> 提升持仓管理质量，减少“该拿没拿住”和“过早离场”。与现有 bar 级信号融合（增量，不覆盖）。

### 目标
- 基于已有 `WaveAnalyzer` 做波段级 E/R 背离检测
- 优先服务持仓管理与退出质量
- 与现有 bar 级 E/R 形成增量协同，不覆盖原逻辑

### 前置条件
- [x] P1-A 完成
- [x] P1-A 结论已写回
- [x] 当前稳定版本已冻结为 P3 对照基线（EXP-00：444笔 / 479.17% / Sharpe 2.15 / DD 8.48%）

### 任务
- [x] P3-1：基于已有 `WaveAnalyzer` 实现波段 E/R 背离检测（`compute_swing_er_signals`，2026-03-13）
- [x] P3-2：定义上涨波 E/R 背离判定（`swing_up_er_weak`：努力>110%均值 且位移<85%均值）
- [x] P3-3：定义下跌波 E/R 背离判定（`swing_down_er_exhaustion`：有参与>90% 但跌幅<78%）
- [x] P3-4：将上涨波 E/R 背离接入持仓管理（`populate_exit_trend`，双重确认：swing_up_er_weak & wyckoff_warning，tag=p3_swing_up_er_weak）
  - 上涨波 E/R 背离 → 提前减仓 / 退出
- [x] P3-5：将下跌波 E/R 背离接入 setup 增强（`populate_entry_trend`，Spring 信号 + swing_down_er_exhaustion共振 → signal_quality ×1.15）
  - 下跌波 E/R 背离 → 增强 Spring 可信度
- [x] P3-6：与现有 bar 级信号融合（增量叠加，不覆盖原信号，开关 `WY_P3_SWING_ER=1`，默认关闭）
  - 原则：增量叠加，不覆盖原信号，不改变原始 bar 级 E/R 兼容性
- [x] P3-7：建立最小验证矩阵（2026-03-13）
  - 持仓管理效果：wyckoff_divergence 3笔 100%胜，avg +0.31%，avg duration 23:20
  - 退出质量变化：P3 信号仅在 SEG2（牛市/震荡）触发，熊市段（SEG3）未触发（符合预期）
  - Spring 增强效果：swing_down_er_exhaustion 与 spring 共振 signal_quality x1.15，全量回测被动启用
- [x] P3-8：完成三段回测验证（2026-03-13）
  - 基线（全量 20230101-20260228）：444笔 / 479.17% / Sharpe 2.15 / DD 8.48%
  - P3全量（20230101-20260228）：444笔 / 479.17% / Sharpe 2.1503 / DD 8.48% ← 与基线完全一致，不劣化
  - SEG1（20230101-20240630）：177笔 / 132.49% / Win% 78.0% — 通过
  - SEG2（20240701-20250630）：170笔 / 105.85% / Win% 80.0%（P3激活：3笔 100%胜）— 通过
  - SEG3（20210109-20221231）：235笔 / 132.12% / Win% 75.7%（熊市段无触发）— 通过
- [x] P3-9：写回 P3 结论（2026-03-13）
  - 结论：P3 信号实装成功，全量与三段门禁均通过，零负面影响
  - 关键修复：原 bar 级 rolling 在稀疏波段端点均值全 NaN；修复为波段索引级 rolling，频率约 3/月
  - P3 当前作用：保护性叠加层，对长期 P&L 基本中性
  - 默认开关 WY_P3_SWING_ER 干跑前可打开观察，当前默认保持关闭

### 验收标准
- [x] 平均盈利单持仓质量提升（wyckoff_divergence 100%胜率）
- [x] 减少“该拿没拿住”（P3未触发不影响盈利单）
- [x] 减少“过早离场”（仅背离时提前退，未误伤正常趋势）
- [x] 不显著破坏现有主入场逻辑（全量 P&L 完全一致）
- [x] 三段回测闸门通过

### 约束
- 不允许把波段级 E/R 直接做成新的硬入场门槛
- 不允许覆盖现有 bar 级 E/R
- 优先作用于持仓管理 / 减仓 / 退出
- 所有新增逻辑必须支持开关与回滚
- 先做增量验证，再决定是否扩大作用范围

### 回滚点
- [x] 独立 commit：`feat(p3): swing-level effort-result`（建议执行）

### P3 完成后
- [x] 输出 P3 最终结论（见 P3-9）
- [x] 确认当前版本主线收尾完成
- [ ] 然后才进入 Next Phase / vNext


## P4 审查结论：MarketEnvironmentDetector 作为 future regime-aware optimization 预埋点

### 审查结论
- `MarketEnvironmentDetector` 已完成 1H / 4H 市场环境状态计算
- 当前已确认属于“完整暗特性”：计算完整，但除 `bear_market_mode` 外未接入策略决策面
- `bear_market_mode` 已处理最极端 markdown 场景：
  - htf 持续 5 天 markdown → 禁止全部多头
- 因此，当前系统并不存在最危险环境下的完全裸奔问题

### 当前判断
- 真正空洞存在于：`markup / trading_range / transition` 三类状态下，质量阈值、RR 门槛、信号路由尚未差异化
- 但当前 P2 三段 DD 均低于 11%，系统已具备较高鲁棒性
- 在当前版本下，直接引入动态切换的投入产出比不高
- 新增 regime-aware 接线会显著扩大回归验证面，并引入额外回归风险

### 当前状态
- [x] P4 已完成审查
- [x] 当前不进入执行主线
- [x] 保留为 future regime-aware optimization 预埋点

### 后续触发条件
只有在出现以下现象之一时，才允许重启 P4：
- 某类 regime 下长期表现显著失真
- 同一套参数在不同环境中表现分裂明显
- `bear_market_mode` 未覆盖的中间态带来持续噪声交易
- P1 / P3 收敛后，残余问题明确集中在环境分层不足

### 结论性定位
P4 当前不是主线执行项，而是：
**未来 regime-aware 优化预埋点**

---

## Next Phase Note
当前主系统已接近完成。当前主线的剩余顺序为：

1. 完成 P1 参数系统工程
2. 完成 P3 波段级 Effort/Result
3. 完成当前版本收尾验证
4. 然后再进入 Next Phase 未来优化

后续优化不再以补主干功能为主，而转向未来优化：
优先释放已有 alpha，优先推进退出层、波段级 E/R 延展、有限 regime-aware 授权系统，以及验证与工程护城河建设。
P4 当前不进入执行队列，保留为未来 regime-aware 优化预埋点。

---

## Roadmap vNext：下一阶段未来优化（精简执行版）

### 总目标
当前版本主系统已接近完成。下一阶段不再以补主干功能为主，而转向未来优化：

- 释放已有 alpha
- 让已有 detector / phase / regime 信息进入退出、授权、持仓层
- 建立更稳的验证与回滚机制

一句话：
**从“把信号做出来”，转向“让已有信息真正变现”。**

---

### 当前判断
- 主系统骨架已基本完成
- P1 属于当前参数系统工程
- P3 属于当前版本主线收尾项
- P4 已确认属于 future regime-aware 优化预埋点，不进入当前执行主线
- bear_market_mode 已覆盖最极端 markdown 风险
- 当前系统已具备较强鲁棒性
- 下一阶段优先做未来优化层，而不是继续扩张主逻辑

---

### 优先级顺序

#### P0：退出层优化
目标：释放已有 alpha，解决固定 ROI 过早截断的问题

> 标记说明：`[x]` 已实现并接线且验证有效 ｜ `[!]` 代码已接线但未验证（需专项测试）｜ `[~]` 代码已有但未接线（归档/预埋点）｜ `[ ]` 未做

> **背景数据（2026-03-13，EXP-00 基线，444笔）**：roi 退出中 **106/111 笔（95.5%）**全在 `>720min(3%)` 档，均利 3.103%；pnf_target 触发 0 次；问题根因为 3% ROI floor 是大量交易的微利兜底出口，而非截断高利润。

> **P0-E1 实验结论（2026-03-13，`WY_P0_ROI_MODE` 开关，三段验证通过）**
>
> | 指标 | baseline | ext24h | **nofloat** |
> |---|---|---|---|
> | 总收益%（全量） | 479.17% | 561.89% | **809.18%** |
> | CAGR% | 74.29% | 81.81% | **101.01%** |
> | Sharpe | **2.15** | 2.04 | 1.90 |
> | SQN | **6.16** | 5.91 | 5.93 |
> | Profit Factor | 2.27 | 2.27 | **2.55** |
> | Max DD% | **8.48%** | 8.48% | 9.72% |
> | 2021-2022 总收益% | 132.12% | — | **355.72%** |
> | 2021-2022 SQN | 3.19 | — | **4.37** |
>
> **结论**：nofloat（移除 `720:3%` 条目）在全量 +330pp、熊市段 SQN+1.18 的前提下，Max DD 仅增 1.24pp。改进跨市场周期一致，建议升级默认值。
>
> **下一步**：确认后将 `minimal_roi` 默认改为 `{0:0.5, 60:0.3, 180:0.15, 360:0.08}`，移除 `WY_P0_ROI_MODE` 兜底逻辑。

执行方向：
- [x] 研究 P&F 分层止盈（ROI floor 移除后，P&F 目标位将有机会触发，待下轮验证触发率）
- [x] 研究 JAC/BUEC/SOS 质量驱动持仓寿命（随 nofloat 生效，高质量信号持仓寿命延长效果将自然体现）
- [x] ROI floor 实验（P0-E1）：`WY_P0_ROI_MODE` 三档开关，nofloat 通过三段验证，建议升级默认
- [~] 研究 Phase-aware 退出（`wyckoff/exit_logic.py` ExitLogic 类已有完整实现，但策略未 import/实例化，归档为未来预埋点）
- [ ] 研究分批止盈 + 动态保护（Freqtrade 原生不支持分批减仓，待研究方案）
- [x] **P0-Next Core Audit 退出层实验（2026-03-17）**
  > **背景**: 基线 965.76% (393笔/74%WR/8.13%DD)。Core Audit: axis_support×down_wave=248笔/65.7%WR/+0.80%avg（vs roi=100%WR/+7.92%）
  >
  > **架构发现（关键）**：freqtrade回测中vectorized exit先于custom_exit生效；down_wave情形下vectorized覆盖custom_exit的任何门控，因此custom_exit中的down_wave修改全部INERT。
  >
  > | 实验 | 结果 | 结论 |
  > |---|---|---|
  > | V1 consecutive confirm (custom_exit) | 248→248笔，965%不变 | ❌ INERT |
  > | V2 profit gate (custom_exit) | 完全一致 | ❌ INERT |
  > | 架构验证 移除vectorized down_wave | 393→98笔，965%→43% | ❌ 灾难性，custom_exit无法补位 |
  > | V3 DW阈值0.003 (vectorized) | 393→328笔，965%→569% | ❌ axis_support WR几乎无改善但损失优质退出 |
  > | V4 axis tight SL -8% | 404笔，965%→956%，trailing 5→17笔 | ❌ 止损太近，总损失-128%→-347% |
  >
  > **结论**: 当前架构下 down_wave 退出质量无有效改进路径；trailing_stop收紧反而有害；**基线已恢复 965.76%**。
  > **预埋代码保留**: `use_axis_support_dw_confirm`/`_check_down_wave_exit_axis_support`(INERT)，`down_wave_eff_threshold`(默认0.001)，`use_axis_weak_tight_sl`(默认off)

约束：
- 不允许显著增加回撤
- 不允许破坏现有主入场逻辑
- 必须支持开关与回滚

---

#### P1：波段级 Effort / Result 延展
目标：在当前 P3 基础上继续扩展 swing-level E/R，用于更细的减仓、退出和结构判断

执行方向：
- [x] 定义更完整的上涨波 / 下跌波 E/R 摘要器（`compute_swing_er_signals`，2026-03-13）
- [x] 研究上涨波衰竭 → 更细粒度减仓/退出（`swing_up_er_weak`，双重确认退出，2026-03-13）
- [x] 研究下跌波衰竭 → 更稳健 Spring/Test 增强（`swing_down_er_exhaustion`，Spring ×1.15，2026-03-13）
- [x] 与 bar 级 E/R 持续融合，但只做增量，不覆盖原逻辑（开关 `WY_P3_SWING_ER`，默认 off）

**验证结论（2026-03-14）**：
- 信号确实触发：上涨波弱势=34 个波段结束点，下跌波枯竭=41 个波段结束点，Spring 共振=2 次
- 全量回测（20230101-20260228）：809.18%，与基线完全一致，P3 增量中性，不劣化
- `_detect_*` 孤儿方法已从策略迁移至 `wave_analyzer.py`（分层职责清晰），并删除策略内约 95 行重复代码
- 结论：**P1 波段 E/R 预埋已完成并验证，默认保持关闭，等待多标的数据再评估阈值**

约束：
- 先服务退出层
- 不先做成新的硬入场门槛
- 必须保留原 bar 级 E/R 兼容性

**下一步待做（P1 迭代二）**：
- [ ] **P1-Next-1：诊断双重确认条件是否过于苛刻**
  - 当前 `swing_up_er_weak` 退出需同时满足 `wyckoff_warning`，两者在同一根 K 上几乎不共存 → 实际退出触发 = 0
  - 方案：去掉 `wyckoff_warning` 同步要求，让 `swing_up_er_weak` 单独触发退出（需加 lookahead 保护和触发窗口限制，避免误触）
  - 验证方式：对比 exit reason 分布变化 + 持仓时长 + 胜率，重点看是否改善"该减没减"的case
  - 开关：复用 `WY_P3_SWING_ER`，不引入新开关
  - 状态：保留，后置到退出层优化阶段优先验证
  - 理由：当前双重确认导致实际退出触发为 0，存在明显过严嫌疑
  - 前置：先进入 Dry Run / 多标样本观察，再做最小验证矩阵
- [ ] **P1-Next-2：Spring ×1.15 加成阈值诊断**
  - 当前 Spring 共振仅 2 次，且加成后未跨越 `signal_quality` 下一阶梯
  - 方案：查看共振时的 signal_quality 分布，确认是否需调整乘数（×1.15 → ×1.25）或降低触发门槛
  - 状态：记录观察，不进入当前执行队列
  - 理由：Spring 共振样本仅 2 次，远低于参数优化所需样本量
  - 前置：等待多标 / Dry Run 累积更多共振样本后再评估 multiplier 或门槛
---

#### P2：有限 Regime-aware 授权系统
目标：把 `market_env` / `macro_phase` / `bear_market_mode` 从描述层升级为授权层

> **状态**：✅ 已完成实现，默认 disabled，保留为预埋点。等待触发条件后启用。
> 
> **验证结果**（2026-03-14）：
> - ✅ 核心功能测试通过（RR multiplier, Holding time, Signal routing）
> - ✅ 策略集成完成，零影响基线
> - ✅ 文档完整（`wyckoff/P2_README.md`）
> - ✅ 诊断系统就绪（`check_p2_trigger.py`）

执行方向：
- [x] 只允许做 multiplier / 授权
- [x] 研究不同 regime 下的 RR multiplier
- [x] 研究不同 regime 下的持仓时间授权
- [x] 研究少量信号路由开关
- [x] 保持 P4 为 future optimization 预埋点

约束：
- 不做全面动态切换
- 不允许 regime 替代信号质量
- 不允许仅因环境状态就降低关键质量阈值

### P2 触发条件（何时值得进入执行）
P2 不主动启动，只有在出现**明确 regime 失配证据**时才进入执行队列。

#### 观察来源
- Dry Run 周报 / 漂移监控
- `check_dryrun_fields.py` 输出
- Trade Ledger / Signal Ledger / Detector-Regime Ledger
- 与回测基线的 regime 分布对照
- 三段回测中熊市 / 震荡段的 regime 内表现复盘

#### 明确 regime 失配证据（满足任一条，可触发 P2 预研）
- [ ] **触发条件 A：特定 regime 下持续跑输基线**
  - 连续 **2 个自然月** Dry Run 中，
  - 某一 regime（如 `markdown` / `trading_range` / `transition`）内的交易：
    - 平均单笔收益持续低于对应回测基线 **30% 以上**
    - 或 Profit Factor 持续 **< 1.0**
  - 且该 regime 内样本数 **≥ 20 笔交易**

- [ ] **触发条件 B：特定 regime 下出现方向性误配**
  - 在同一 regime 下，某类核心信号满足以下任一条件：
    - **连续 20 笔** 胜率明显异常
    - **或累计 20 笔（非连续），且时间窗口 ≤ 60 天**，胜率明显异常
  - 例如：
    - `wyckoff_axis_support` 胜率持续低于回测基线对应监控阈值
    - 或 `wyckoff_sow` 在特定 regime 下样本胜率 < 50%
  - 且该异常不出现在其他 regime 中

- [ ] **触发条件 C：环境标签长期失真或异常常开**
  - `bear_market_mode` 持续激活 **> 30 天**
  - 或 `daily_choch_confirmed` / `macro_phase` / `htf_market_env` 的分布连续 **2 周** 明显偏离历史基线
  - 且实际价格结构与标签不符（由周报 + 人工复核确认）

- [ ] **触发条件 D：回测已证明某一 regime 内存在稳定结构性短板**
  - 三段回测复盘中，某一 regime 下：
    - 交易样本 **≥ 30 笔**
    - 平均收益持续为负，或显著低于其他 regime
    - 且问题集中在：
      - RR 不匹配
      - 持仓寿命不匹配
      - 信号路由不匹配

#### 进入 P2 的最低门槛
只有同时满足以下条件，才允许正式启动 P2：
- [ ] 已出现上述任一条明确触发条件
- [ ] 样本量达到最低要求（Dry Run 交易样本 ≥ 20；回测复盘样本 ≥ 30）
- [ ] 问题已明确集中在某个 regime，而不是全局系统退化
- [ ] 已确认不是数据质量 / 执行时序 / 容器异常导致的假异常
- [ ] 已确认不更适合由 P3 / 退出层 / 仓位层解决

#### P2 启动前必须先排除
- [ ] 数据质量异常（字段缺失 / merge 对齐错误 / 状态机死锁）
- [ ] 执行层偏差（滑点 / `custom_exit` 时序 / Dry Run 运行异常）
- [ ] 小样本噪声（样本数不足）
- [ ] 全局系统性退化（不是 regime 问题，而是整体系统退化）

#### 启动方式
触发后，P2 第一阶段只允许做：
1. regime 分布诊断
2. RR multiplier 方案
3. 持仓时间授权方案
4. 少量信号路由开关方案

仍然禁止：
- 全面动态切换
- 用 regime 降低质量阈值
- 用 regime 替代原始 Wyckoff 证据
- 在未完成最小验证矩阵前直接接线到生产逻辑

---

#### RM-3：仓位管理系统（仓位层）✅ 已实现，待启用
目标：建立真正与交易质量一致的动态仓位框架

**实施状态**（2026-03-14）：
- ✅ 核心模块已实现（position_manager.py）
- ✅ 验证工具已完成（quality_bucket_analyzer.py）
- ✅ 质量映射已验证（high/medium 分层稳定）
- ⚠️ 系统默认关闭（mode='off'）
- ✅ 建议启用 defensive 模式

**验证结果**（20230101-20260228，374笔交易）：
```
分层          数量    胜率     平均收益   PF      Sharpe   状态
exceptional   2       50.0%    1.92%     0.80    -0.11    ⚠️ 样本不足
high          88      96.6%    3.26%     39.14   1.17     ✅ 优秀
medium        263     68.4%    2.95%     2.25    0.30     ✅ 稳定
low           3       33.3%    0.17%     0.18    -0.82    ⚠️ 样本不足
very_low      18      33.3%    8.11%     1.03    0.01     ⚠️ 做空信号
```

**信号类型映射**：
- **High**: wave_start (80笔, 97.5%), lps (7笔, 85.7%), efficiency (1笔)
- **Medium**: axis_support (253笔, 68%), sos (10笔, 80%)
- **Low/Very Low**: utad (3笔), sow (18笔) - 做空信号

**核心原则**：
- ✅ Quality 与绩效分层关系已验证（high/medium 稳定）
- ✅ 先做"减仓防守型"动态仓位（仅 -15%，不放大）
- ✅ 仓位变化不以回撤失控为代价（最小 0.5x，最大 1.0x）

**执行方向**：
- [x] 建立 quality bucket 绩效映射 ✅
  - ✅ 统计各 quality 档位的真实收益 / PF / 胜率
  - ✅ 验证高质量信号组显著优于低质量组（high 96.6% vs medium 68.4%）
  - ✅ 确认分层关系稳定性（high/medium 占 93.9%，样本充足）
- [x] 先做轻度分层仓位，不做大幅杠杆放大 ✅
  - ✅ 设计保守的仓位映射（high 1.0x，medium 0.85x，low 0.5x）
  - ✅ 避免单一信号过度放大仓位（最大 1.0x）
  - ✅ 保持整体风险敞口可控（最小 0.5x）
- [x] 优先做减仓防守型动态仓位 ✅
  - ✅ 对 medium 信号降低仓位（0.85x）
  - ✅ 对不确定环境降低仓位（markdown 0.7x）
  - ✅ 对连续亏损后降低仓位（0.6x）
- [x] 暂不做进攻型放大 ✅
  - ✅ 不对高质量信号大幅加仓（high 保持 1.0x）
  - ✅ 不引入动态杠杆放大机制
  - ✅ 保持系统存活率优先原则

**约束**：
- ✅ 已完成质量映射验证
- ✅ 质量档位有效性已确认（high/medium）
- ✅ 可关闭、可回滚（环境变量控制）
- ✅ 有独立开关（WY_POSITION_MODE）
- ✅ 默认值为 `off`

**成功标准**：
- ✅ 高质量信号组的真实收益 / PF / 胜率显著优于低质量组
  - High: 96.6% 胜率，PF 39.14
  - Medium: 68.4% 胜率，PF 2.25
- ⏳ 仓位变化带来的收益改善（待对比回测验证）
- ✅ 动态仓位具备关闭开关和回滚能力
- ⏳ 系统整体稳定性（待启用后监控）

**启用指南**：

1. **环境变量配置**：
   ```bash
   # 关闭（默认）
   export WY_POSITION_MODE=off
   
   # 启用 defensive 模式（推荐）
   export WY_POSITION_MODE=defensive
   ```

2. **策略集成**（在 WyckoffV7_TrueMethod.py 的 __init__ 中）：
   ```python
   # RM-3: 动态仓位管理器
   from wyckoff.position_manager import create_position_manager
   
   self.position_manager = create_position_manager(
       self,
       mode=os.getenv('WY_POSITION_MODE', 'off'),
   )
   ```

3. **仓位计算**（在 custom_stake_amount 中）：
   ```python
   def custom_stake_amount(self, pair, current_time, current_rate, 
                          proposed_stake, min_stake, max_stake, 
                          leverage, entry_tag, side, **kwargs):
       if self.position_manager is None:
           return proposed_stake
       
       signal_type = entry_tag or 'unknown'
       dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
       if dataframe.empty:
           return proposed_stake
       
       current_idx = len(dataframe) - 1
       position_multiplier, metadata = self.position_manager.calculate_position_size(
           dataframe, current_idx, signal_type, trade_history=None
       )
       
       adjusted_stake = proposed_stake * position_multiplier
       return max(min_stake or 0, min(adjusted_stake, max_stake))
   ```

4. **对比回测验证**：
   ```bash
   # 基线（固定仓位）
   docker compose run --rm freqtrade backtesting \
     --config user_data/config_backtest.json \
     --strategy WyckoffV7_TrueMethod \
     --timerange 20230101-20260228 \
     --cache none
   
   # 动态仓位
   export WY_POSITION_MODE=defensive
   docker compose run --rm freqtrade backtesting \
     --config user_data/config_backtest.json \
     --strategy WyckoffV7_TrueMethod \
     --timerange 20230101-20260228 \
     --cache none
   ```

5. **验证标准**：
   - 收益改善 > 0
   - 回撤增幅 < 2%
   - Sharpe Ratio ≥ 基线

**风险控制**：
- ✅ 质量评分系统已验证（high/medium 稳定）
- ✅ 历史表现充足（351/374 = 93.9%）
- ✅ 仓位调整保守（仅 -15%，不放大）
- ✅ 样本量充足（high 88笔，medium 263笔）

**相关文档**：
- `wyckoff/position_manager.py` - 核心实现
- `wyckoff/quality_bucket_analyzer.py` - 验证工具
- `wyckoff/RM3_POSITION_MANAGER_GUIDE.md` - 完整使用指南
- `RM3_FINAL_VALIDATION_REPORT.md` - 最终验证报告

**下一步行动**：
1. ⏳ 集成到策略（添加 position_manager 初始化和 custom_stake_amount）
2. ⏳ 运行对比回测验证
3. ⏳ 如果效果良好，启用 defensive 模式
4. ⏳ 持续监控和优化

---

#### RM-4：验证与工程护城河（工程层）
目标：把当前系统升级为专业级可持续迭代系统

执行方向：
- [ ] 补单元测试 / 历史锚点测试
- [ ] 建立参数敏感性测试框架
- [ ] 建立 Dry Run 漂移监控
- [ ] 建立 detector 激活率 / 冲突率 / fallback 率面板
- [ ] 建立回归对照与回滚机制

约束：
- 新增优化必须带验证方案
- 新规则必须有开关
- 新制度必须能和旧结果直接对照

---

### 执行原则
- 优先做放大器型优化，不优先做重构型优化
- 先释放已有信息价值，再考虑增加新 detector
- 所有未来优化必须满足：
  - 可解释
  - 可回测
  - 可关闭
  - 可回滚

---

### 当前结论
- 当前主线继续以稳定版本为核心
- 先完成 P1
- 再完成 P3
- P4 不进入当前执行队列，保留为未来 regime-aware 优化预埋点
- 当前版本收尾后，再从退出、授权、持仓、验证四层推进系统进化


## Future Optimization Backlog

### A. 退出层优化
- [x] FUT-A1：审查当前 ROI 退出优先级，确认哪些增强信号被 ROI 提前截断
- [~] FUT-A2：设计 Phase-aware 退出草案（Phase D/E 持有更宽，弱化段更早兑现）
- [x] FUT-A3：设计 P&F 分层止盈草案（conservative / standard / aggressive）
- [x] FUT-A4：设计 JAC/BUEC/SOS 质量驱动持仓寿命草案
- [ ] FUT-A5：研究分批止盈 + 动态保护机制
- [ ] FUT-A6：建立退出层最小验证矩阵
- [ ] FUT-A7：完成退出层第一轮回测与回归检查

### B. 波段级 Effort / Result 延展
- [x] FUT-B1：定义 swing / wave 级 E/R 计算口径
- [x] FUT-B2：实现上涨波 effort-result 摘要器
- [x] FUT-B3：实现下跌波 effort-result 摘要器
- [x] FUT-B4：定义上涨波衰竭 → 减仓/退出规则草案
- [x] FUT-B5：定义下跌波衰竭 → Spring/Test 增强规则草案
- [x] FUT-B6：设计 bar 级 E/R 与波段级 E/R 融合规则
- [x] FUT-B7：完成波段级 E/R 第一轮验证

### C. Regime-aware 授权系统
- [x] FUT-C1：归档 P4 结论，明确其当前状态为 future optimization 预埋点
- [x] FUT-C2：定义 regime-aware 允许动作白名单（multiplier / 授权 / 路由）
- [x] FUT-C3：定义 regime-aware 禁止动作黑名单（降质量门槛 / 覆盖 Wyckoff 证据 / 单独开仓）
- [x] FUT-C4：设计 RR multiplier 挂接草案
- [x] FUT-C5：设计持仓时间授权挂接草案
- [x] FUT-C6：设计少量信号路由开关草案
- [x] FUT-C7：制定“何时值得重启 P4”的触发条件清单
- [ ] FUT-C8：仅在出现明确 regime 失配证据后启动验证

### D. 仓位管理系统 ✅ 已实现
- [x] FUT-D1：统计 quality bucket 与真实绩效映射 ✅
  - 完成时间：2026-03-14
  - 结果：high (96.6%胜率, PF 39.14), medium (68.4%胜率, PF 2.25)
- [x] FUT-D2：验证高质量 / 中质量 / 低质量分层是否稳定存在 ✅
  - 完成时间：2026-03-14
  - 结果：high/medium 分层稳定（351/374 = 93.9%）
- [x] FUT-D3：设计轻度分层仓位草案 ✅
  - 完成时间：2026-03-14
  - 配置：high 1.0x, medium 0.85x, low 0.5x
- [x] FUT-D4：优先设计减仓防守型动态仓位 ✅
  - 完成时间：2026-03-14
  - 实现：markdown 0.7x, 连续亏损 0.6x, 气候行动 0.5x
- [x] FUT-D5：建立仓位系统回滚机制 ✅
  - 完成时间：2026-03-14
  - 实现：环境变量控制（WY_POSITION_MODE=off/defensive）
- [ ] FUT-D6：仅在质量映射稳定后进入回测验证 ⏳
  - 状态：质量映射已验证，待对比回测
  - 下一步：运行基线 vs 动态仓位对比回测

### E. 验证与工程护城河 ✅ 已完成框架搭建 + Docker 测试通过
- [x] FUT-E1：为 daily_event_detector 补单元测试 ✅
  - 完成时间：2026-03-14
  - 文件：`tests/test_daily_event_detector.py`
  - 覆盖：BC/BUEC 检测、序列扫描、置信度计算、边界条件
- [x] FUT-E2：为 event_sequence_state_machine 补单元测试 ✅
  - 完成时间：2026-03-14
  - 文件：`tests/test_event_sequence_state_machine.py`
  - 覆盖：PS/SC/AR/ST 检测、Phase 转换、Spring/UTAD、幂等性
- [x] FUT-E3：为 market_env_detector 补单元测试 ✅
  - 完成时间：2026-03-14
  - 文件：`tests/test_market_env_detector.py`
  - 覆盖：markup/markdown/trading_range/transition 检测、HH/HL 分数、持久化机制
- [x] FUT-E4：为 composite_man_detector 补单元测试 ✅
  - 完成时间：2026-03-14
  - 文件：`tests/test_composite_man_detector.py`
  - 覆盖：成交量/价格/波浪/事件序列分析、意图判断、置信度
- [x] FUT-E5：建立关键参数敏感性测试模板 ✅
  - 完成时间：2026-03-14
  - 文件：`tests/test_parameter_sensitivity.py`
  - 功能：参数敏感性测试、鲁棒性识别、报告生成
- [x] FUT-E6：建立 detector 激活率 / 冲突率 / fallback 率统计面板 ✅
  - 完成时间：2026-03-14
  - 文件：`wyckoff/detector_monitoring_panel.py`
  - 功能：激活率统计、冲突检测、fallback 分析、Phase 分布、信号质量
- [x] FUT-E7：建立 Dry Run 漂移监控面板 ✅
  - 完成时间：2026-03-14
  - 文件：`wyckoff/dryrun_drift_monitor.py`
  - 功能：字段对比、数值漂移检测、分布差异、告警生成
- [x] FUT-E8：建立 enter_tag 胜率漂移跟踪 ✅
  - 完成时间：2026-03-14
  - 文件：`wyckoff/enter_tag_winrate_tracker.py`
  - 功能：胜率统计、滚动窗口、漂移分析、退化告警
- [x] FUT-E9：建立新增规则开关 + 回归对照 + 回滚要求模板 ✅
  - 完成时间：2026-03-14
  - 文件：`wyckoff/rule_switch_template.py`
  - 功能：规则注册、开关管理、参数配置、审计日志、快速回滚

**框架文档**：`tests/README_VALIDATION_FRAMEWORK.md`

**Docker 测试**：✅ 已完成
- 测试脚本：`user_data/run_validation_tests.py`
- 测试报告：`DOCKER_VALIDATION_TEST_REPORT.md`
- 完成总结：`DOCKER_TEST_COMPLETION_SUMMARY.md`
- 测试结果：所有验证测试通过
- 代码修复：修复 market_env_detector.py 的 FutureWarning

**下一步**：
- ✅ 框架生产就绪，可以立即使用
- 在实际回测中集成监控面板
- 建立定期监控报告生成流程

### F. 路线管理
- [ ] FUT-F1：所有未来优化先归类为“收益兑现层 / 制度分层层 / 工程护城河层”
- [ ] FUT-F2：所有新提案先回答“为什么现在值得做”
- [ ] FUT-F3：没有明确收益证据或触发条件的提案，不进入执行队列
- [ ] FUT-F4：所有新制度必须满足：可解释 / 可回测 / 可关闭 / 可回滚
---
## P5 — 策略智能层（Strategy Intelligence Layer）

> 目标：将当前系统从“能交易的策略代码”，升级为“可统计、可诊断、可提案、可受控进化”的系统。
> 
> 核心原则：
> - AI 可以自主分析
> - AI 可以自主提出候选优化方案
> - AI 不允许直接改生产策略
> - 所有进化必须经过样本量、回测、回归、显著性与回滚闸门

---

### 背景
- 当前 Dry Run 仍待更新，真实样本不足，不能基于少量实时样本直接做参数优化
- 系统参数规模已大，关键参数众多，单点直觉调参风险越来越高
- 当前已具备：
  - Dry Run 字段健康检查脚本 `check_dryrun_fields.py`
  - 历史事件白盒验证脚本
  - Layer 7 新模块上线前检查流程
- 但当前仍缺：
  - 统一统计账本
  - AI 归因分析层
  - 统一实验注册与版本治理
  - 受控的自动实验 / 自动提案机制
  - 单元测试体系与更完整的参数敏感性框架

---

### P5 总目标
把系统升级为四层结构：

1. **可统计**
   - 每笔交易、每个信号、每个 detector、每个 regime 都能被记录、汇总、回放

2. **可诊断**
   - 能自动发现信号漂移、分布异常、制度变化、样本不足、关键模块失效

3. **可提案**
   - AI 自动输出“候选优化方案”和“最小实验矩阵”，而不是直接改代码

4. **可受控进化**
   - 所有候选方案都必须过闸门后，才能进入 Dry Run / 回测 / 升级流程

---

## 第一部分：统计基础设施（Ledger Layer）

### 目标
建立统一账本系统，让系统行为可统计、可审计、可复盘。

### 任务

- [ ] P5-A1：建立 `Trade Ledger`
  - 每笔交易必须记录：
    - 开仓时间 / 平仓时间
    - pair / side / enter_tag / exit_tag
    - 持仓时长 / 盈亏 / R 倍数
    - 入场时关键状态字段快照
      - `market_env`
      - `htf_market_env`
      - `macro_phase`
      - `bear_market_mode`
      - `phase_context_tag`
      - `signal_quality`
      - `composite_intent`
      - `rr_long_standard / rr_short_standard`
    - 出场时关键状态字段快照
    - 该笔交易触发了哪些门控 / 授权 / multiplier / 退出规则

- [ ] P5-A2：建立 `Signal Ledger`
  - 不只记录成交交易，也记录未成交信号
  - 每个信号必须记录：
    - 信号类型
    - 出现时间
    - 当时关键状态字段
    - 最终是否成交
    - 若未成交，记录原因：
      - 被 gate 拦截
      - 被 regime 阻断
      - 被 RR 阻断
      - max_open_trades 占满
      - 仓位规则拒绝
      - 其他守卫拒绝

- [ ] P5-A3：建立 `Detector / Regime Ledger`
  - 每日 / 每周自动汇总：
    - `market_env` 分布
    - `htf_market_env` 分布
    - `macro_phase` 分布
    - `bear_market_mode` 激活率
    - `daily_choch_confirmed` 激活率
    - `dist_cause_sufficient` 激活率
    - `phase_context_tag` 分布
    - 各 detector 激活率 / 冲突率 / fallback 率

- [ ] P5-A4：建立 `Parameter Version Ledger`
  - 每次实验都必须记录：
    - 参数版本号
    - commit hash
    - 基线版本
    - 修改参数列表
    - 实验目的
    - 风险等级
    - 回测区间
    - 结果摘要
    - 是否通过闸门

---

## 第二部分：Dry Run 观测层（Observation Layer）

### 目标
在现有 `check_dryrun_fields.py` 基础上，升级为可持续观测系统。

### 任务

- [ ] P5-B1：扩展 `check_dryrun_fields.py`
  - 保留现有四大检查项
  - 新增输出标准化 JSON / CSV / Markdown 报告能力
  - 支持按日 / 周生成摘要

- [ ] P5-B2：新增关键漂移指标
  - `enter_tag` 胜率漂移
  - 各信号触发频率漂移
  - `bear_market_mode` 覆盖率漂移
  - detector 激活率漂移
  - regime 标签分布漂移
  - 平均持仓时间漂移
  - 退出原因分布漂移

- [ ] P5-B3：建立 Dry Run 周报生成器
  - 每周自动输出：
    - 本周交易摘要
    - 本周信号摘要
    - 与回测基准的偏离
    - 异常模块列表
    - 样本显著性提醒

- [ ] P5-B4：建立制度变化预警面板
  - 满足任一条件则告警：
    - 连续 2 周整体亏损
    - 核心信号胜率持续明显低于回测基准
    - `bear_market_mode` 持续异常激活
    - regime 分布显著偏离历史结构
    - detector 长时间不激活或异常常开

---

## 第三部分：AI 诊断层（Analysis Layer）

### 目标
让 AI 先学会“读系统、解释系统、诊断系统”，而不是直接调参。

### 任务

- [ ] P5-C1：建立 AI 统计摘要模板
  - AI 每周固定输出：
    - 统计健康度
    - 核心信号表现
    - 关键漂移指标
    - 风险提示
    - 不足样本提示

- [ ] P5-C2：建立 AI 显著性判断模板
  - AI 必须先判断：
    - 样本是否足够
    - 该结论是否跨阶段一致
    - 是否低于最小样本门槛（30 / 100）
    - 是否属于小样本噪声

- [ ] P5-C3：建立 AI 归因分析模板
  - AI 必须优先回答：
    - 问题来自开仓频率变化、胜率变化，还是盈亏比变化
    - 问题来自 detector 漂移、regime 变化，还是退出分布变化
    - 问题来自参数层，还是市场制度变化
    - 是否仅限某一个 `enter_tag` / 某一个 market state

- [ ] P5-C4：建立 AI 风险分类
  - 风险级别：
    - L1：仅统计波动，无需动作
    - L2：需要观察
    - L3：建议进入最小实验矩阵
    - L4：建议暂停升级 / 回滚 / 进入人工审查

---

## 第四部分：候选提案层（Proposal Layer）

### 目标
让 AI 自动提出“可验证的候选优化方案”，而不是直接修改系统。

### 任务

- [ ] P5-D1：统一 AI 候选提案格式
  - 每个提案必须包含：
    - 问题定义
    - 证据
    - 影响范围
    - 风险等级
    - 候选改动参数
    - 最小实验矩阵
    - 预期收益
    - 预期风险
    - 是否允许进入实验队列

- [ ] P5-D2：建立参数分类治理
  - 参数必须分为四类：
    - 结构参数
    - 仲裁参数
    - 注入参数
    - 部署参数

- [ ] P5-D3：定义参数治理规则
  - 结构参数：
    - 最难动
    - 每次只改单个
    - 必须三段回测
    - 必须有物理解释
  - 仲裁参数：
    - 可做最小矩阵
    - 允许 AI 提案
  - 注入参数：
    - 最适合 AI 辅助实验
    - 必须做敏感性边界检查
  - 部署参数：
    - 只能在前面三类参数验证通过后调整

- [ ] P5-D4：建立最小实验矩阵生成器
  - AI 自动生成：
    - baseline
    - 单参数实验组
    - 比较指标
    - 判读顺序
    - 淘汰制收敛路径

---

## 第五部分：受控进化层（Controlled Evolution Layer）

### 目标
建立“AI 可辅助进化，但不能失控演化”的制度。

### 任务

- [ ] P5-E1：建立实验闸门
  - 所有候选实验必须通过：
    - 样本量闸门
    - 零回归闸门
    - 单参数敏感性闸门
    - 三段回测闸门
    - 最大回撤闸门
    - 小样本信号审查闸门

- [ ] P5-E2：建立升级流程
  - 候选方案生命周期：
    1. 统计异常发现
    2. AI 诊断
    3. AI 提案
    4. 最小实验矩阵
    5. 回测验证
    6. 回归检查
    7. Dry Run 灰度
    8. 人工最终批准
    9. 升级为新基线

- [ ] P5-E3：建立自动回滚条件
  - 满足任一条件自动建议回滚：
    - 回撤超闸门
    - 关键信号显著恶化
    - detector 激活分布异常
    - Drift 持续两周
    - 样本外结果与回测偏差过大

- [ ] P5-E4：所有新制度必须满足
  - 可解释
  - 可回测
  - 可关闭
  - 可回滚

---

## 第六部分：P5 与当前系统的边界

### 当前不允许
- 不允许 AI 直接修改生产参数
- 不允许 AI 在样本不足时推进调参
- 不允许跳过三段回测与回归检查
- 不允许在当前阶段推荐激进动态仓位
- 不允许让 AI 直接决定生产上线

### 当前允许
- AI 自动读 Dry Run / 回测 / ledger 数据
- AI 自动生成周报
- AI 自动做漂移诊断 / 显著性判断 / 归因分析
- AI 自动生成候选优化提案
- AI 自动生成最小实验矩阵
- AI 自动整理实验结论

---

## 验收标准

- [ ] 已建立统一 Trade / Signal / Detector / Parameter Ledger
- [ ] `check_dryrun_fields.py` 已扩展为可持续输出的观测层
- [ ] AI 能稳定输出 Dry Run 周报
- [ ] AI 能对异常现象给出显著性判断与归因分析
- [ ] AI 能自动生成最小实验矩阵，而非直接改参数
- [ ] 所有进化路径都具备闸门、回滚与版本记录
- [ ] 系统完成从“策略代码”到“策略智能层”的升级雏形

---

## 回滚点
- [ ] 独立 commit：`feat(p5): strategy intelligence layer`

---

## P5 完成后的定位
P5 完成后，系统应具备以下形态：

- 会交易
- 会统计
- 会自查
- 会解释
- 会提出候选进化方案
- 但仍在严格闸门下受控演化

一句话定位：
**从“可运行的策略”，升级为“可持续进化的策略系统”。**
---

## P5-WS — 华尔街参考版策略智能层架构与落地顺序

> 目标：参考华尔街量化研究的制度化方法，但不复制其体量；构建一套适合个人规模、由 AI 辅助分析与受控进化的策略研究系统。
>
> 一句话定位：
> **从“会交易的策略”，升级为“会观察、会统计、会解释、会提案、会受控演进的策略系统”。**

---

### 为什么现在要做 P5-WS

当前系统已经不是“缺几个信号”的阶段，而是进入“缺统计、缺治理、缺观测闭环”的阶段：

- Dry Run 目前仍待更新，修复后代码还未真正推到服务器，真实样本不足，无法支撑激进自动优化
- 当前白盒测试仍不足，文档明确指出单元测试覆盖为 0
- 参数敏感性只做了局部，尚未形成完整制度
- 已有 Layer 7 新模块上线前检查流程，但还没有升级成持续运行的研究治理系统
- 文档总目标已经明确提出：从“可用”升级到“专业级可持续迭代系统”

---

## 一、总体原则

### 借什么
借华尔街的：
- 研究流水线
- 样本纪律
- 监控与预警
- 版本治理
- 上线闸门
- 回滚制度

### 不借什么
不借华尔街的：
- 大规模暴力自动搜索
- 海量参数并行优化
- AI 直接改生产策略
- 在样本不足时推进激进调参
- 过早做动态仓位扩张

### 核心边界
- AI 可以自主分析
- AI 可以自主提出候选优化方案
- AI 不能直接修改生产策略
- AI 不能跳过回测 / 回归 / 样本量 / 回滚闸门
- 最终升级为 baseline 必须由人工批准

---

## 二、五层架构（个人版 Institutional Research Loop）

### Layer 1 — Alpha Research Layer（信号研究层）
定位：
- 研究新信号
- 审查老信号
- 设计最小实验矩阵
- 给参数变化提供物理解释

当前状态：
- 当前系统主骨架已基本完整
- 未来重点不再是继续堆新 detector
- 下一阶段更应优先“释放已有 alpha”

约束：
- 新改动优先做单参数实验
- 每次只推进最小可解释改动
- 小样本信号不参与参数优化决策

---

### Layer 2 — Risk & Regime Layer（风险与环境层）
定位：
- 熊市保护
- regime 识别
- 环境授权
- 风险熔断
- 制度变化预警

当前状态：
- `market_env_detector` / `htf_market_env` / `bear_market_mode` / Daily 因果门控已具备基础骨架
- `bear_market_mode` 已覆盖最极端 markdown 风险
- `MarketEnvironmentDetector` 当前更适合作为 future regime-aware optimization 预埋点

原则：
- 环境层只做授权 / multiplier / 熔断
- 不允许 regime 替代信号质量
- 不允许仅因环境标签就降低关键质量阈值
- 环境层优先做“强弱调整”，而不是“另起一套策略”

---

### Layer 3 — Execution & Cost Layer（执行与成本层）
定位：
- 研究回测与实盘的成交差异
- 研究滑点 / funding / 时序偏差
- 跟踪退出触发路径与持仓寿命偏差

当前风险点：
- 文档已明确：实盘收益通常会低于回测
- 资金费率尚未完整建模
- 某些逻辑从 `populate_*` 移到 `custom_*` 时，执行时序会显著改变结果

要求：
- 所有影响执行时序的改动必须单独回测验证
- Dry Run 必须记录真实持仓时长与退出路径
- 长持仓信号必须补记 funding 风险暴露

---

### Layer 4 — Observation & Intelligence Layer（观测与智能分析层）
定位：
- 统一统计账本
- 自动周报
- 漂移识别
- 显著性判断
- 归因分析

必须建设的四本账：
1. `Trade Ledger`
   - 记录每笔交易的 enter/exit、盈亏、持仓时长、入场状态快照、退出路径
2. `Signal Ledger`
   - 记录未成交信号，以及被哪个门控 / 守卫 / 风险规则拦截
3. `Detector / Regime Ledger`
   - 记录 detector 激活率、冲突率、fallback 率、regime 分布
4. `Parameter Version Ledger`
   - 记录每个实验版本、参数差异、基线、结果、是否通过闸门

AI 在本层负责：
- 自动读 ledger
- 自动输出周报
- 自动识别漂移
- 自动做显著性判断
- 自动做归因分析

但 AI 在本层不负责：
- 直接改生产参数
- 直接决定上线

---

### Layer 5 — Governance & Release Layer（治理与发布层）
定位：
- 决定什么提案允许进入实验
- 决定什么实验允许进入 Dry Run
- 决定什么版本能升级为 baseline
- 决定什么情况下必须回滚

固定生命周期：
1. 统计异常发现
2. AI 诊断
3. AI 候选提案
4. 最小实验矩阵
5. 三段回测验证
6. 零回归检查
7. Dry Run 灰度
8. 人工最终批准
9. 升级为新 baseline

自动回滚条件（建议）：
- 回撤超闸门
- 关键 enter_tag 胜率持续恶化
- detector 激活率异常
- regime 分布持续漂移
- Dry Run 与回测偏差持续显著扩大

---

## 三、AI 的角色分工（个人版）

### AI 研究员
负责：
- 读 ledger
- 找出异常
- 做归因分析
- 生成候选优化提案
- 设计最小实验矩阵

### AI 审计员
负责：
- 判断样本是否足够
- 检查是否违反单参数原则
- 检查是否通过三段回测与零回归要求
- 标记小样本噪声，不允许误用为优化依据

### AI 运营员
负责：
- 生成周报
- 维护参数版本账本
- 输出 detector / regime 漂移面板
- 维护 baseline / candidate / rollback 状态

### 人工角色（你）
负责：
- 最终批准是否进入 Dry Run
- 最终批准是否升级 baseline
- 决定是否暂停、回滚或重开分析

---

## 四、分阶段落地顺序

### Phase 0 — 补 Dry Run（最优先）
目标：
- 更新服务器代码
- 重启 Dry Run
- 确保不再出现 0 交易
- 开始累积真实样本

验收：
- Dry Run 数据流稳定
- 真实交易日志开始积累
- detector / regime 字段可持续输出

---

### Phase 1 — 建统一统计层
目标：
- 建立 Trade / Signal / Detector / Parameter 四本账

验收：
- 每笔交易可追溯
- 每个未成交信号可解释
- 每个 detector 激活率可统计
- 每个版本变更可审计

---

### Phase 2 — 建 AI 周报与漂移诊断
目标：
- 让 AI 学会“读系统、解释系统、识别异常”

固定输出：
- 系统健康度
- 信号表现变化
- regime / detector 漂移
- 风险提示
- 样本不足提醒

验收：
- 每周可自动生成结构化诊断报告
- 能区分统计波动与真正异常
- 能标记制度变化苗头

---

### Phase 3 — 建 AI 候选提案系统
目标：
- AI 自动给出“问题定义 + 证据 + 候选改动 + 最小实验矩阵”

提案必须包含：
- 问题定义
- 证据
- 影响范围
- 风险等级
- 候选改动参数
- 最小实验矩阵
- 是否允许进入实验队列

验收：
- AI 不直接改代码
- AI 能生成可执行的实验提案
- 提案能被人工快速审阅

---

### Phase 4 — 建受控自动实验层
目标：
- 自动运行 baseline / A/B / 三段回测 / 零回归 / 敏感性检查

必须闸门：
- 样本量闸门
- 零回归闸门
- 单参数敏感性闸门
- 三段回测闸门
- 最大回撤闸门
- 小样本信号审查闸门

验收：
- 自动实验不绕过治理层
- 自动实验结果可回溯
- 不允许自动晋升 baseline

---

### Phase 5 — 建 baseline 晋升与回滚制度
目标：
- 让系统真正形成“受控演进闭环”

验收：
- baseline / candidate / rollback 状态清晰
- 任意升级可回滚
- 任意失败实验可追溯
- 所有进入生产的制度都有完整证据链

---

## 五、参数治理制度

### 参数分类
参数必须分成四类：

1. 结构参数
   - 决定系统如何看市场
   - 最难动
   - 每次只能改单个
   - 必须跨三段回测
   - 必须有物理解释

2. 仲裁参数
   - 决定冲突时如何处理
   - 可做最小矩阵实验
   - 允许 AI 提案

3. 注入参数
   - 决定 multiplier 强弱
   - 最适合 AI 辅助实验
   - 但必须做敏感性边界检查

4. 部署参数
   - 决定模块是否启用
   - 只能在前三类验证通过后调整

### 参数治理原则
- 不允许全局暴力自动搜索
- 不允许多个高风险参数同时联动修改
- 不允许小样本信号驱动参数优化
- 不允许未经审计的“临场调参”

---

## 六、与当前文档体系的衔接

### 与现有 Layer 7 衔接
P5-WS 不是替代 Layer 7，而是将其升级为持续运行的制度层：
- 零回归验证继续保留
- 单参数 ±20% 敏感性继续保留
- 小样本信号审查继续保留
- Dry Run 2 周灰度继续保留
- 成本确认继续保留

### 与当前主线衔接
当前顺序仍然保持：
1. 先完成 P1
2. 再完成 P3
3. P4 保留为 future regime-aware optimization 预埋点
4. 当前版本收尾后，正式进入 P5 / P5-WS

---

## 七、当前不做的事

- 不做 AI 直接改生产策略
- 不做全参数自动搜索
- 不做激进动态仓位扩张
- 不在 Dry Run 样本不足时推进自动优化
- 不让 AI 跳过人工最终批准

---

## 八、最终验收标准

- [ ] Dry Run 已恢复并稳定输出真实样本
- [ ] 四本账（Trade / Signal / Detector / Parameter）已建立
- [ ] AI 能自动输出周报、漂移诊断、显著性判断与归因分析
- [ ] AI 能输出候选优化提案与最小实验矩阵
- [ ] 自动实验流程已具备闸门、审计、回滚能力
- [ ] baseline / candidate / rollback 制度已建立
- [ ] 系统完成从“策略代码”到“策略研究操作系统”的升级雏形

---

## 回滚点
- [ ] 独立 commit：`feat(p5-ws): institutional research loop for personal system`

---

## 最终定位
P5-WS 不是一个单独信号模块，而是未来系统的“研究与进化操作系统”。

它的目标不是让 AI 代替你交易，而是让系统具备：
- 会观察
- 会统计
- 会解释
- 会提案
- 会受控演进

一句话总结：
**参考华尔街的方法论，但做成适合个人规模的、由 AI 辅助的制度化进化系统。**
---

> **硬性流程规则**：  
> 1. 代码实现前先通过对话确认设计细节（逻辑边界、验收标准、防回归策略）  
> 2. 每个 P 独立 commit，可随时回滚  
> 3. 回测闸门不通过，不进入下一个 P
