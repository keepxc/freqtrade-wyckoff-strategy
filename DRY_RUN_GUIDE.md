# Dry Run 实盘模拟指南

生成时间：2026-01-29  
策略版本：WyckoffV7_TrueMethod (Phase 1 修复中)

---

## ✅ 策略状态检查

### 回测结果验证（2023-2025）

**整体表现**：
- ✅ 总收益: +293.39% (3年)
- ✅ 胜率: 76.1% (223胜/70负)
- ✅ 夏普比率: 1.77
- ✅ 最大回撤: 6.48%
- ✅ CAGR: 58.46%

**信号质量**：
- ✅ wyckoff_wave_start: 102笔，95.1%胜率
- ✅ wyckoff_sos: 172笔，64.0%胜率
- ✅ wyckoff_effort_result: 7笔，100%胜率
- ⚠️ wyckoff_axis_support: 2笔，100%胜率（待优化）

**结论**：✅ **策略可以安全启动 Dry Run**

---

## 🚀 启动 Dry Run

### 方法 1: Docker Compose（推荐）

```bash
# 启动 Dry Run
docker-compose up -d

# 查看日志
docker-compose logs -f --tail=100

# 停止
docker-compose down
```

### 方法 2: Docker Run

```bash
# 启动 Dry Run
docker-compose run -d --name freqtrade_dryrun freqtrade trade \
  --config user_data/config.json \
  --strategy WyckoffV7_TrueMethod

# 查看日志
docker logs -f freqtrade_dryrun

# 停止
docker stop freqtrade_dryrun
docker rm freqtrade_dryrun
```

---

## 📊 监控和管理

### 1. Web UI 访问

打开浏览器访问：
```
http://localhost:8080
```

**登录信息**：
- 用户名: `ipconfig.log@gmail.com`
- 密码: `Linus*0987`

### 2. 实时日志监控

```bash
# 查看最新100行日志
docker-compose logs -f --tail=100

# 只看策略相关日志
docker-compose logs -f | grep "WyckoffV7"

# 查看入场信号
docker-compose logs -f | grep "wyckoff_"
```

### 3. 性能监控

```bash
# 查看容器状态
docker-compose ps

# 查看资源使用
docker stats freqtrade
```

---

## 🔍 关键监控指标

### 每日检查清单

**交易信号**：
- [ ] 是否有新的入场信号？
- [ ] 信号类型分布是否合理？
- [ ] 是否有异常的连续亏损？

**系统健康**：
- [ ] 容器是否正常运行？
- [ ] API 是否可访问？
- [ ] 日志是否有错误？

**性能指标**：
- [ ] 当日盈亏如何？
- [ ] 持仓情况如何？
- [ ] 胜率是否保持在70%+？

---

## ⚠️ 当前已知问题

### 问题 1: support_strength 字段未生效

**状态**: 🔧 修复中  
**影响**: 轴线优化功能（V2信号）未启用  
**风险**: 低（不影响主要交易逻辑）

**表现**：
```
📊 AXIS优化 - 字段统计:
  support_strength:
    ⚠️  无有效数据
```

**解决方案**: 正在调试中，预计下次更新修复

### 问题 2: 轴线支撑信号较少

**状态**: ⚠️ 待优化  
**影响**: 只有2笔轴线支撑交易（应该更多）  
**风险**: 低（其他信号正常工作）

**优化计划**: Phase 2 三层优化

---

## 🔄 热更新策略

当优化完成后，可以热更新策略：

```bash
# 1. 停止当前运行
docker-compose down

# 2. 确认代码已更新
git pull  # 如果使用 git

# 3. 重新启动
docker-compose up -d

# 4. 查看日志确认
docker-compose logs -f --tail=50
```

---

## 📝 日志分析

### 正常运行的日志示例

```
2026-01-29 15:02:21 - WyckoffV7_TrueMethod - INFO - ✅ WaveAnalyzer initialized
2026-01-29 15:02:51 - WyckoffV7_TrueMethod - INFO - Phase 2 轴线系统: 活跃轴线 8 条
2026-01-29 15:03:12 - WyckoffV7_TrueMethod - INFO - Phase 2 测试分类: Normal=67, Absorption=2, Spring=11
```

### 需要关注的日志

**入场信号**：
```
Phase 2 入场信号: 轴线支撑=63, Spring恢复=6, 共振信号(Axis+SOS)=1
```

**信号过滤**：
```
Phase 2 逐层过滤:
  1.支撑存在: 9186
  2.+接近支撑: 1127 (过滤掉 87.7%)
  3.+Normal/Absorption: 63 (过滤掉 94.4%)
```

---

## 🎯 优化计划（并行进行）

### Phase 1: 修复 support_strength（进行中）

**目标**: 使 support_strength 字段正确赋值  
**预期**: V2信号数量 > 0

**进度**：
- [x] 代码修复完成
- [x] 添加调试日志
- [ ] 验证修复效果
- [ ] 完整回测验证

### Phase 2: 三层优化（待启动）

**目标**: 释放被过滤的55个信号  
**预期**: 信号释放率从13%提升到70%+

**计划**：
1. Layer 1: not_distribution 优化
2. Layer 2: trend_support 优化
3. Layer 3: 轴线强度分级

---

## 📞 故障排查

### 问题：容器无法启动

**检查**：
```bash
# 查看容器状态
docker-compose ps

# 查看错误日志
docker-compose logs
```

**常见原因**：
- 端口8080被占用
- 配置文件格式错误
- Docker服务未启动

### 问题：无法访问 Web UI

**检查**：
```bash
# 测试端口
curl http://localhost:8080/api/v1/ping

# 检查防火墙
netstat -an | findstr 8080
```

### 问题：策略不生成信号

**检查**：
```bash
# 查看策略日志
docker-compose logs -f | grep "Phase 2 入场信号"

# 检查数据更新
docker-compose logs -f | grep "Refreshing candle"
```

---

## 📈 预期表现

基于回测结果，Dry Run 预期表现：

**每日交易**：
- 平均交易数: 0.27笔/天
- 平均盈利: 27 USDT/天（基于10,000 USDT本金）

**每周交易**：
- 预期交易: 2-3笔/周
- 预期盈利: 150-200 USDT/周

**风险控制**：
- 最大回撤: < 10%
- 单笔最大亏损: < 12%（止损）
- 连续亏损: < 4笔

---

## ✅ 启动检查清单

在启动 Dry Run 前，确认：

- [ ] 回测结果满意（✅ 293.39%收益）
- [ ] 配置文件正确（✅ dry_run: true）
- [ ] Docker 服务运行正常
- [ ] 端口8080可用
- [ ] 网络连接正常（访问 Binance API）
- [ ] 日志目录可写
- [ ] 数据库文件存在

---

## 🎉 准备就绪！

**当前状态**: ✅ **可以启动 Dry Run**

**启动命令**：
```bash
docker-compose up -d
```

**监控命令**：
```bash
docker-compose logs -f --tail=100
```

**Web UI**：
```
http://localhost:8080
```

---

**祝交易顺利！** 🚀

同时我们会继续优化 support_strength 问题，完成后可以热更新策略。
