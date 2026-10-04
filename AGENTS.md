# AGENTS.md

> ⚠️ **本仓库已归档（2026-10-04）**——全部 freqtrade bot 已停：
> DonchianV1 09-22、AI500 dryrun 08-23、AI500 live 09-01、V3 09-25、BStocks 09-08、V8 07-09
> 之后均无新开仓。
>
> **当前在跑的系统不在这里** → 见 `~/.hermes/scripts/README.md`
> （币安跟单 / ETF 定投 / 币安美股手动）。

## 必读（按顺序）

1. `CLAUDE.md` — 方法论：前置门控 / 第一原则 / Lookahead 清单 / 三层终止校验 / 三档分级 / 抗过拟合工具箱
   （**策略无关，仍然有效**——做任何研究性工作前先读）
2. `RESEARCH_FRAMEWORK.md` — 研究方向怎么选、探索性 vs 确认性、什么时候该放弃
3. `TASK_NEW/quant_system_tasks.md` — 当前权威任务与结论记录

## 归档层（威科夫时代的规则，保留供追溯，**不要再当现行规则用**）

| 文件 | 状态 |
|---|---|
| `GOLDEN_RULES.md` | G001（`WY_DW_EFF_THRESHOLD`）、G005（V7.1 baseline）只对**已停用的威科夫策略**有意义；G003（硬编码路径）仍通用 |
| `DECISIONS.md` | D001/D002/D003 属威科夫项目；D004（P5 只用 dry_run）/D005（AI 不直接改生产逻辑）仍通用 |
| `ARCHITECTURE.md` | 描述 `WyckoffV7 → P5 → AI`，**架构已不反映现状** |
| `scripts/lint_golden_rules.py` | 机械校验，当前 exit 0；覆盖的是归档代码 |

## 判断"策略是否在跑"

**必须查 trades DB 的 `MAX(open_date)`**——不能凭「策略文件存在 / service 文件存在 /
config 文件存在」下结论（2026-10-04 犯过此错，被用户纠正）。

同理：日报类任务出现**超长持有小时数**（如 "持1390h"）时，先怀疑 **bot 已停、仓位冻结**，
而不是"数据滞后"。

## 🚨 涉及网络 / 代理 / 交易所连接 / 订单执行（OKX、币安、mihomo、unit env 配置）

**先读 `~/AGENTS.md`**（本机护栏：必读 skill 清单 + 有意设计 + 事故史），再决定如何修改——
2026-09-07 曾因未读既有设计向 systemd unit 注入 env proxy 造成多余变更。
