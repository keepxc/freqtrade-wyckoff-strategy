# Gbrain 进化 SPEC — 威科夫记忆系统 2.0

## 目标

将当前「用户触发写索引文件」的扁平模式，进化为「AI 自动捕获 + 知识图谱 + typed 关系」的记忆系统。

---

## 模块 1: Signal Detector（信号探测器）

**目标**：每条消息后台并行捕获想法，不阻塞主响应

### 实现方案

新建 `~/.hermes/skills/signal-detector/SKILL.md`，但需要适配 Hermes 架构：

```
~/.hermes/skills/signal-detector/
├── SKILL.md                          # 技能定义（YAML frontmatter + markdown）
└── references/
    └── capture.py                    # 捕获逻辑（写入 Obsidian vault）
```

**触发条件**：每条 inbound message 触发（不阻塞）

**捕获内容**：
1. **Original thinking** — 用户的观察、论点、框架、决策
2. **Entity mentions** — trade_X、OKX、market_env、策略名、文件名

**写入目标**（Obsidian vault）：
- `originals/{slug}.md` — 原始想法
- `concepts/{slug}.md` — 世界概念
- `ideas/{slug}.md` — 产品/策略想法
- `entities/{slug}.md` — 实体（trade、exchange、regime 等）

### 关键设计

- 捕获用户的**确切措辞**，不 paraphrase
- 每条事实带 `[^1]` 引用（来源：消息时间戳）
- 自动反向链接到提及的实体页
- Signal Detector 作为轻量 sub-agent 调度，不阻塞主响应

---

## 模块 2: Typed 关系提取（零 LLM）

**目标**：不调用 LLM，用正则提取实体 + 类型

### 实现方案

```python
# reference/extractors/entity_patterns.py

ENTITY_PATTERNS = {
    "trade": [
        r"trade[_\s]?(\d+)",
        r"持仓.*?(\d+)",
    ],
    "exchange": [
        r"(OKX|Binance|Bybit|Bitget)",
    ],
    "regime": [
        r"(markdown|markup|transition|trading_range|spring|sos)",
    ],
    "file": [
        r"(\w+\.py|\w+\.md|\w+\.json)",
    ],
    "command": [
        r"(curl|python|bash|grep|git)",
    ],
}
```

### 效果

- 从消息中提取 `trade_9` → 类型 `trade`，ID `9`
- 从消息中提取 `OKX` → 类型 `exchange`
- 写入实体页时自动建立关系边

---

## 模块 3: 知识图谱层

**目标**：将扁平索引文件升级为图结构

### 数据模型

```yaml
# entities/trade_9.md
---
type: trade
id: 9
status: open
open_date: 2026-04-19
open_price: 74778
current_pnl: 12.02%
mentions:
  - 2026-04-25: "trade_9，持仓6天" ← originals/2026-04-25-session.md
---

# concepts/trading-range.md
---
type: concept
related_entities:
  - trade_9 (mentioned_in: 2026-04-25)
  - market_env (mentioned_in: 2026-04-21)
related_originals:
  - 2026-04-21-phase-d-signal-zero
---

# originals/2026-04-26-gbrain-evolution.md
---
type: original
mentions:
  - trade_9 (entity)
  - OKX (entity)
  - market_env (entity)
  - gbrain (concept)
cross_links:
  - gbrain 知识图谱设计 → concepts/gbrain
  - Typed 关系提取 → concepts/entity-extraction
---
```

### 迁移计划

1. 保留 `SESSION_INDEX.md` 作为「人类可读索引」（不变）
2. 新增 `graph/` 目录存图结构
3. 写一个 `scripts/migrate_index.py` 将现有 SESSION_INDEX.md 解析并迁移到图结构

---

## 模块 4: Skill 系统（Fat Skills）

**目标**：将知识注入 skill，而非 runtime

### 设计原则

> **Thin harness, fat skills** — 智能在 skill 里，不在框架里

每个 freqtrade 相关 skill 包含：
- 完整领域知识（不仅仅是步骤）
- 决策树（if-then）
- 常见错误模式（pitfalls）
- 验证步骤（verification）

### 优先级

```
freqtrade-strategy-diagnostics/    # 已有，扩充 pitfalls
freqtrade-process-management/      # 已有，补充夜间优化
wyckoff-trading-knowledge/         # 已有，补充图结构关联
```

---

## 模块 5: 夜间优化

**目标**：定时任务自动修复引用、合并记忆

### Cron 设计

```bash
# 每天 03:00 AM 执行（错开 dry_run 高峰）
0 3 * * * hermes skill run nightly-maintenance
```

### 任务内容

1. **引用检查**：所有 `[^1]` 引用是否有效（文件存在）
2. **孤立节点**：有出链无入链的实体，建反向链接
3. **重复检测**：相同 slug 的 originals/concepts/ideas 合并
4. **Session 归档**：将超过 7 天的 `originals/` 合并为周报
5. **Graph 可视化**：生成 `graph/index.json` 供快速查询

---

## 文件结构（目标）

```
~/hermes/
├── skills/
│   ├── signal-detector/              # 新增
│   │   ├── SKILL.md
│   │   └── references/
│   │       ├── capture.py
│   │       └── extractors/
│   │           ├── entity_patterns.py
│   │           └── idea_detector.py
│   ├── nightly-maintenance/          # 新增
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── fix_broken_refs.py
│   │       ├── merge_duplicates.py
│   │       └── orphan_check.py
│   ├── freqtrade-strategy-diagnostics/
│   │   ├── SKILL.md                  # 扩充
│   │   └── references/
│   │       └── decision_tree.md      # 新增
│   └── wyckoff-trading-knowledge/
│       ├── SKILL.md                  # 扩充
│       └── references/
│           └── wyckoff_graph.md      # 新增（知识图谱）
└── vault/                            # Obsidian vault
    ├── originals/
    ├── concepts/
    ├── ideas/
    ├── entities/
    └── graph/
        └── index.json
```

---

## 实现顺序

- [x] **Signal Detector**（模块 1）— 核心感知层 ✅
- [x] **Typed 关系提取**（模块 2）— 依附模块 1 ✅
- [x] **Skill 扩充**（模块 4）— decision_tree.md + wyckoff_graph.md ✅
- [x] **知识图谱层**（模块 3）— entities(28) + concepts(7) + graph/index.json ✅
- [x] **夜间优化**（模块 5）— cron 挂载 ✅

### 模块 3 完成状态（2026-04-27）

**新增 entity 页**（regime×6, signal×7, market×2, 补全×3）：
- regime: markup, markdown, transition, spring, sos, sow, trading_range(补全)
- signal: sos_bar, sow_bar, down_wave_exit, sow_fast_exit, wave_start, phase_d_signal, axis_support(补全)
- market: btc, eth
- trade: trade_9(补全), exchange: okx(补全)

**新增 concept 页**（7 个）：entry-optimization, exit-optimization, rm3-position-manager, p5-intelligence, regime-routing, wyckoff-three-laws, book-analysis

**图谱统计**：originals:7 | concepts:7 | entities:28 | regime×9 | signal×7 | market×2

---

## 风险控制

- Signal Detector 是**后台并行**，主响应链路的 SLA 不受影响
- 写入权限只在 `originals/`、`concepts/`、`entities/`、`ideas/`，不碰策略代码
- 每个模块完成后在 dry_run 观测 1 周再继续下一个
- 所有新增 skill 先在 `~/.hermes/skills/` 调试，不影响生产