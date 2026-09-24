# AI500 P0 —— pandas 多列 tuple 赋值 Bug 修复报告

**日期**：2026-09-24
**环境**：pandas 2.3.3 / numpy 2.4.2 / freqtrade 2026.1 / Python 3.13.3
**范围**：全项目 `user_data/strategies/` 下所有策略文件

---

## 1. 执行摘要

对全项目策略代码做了多列批量赋值（`.loc[mask, ['enter_long','enter_tag']] = (1, 'xxx')`）的搜索与修复，
统一改为逐列赋值（`.loc[mask, 'enter_long'] = 1` + `.loc[mask, 'enter_tag'] = 'xxx'`），
消除 pandas 2.x + numpy 2.x 下多列 tuple 赋值可能产生的 dtype/shape 隐患。

**结论**：33 处全部修复，15 个文件；语法检查、dtype 检查、策略加载 smoke test 全部通过。

---

## 2. 发现多少处

全项目共发现 **33 处** 多列批量赋值：

| 类型 | 数量 |
|---|---|
| `.loc[..., ['a','b']] = (1, 'xxx')`（tuple 赋值） | 31 |
| `.loc[..., ['a','b']] = [1, 'xxx']`（list 赋值） | 2 |

分布（按文件）：

| 文件 | 处数 |
|---|---|
| `AI500V1DryRun.py` | 2 |
| `AI500V1MacdExitDryRun.py` | 2 |
| `AI500V2FlowHold.py` | 1 |
| `AI500V3ScoreHold.py` | 1 |
| `BStocksDonchianV1.py` | 2 |
| `BStocksHybridV1.py` | 1 |
| `DonchianV1.py` | 2 |
| `DonchianV1AtrMaExit.py` | 3 |
| `DonchianV1AtrSizing.py` | 2 |
| `DonchianV1MaExitOnly.py` | 3 |
| `DonchianV1RSize.py` | 2 |
| `DonchianV1ScaleOutR.py` | 2 |
| `DonchianV2Ensemble.py` | 2 |
| `MacdUltimateV24.py` | 6 |
| `WyckoffV8.py` | 2 |
| **合计** | **33** |

---

## 3. 修改多少处 / 修改文件列表

**33 处全部修改，15 个文件**：

```
user_data/strategies/AI500V1DryRun.py
user_data/strategies/AI500V1MacdExitDryRun.py
user_data/strategies/AI500V2FlowHold.py
user_data/strategies/AI500V3ScoreHold.py
user_data/strategies/BStocksDonchianV1.py
user_data/strategies/BStocksHybridV1.py
user_data/strategies/DonchianV1.py
user_data/strategies/DonchianV1AtrMaExit.py
user_data/strategies/DonchianV1AtrSizing.py
user_data/strategies/DonchianV1MaExitOnly.py
user_data/strategies/DonchianV1RSize.py
user_data/strategies/DonchianV1ScaleOutR.py
user_data/strategies/DonchianV2Ensemble.py
user_data/strategies/MacdUltimateV24.py
user_data/strategies/WyckoffV8.py
```

---

## 4. 修改前后写法

### 4.1 标准模式（Donchian/AI500 系列，多行 mask）

**修改前**：
```python
dataframe.loc[
    dataframe['don_upper'].notna() & (dataframe['close'] > dataframe['don_upper']),
    ['enter_long', 'enter_tag']
] = (1, 'don_break')
```

**修改后**：
```python
entry_mask = dataframe['don_upper'].notna() & (dataframe['close'] > dataframe['don_upper'])
dataframe.loc[entry_mask, 'enter_long'] = 1
dataframe.loc[entry_mask, 'enter_tag'] = 'don_break'
```

### 4.2 单行模式（MacdUltimateV24）

**修改前**：
```python
dataframe.loc[dataframe['scr_super_ralli'], ['enter_long', 'enter_tag']] = (1, 'macd_super_ralli')
```

**修改后**：
```python
dataframe.loc[dataframe['scr_super_ralli'], 'enter_long'] = 1
dataframe.loc[dataframe['scr_super_ralli'], 'enter_tag'] = 'macd_super_ralli'
```

### 4.3 列表赋值模式（WyckoffV8）

**修改前**：
```python
dataframe.loc[is_down_wave, ['exit_long', 'exit_tag']] = [1, 'v8_down_wave']
```

**修改后**：
```python
dataframe.loc[is_down_wave, 'exit_long'] = 1
dataframe.loc[is_down_wave, 'exit_tag'] = 'v8_down_wave'
```

### 4.4 顺带修复的真实 bug（AI500V1DryRun exit）

**修改前**（标量 bool 做 `.loc` 行索引，会抛 `KeyError: cannot use a single bool to index into setitem`，exit 恒不生效）：
```python
dataframe.loc[
    confirmed,           # confirmed 是 Python bool
    ['exit_long', 'exit_tag']
] = (1, 'netflow_oi_exit')
```

**修改后**（与 `AI500V1MacdExitDryRun` 既有的正确写法对齐）：
```python
if confirmed:
    dataframe.loc[dataframe.index[-1], 'exit_long'] = 1
    dataframe.loc[dataframe.index[-1], 'exit_tag'] = 'netflow_oi_exit'
```

### 4.5 依赖前一次赋值的链式条件（DonchianV1AtrMaExit / DonchianV1MaExitOnly）

这两个文件的 `ma20_exit` 条件里含 `(dataframe['exit_long'] != 1)`，必须在前一处 `don_exit` 赋值**之后**重新取 mask，已按顺序正确处理：

```python
exit_mask = dataframe['don_exit'].notna() & (dataframe['close'] < dataframe['don_exit'])
dataframe.loc[exit_mask, 'exit_long'] = 1
dataframe.loc[exit_mask, 'exit_tag'] = 'don_exit'
sma_exit_mask = (dataframe['sma_exit'].notna() & (dataframe['close'] < dataframe['sma_exit'])
                 & (dataframe['exit_long'] != 1))
dataframe.loc[sma_exit_mask, 'exit_long'] = 1
dataframe.loc[sma_exit_mask, 'exit_tag'] = 'ma20_exit'
```

---

## 5. 测试结果

| 验证项 | 结果 |
|---|---|
| 全项目复扫多列赋值 | ✅ 策略文件 0 处残留（`= (1,'x')` 与 `loc[..., ['enter_long',...]]` 均清空） |
| `py_compile` 语法检查（15 文件） | ✅ exit 0 全通过 |
| dtype 检查（模拟 AI500V3 entry 场景） | ✅ `enter_long` int64、`enter_tag` object，值 `[1,1,0]` 正确 |
| `freqtrade list-strategies` 加载 smoke test | ✅ 21 个策略全 `OK`，无 Error/Traceback/WARN |
| `get_latest_candle` warning 是否消失 | ⚠️ **本环境无法验证**（见 §6） |
| skipped pair 是否减少/消失 | ⚠️ **本环境无法验证**（见 §6） |

---

## 6. 未验证项（如实声明，非"代码能跑就算完"）

任务书要求验证 `get_latest_candle warning` 消失与 `skipped pair` 减少，这两项需要**真实数据流的
dry-run/回测**才能观察。当前环境：

- 币安 API（fapi + spot）返回 **451 geo-fence**（直连与 10809 代理均拦）
- 本机 **7890 端口不存在**（那是 bot 机器端口）

因此无法在本机下载数据跑真实回测/dry-run。这两项留待有可用网络的 bot 环境验证，
**未声称已验证**。

---

## 7. 是否还有类似风险

- **已清理**：`user_data/strategies/` 下多列批量赋值（tuple/list 两种）全部清除。
- **未检查**：`user_data/strategies_orig_updater/`（历史备份目录）与根目录 `temp_310_strategy.py`
  等非活跃文件未纳入本次范围——这些不参与 dry-run/回测，风险不在此。
- **重要事实（防止误判）**：在 pandas 2.3.3 + numpy 2.4.2 的最小复现中，`.loc[mask, ['a','b']] = (1,'x')`
  **未复现出 void dtype**（赋值行为正常）；但 freqtrade 2026.1 框架自身已在 `advise_entry`/`advise_exit`
  内置 `# Initialize column to work around Pandas bug #56503` 的 workaround，证明该 bug 在框架层面真实存在且已知。
  故本次修复定性为**防御性消除隐患 + 修复确凿存在的标量 bool 索引 bug**，而非"复现了 void dtype 后的对症修复"。

---

## 8. 附：验证命令

```bash
# 语法检查
python -m py_compile user_data/strategies/{15个文件}.py

# 策略加载 smoke test
python -m freqtrade list-strategies --config user_data/config.json
```
