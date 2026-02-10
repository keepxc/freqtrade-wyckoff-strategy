# Freqtrade 威科夫交易策略 (Wyckoff V7 TrueMethod)

基于威科夫交易方法的量化交易策略，专注于 BTC/USDT 交易对。

## 📊 当前性能

- **总收益率**: 310.92%
- **胜率**: 75.3%
- **交易数**: 381 笔
- **最大回撤**: 8.07%
- **Sharpe 比率**: 1.57
- **回测期间**: 2023-01-09 至 2025-12-31

## 🎯 核心功能

### 1. Weis Wave 基础 (波段分析)
- 波段识别引擎
- 努力结果计算器
- 冲力缩短检测 (SOT)
- 波段质量分析

### 2. Axis Line System (轴线系统)
- 动态轴线识别
- 支撑/阻力测试分类 (Normal/Absorption)
- Spring 行动识别
- Ice 线识别

### 3. Spring 质量分级系统 ⭐ 最新
- **Spring #3**: 轻度跌破 + 极低量（最高质量）
- **Spring #2**: 中度跌破 + 适度量（中等质量）
- **Spring #1**: 深度跌破 + 极高量（低质量）

### 4. 信号系统
- `wyckoff_axis_support`: 轴线支撑入场 (71.4% 胜率)
- `wyckoff_sos`: 强势信号 (71.8% 胜率)
- `wyckoff_wave_start`: 波段启动 (93.9% 胜率)
- `wyckoff_lps`: 最后支撑点 (70.0% 胜率)
- `wyckoff_spring_3`: Spring #3 入场（开发中）

## 📁 项目结构

```
freqtrade/
├── user_data/
│   ├── strategies/
│   │   └── WyckoffV7_TrueMethod.py    # 主策略文件
│   └── config_backtest.json           # 回测配置
├── wyckoff/                           # 威科夫核心模块
│   ├── axis_system.py                 # 轴线系统
│   ├── spring_classifier.py           # Spring 分级器
│   ├── weis_wave_foundation.py        # Weis Wave 基础
│   └── ...
├── TASKS.md                           # 任务清单
├── docker-compose.yml                 # Docker 配置
└── README.md                          # 本文件
```

## 🚀 快速开始

### 1. 环境要求
- Docker & Docker Compose
- Python 3.9+
- Freqtrade

### 2. 回测
```bash
docker-compose run --rm freqtrade backtesting \
  --strategy WyckoffV7_TrueMethod \
  --timerange 20230101-20251231 \
  --config user_data/config_backtest.json
```

### 3. Dry Run (模拟交易)
```bash
docker-compose up -d
docker-compose logs -f
```

## 📈 开发进度

### ✅ 已完成
- [x] Weis Wave 基础系统
- [x] Axis Line 轴线系统
- [x] Pattern Recognition 模式识别
- [x] 基础信号系统
- [x] Spring 质量分级系统
- [x] 置信度计算修正

### ⏳ 进行中
- [ ] Spring 信号优化（当前任务）
- [ ] 轴线强度分级过滤
- [ ] SOS Bar 四大硬指标验证

### 📋 计划中
- [ ] 事件序列状态机 (PS→SC→AR→ST→Spring→SOS→LPS)
- [ ] Phase A-E 识别系统
- [ ] 显著K线识别系统
- [ ] 多时间框架协调
- [ ] 精细化退出策略

## 📚 理论基础

本策略基于威科夫交易方法的三大定律：

1. **供需定律**: 价格反映供需关系
2. **因果定律**: 横盘时间决定趋势幅度
3. **努力结果定律**: 成交量与价格运动和谐性

详细理论请参考：`威科夫交易方法完整学习指南.md`

## 🔧 Git 使用指南

### 初始化仓库
```powershell
.\git_init.ps1
```

### 快速提交
```powershell
.\git_commit.ps1 "提交信息"
```

### 常用命令
```bash
git status              # 查看状态
git add <file>          # 添加文件
git commit -m "msg"     # 提交
git log                 # 查看历史
git diff                # 查看更改
```

## 📊 最新优化

### 2026-02-09: 置信度计算修正
- 修正了 `_classify_axis_tests` 方法的置信度计算
- 置信度从固定 0.4 提升到动态 0.5-1.0 范围
- 利润保持 310.92%，胜率提升到 75.3%

### 2026-02-09: Spring 分级阈值优化
- Spring #3 数量从 2 个增加到 6 个
- Spring #2 数量从 4 个增加到 8 个
- 完全移除 Spring #3 的 EMA 限制

## 📝 文档

- `TASKS.md`: 完整的任务清单和开发计划
- `AXIS_TESTS_CONFIDENCE_FIX_REPORT.md`: 置信度修正报告
- `SPRING_OPTIMIZATION_REPORT.md`: Spring 优化报告
- `威科夫交易方法完整学习指南.md`: 威科夫理论详解

## ⚠️ 风险提示

本策略仅供学习和研究使用，不构成投资建议。加密货币交易存在高风险，请谨慎使用。

## 📄 许可证

MIT License

## 👤 作者

Kiro AI Assistant & User

---

**最后更新**: 2026-02-09  
**版本**: v7.0  
**状态**: 🟢 稳定运行
