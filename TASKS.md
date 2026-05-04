# P5 Tasks & Evaluations

## 待评估：FinGPT 集成

**仓库：** https://github.com/AI4Finance-Foundation/FinGPT
**状态：** 待分析，暂不引入
**Stars：** 19.2k | 最近更新：2026-04-11

### 核心模块
- **FinGPT Sentiment v3** — 金融情绪分析，RTX 3090 可跑，LoRA 微调 <$300，效果优于 GPT-4
- **FinGPT-Forecaster** — 基于新闻预测周级走势（DOW30）
- **Adanos Forecaster** — 2026-04-11 刚合并，加了情绪信号

### 潜在用途
- 包装为 P5 detector，输出 `sentiment_shift` 事件
- 和现有 `phase_context_tag`、`daily_choch_confirmed` 交叉验证
- 作为 regime 切换的辅助信号

### 注意事项
- 主要针对美股训练，加密货币需额外微调
- Forecaster 是周级预测，与 1h/4h Wyckoff 策略时间框架不完全匹配
- 是"分析层"不是"执行层"，不直接生成交易信号

### 下一步
- [ ] 评估加密货币情绪数据源（Twitter/Reddit/新闻）
- [ ] 测试 FinGPT Sentiment 在 crypto 新闻上的效果
- [ ] 设计 detector 接口：输入→输出→与现有事件的关联规则
