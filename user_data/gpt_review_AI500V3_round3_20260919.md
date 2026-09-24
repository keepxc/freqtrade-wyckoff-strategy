先给结论：

这轮证据把问题重新分成了两层：

工程层面：D（void dtype）现在是最高优先级的真实生产缺陷，应该先修。

策略层面：最大的未知量仍然是 AI500 score 是否有增量预测力，而 signal age 是解释/分层这个问题的关键协变量。

A 类修复目前只能说“已证明对 AIN 这笔历史错误有效”，不能说“已经证明出场规则有效”。

S−H 的 +85.85pp 极其醒目，但它主要证明了“旧实现的出场机制有严重缺陷”，不是证明 AI500 有 alpha，更不是证明新的 4h 阴线出场本身具有 +85pp 的普遍价值。

1. 之前 5 条：哪些撤回、哪些坚持、哪些改措辞
原判断	现在结论	修正后的准确表述
① use_custom_exit 必须显式打开	撤回	Freqtrade 2026.2 中不存在这个配置；本策略 use_exit_signal=True，而 custom_exit() 位于 should_exit() 的相应调用路径，当前无需加不存在的配置
② dataframe 末行可能是未收盘 K	部分撤回 / 改措辞	Binance 正常路径下 drop_incomplete=True 会丢掉未完成 K，因此当前末行确实是已收盘 K；但 len(ticks)==1 时源码存在不丢弃的边界，所以你加的守卫仍然合理
③ 同一根阴线会重复触发	坚持，但严重程度下调	custom_exit() 可以在多个 throttle 周期看到同一根已收盘阴 K；但一旦第一次执行成功，trade 已退出，因此通常不是交易结果级 bug，而是重复评估/日志层面的现象
④ dry-run 触发存在延迟	坚持	custom_exit() 是 bot loop 中被周期性检查，不会精确卡在 4h 收盘瞬间执行
⑤ stoploss/ROI/custom exit 优先级	撤回原说法，完全反转	在同一轮 should_exit 列表中，exit-signal 在 stoploss 前；但 stoploss 可以在更早的 throttle 轮次已经成交，因此实盘看到 stoploss 先发生，首先是时间先后而不是同轮优先级

尤其是第 ⑤ 条，现在应该明确写成：

同一轮同时满足时，exit-signal/custom_exit 在 Freqtrade 2026.2 的 should_exit 序列中先于 stoploss；但这不意味着实际成交中 4h 阴线一定能抢在止损之前，因为止损可以在 4h 收盘之前的任意一次 bot loop 已经触发。

这比我上次的表述准确得多。

2. 加守卫后，custom_exit 还有没有漏洞？
有，但已经不是原来的 A 类结构性漏洞。

现在：

Python
Run
candle_close = last['date'] + timeframe
if candle_close > current_time:
    return None

这个守卫的价值是明确的：

它把“末行一定是已收盘 K”的外部假设，变成了 custom_exit() 自己的必要条件。

所以我赞成保留。

但它不能解决所有时间问题
① 它不能保证“刚刚收盘的 K 已经进入 dataframe”

例如 20:00:00 K 理论收盘。

bot 可能：

19:59:59：最后一根还是上一根

20:00:02：exchange 数据还没刷新

20:00:20：拿到新 K

20:00:30：custom_exit() 真正看到它

所以它解决的是：

不能提前使用未来 K

而不是：

保证收盘后立刻看到新 K。

这是两件事。

② 时钟偏移会造成“晚一点触发”，不会造成“提前触发”

如果 bot 本机时间比交易所快：

candle_close <= current_time

守卫可能提前放行。

但注意，Freqtrade 的 current_time 本身是 bot 时间体系，而 OHLCV 的 candle timestamp 来自交易所数据。

所以真正需要防的是系统时钟异常。

如果系统时间正常同步，这个风险很小。

如果 bot 时钟落后交易所，则：

candle_close > current_time

会让出场晚一点。

因此这个守卫的方向是正确的：

宁可晚一点，也不要提前拿未完成 K 做出场。

③ 同一根阴线反复评估：仍然存在，但不是核心漏洞

例如：

20:00 新阴线进入 dataframe
20:00:10 custom_exit → return bearish

trade 出掉。

之后：

20:00:40
20:01:10
20:01:40

理论上不再有这个 trade，所以不会再次执行实际出场。

因此它不是原来那种：

“同一根 K 导致多次真实平仓”

的问题。

目前没有必要为了这个再增加状态机。

④ 真正值得关注的是“最新已收盘 K 是否就是你想要的那一根”

当前逻辑实际上是：

取 dataframe 最后一根
→ 计算它的 candle_close
→ candle_close > current_time ? 不用
→ candle_close <= open_date ? 不用
→ close < open ? 出场

这个逻辑本身是自洽的。

尤其：

Python
Run
candle_close <= trade.open_date_utc

很好地处理了：

入场发生在某根 4h K 中间，那么这根 K 即使最后收阴，也不能算“入场后的首根阴线”。

所以这一块现在已经从“结构性危险”降到“需要持续观察的工程边界”。

3. H −87.93%、S−H +85.85pp 到底说明什么？

这里必须非常克制。

它最强的证据不是“策略有 alpha”

而是：

原来的实盘执行机制确实严重破坏了预期出场规则。

因为：

H：裸持 48h       -87.93%
S：修复后出场      -2.07%
差异              +85.85pp

这是巨大的差异。

但 n=1。

更重要的是，H 是：

不止损的 48h 裸持

它不是一个纯粹的：

“S 不执行 4h 阴线出场时会怎样？”

的理想 counterfactual。

所以不能把：

S − H = +85.85pp

解释成：

“4h 阴线出场贡献了 +85.85pp。”

严格来说，这个数字混合了很多东西：

4h 阴线出场

5% stoploss

持有路径

48h 退出

A 类 bug 修复

单个 AIN 的特殊价格路径

更有价值的是 S vs B / L

现在：

S − B = -1.55pp
S − L = -0.81pp

但仍然是：

n = 1

所以目前最合理的科学结论是：

当前样本没有证明 S 相对于 BTC 基准或 score 60–80 对照腿存在正的增量收益。

甚至这个方向上观察值是负的。

但不能因为它是负的就宣布 AI500 没用。

关键问题：是否意味着核心价值其实在“出场规则”？
目前不能这样下结论。

但这已经成为一个必须优先验证的竞争假设。

现在至少有三个竞争假设：

H1：AI500 score 有入场 alpha

即：

AI500 80+ 入场
>
合理的非 AI500 对照
H2：AI500 本身没多少 alpha，但“4h 阴线退出”有价值

即：

同样的入场
+ 4h bearish exit
>
同样的入场
+ 裸持 / 其他退出
H3：真正贡献来自两者组合

即：

AI500 入场
+
4h bearish exit

形成交互效应。

所以后续假设顺序应该改成：
第一优先级：先验证“入场有没有信息量”

这是核心。

不是先优化 exit。

应该回答：

在完全相同的出场规则下，AI500 80+ 和 60–80、以及其他可接受基准，收益分布到底有没有稳定差异？

第二优先级：验证 exit 是否提供增量

固定同一批入场：

同一 entry
├── 4h bearish exit
├── 48h hold
└── 其他预注册基准

这样才能真正估算 exit contribution。

第三优先级：signal age

因为即使 score 本身有信息：

score = 85

也可能完全不同：

signal age = 5 min
vs
signal age = 18 h
vs
signal age = 5 days

所以 age 很可能是 score 的重要条件变量。

第四优先级：其他协变量

你现在已经落盘：

signal_age_hours

prior_gain_pct

score_over_peak

这是正确方向。

4. TOP3 → score 60–80 对照腿，这个替换能不能接受？

可以，而且比原来的 rank 4–10 对照腿更有解释力。

因为你已经实测发现：

每次快照资产数：
median = 3
mean = 3.89
>3 的快照 = 48.9%

这意味着：

“rank 1–3 vs rank 4–10”

在很多时刻根本没有稳定存在的 rank 4–10。

所以原来的 rank control 本身就不成立。

但 L 不是“无偏对照”

这是必须承认的。

60–80 和 80+ 天然可能存在：

不同 score 强度

不同 signal age

不同 prior gain

不同币种组成

不同信号持续时间

不同市场阶段

不同价格动量

所以：

S = score >= 80
L = 60–80

的差异不能简单解释成：

“score 门槛带来的纯因果效应”。

最好的理解方式是：

L 是一个：

探索性 dose-response / score-band 对照

而不是严格随机控制组。

后面如果样本够，应进一步做：

score band
60–70
70–80
80–85
85–90
90+

然后同时看：

return
MAE
holding time
signal age
prior gain
score_over_peak

尤其要看：

score 与收益的关系是不是单调。

如果 60–80、80–90、90+ 没有任何梯度关系，那么“80 门槛”就很可疑。

5. AIN：−5.20% → −2.07%，能不能作为 A 类修复证据？
可以作为“代码级 + 个案级”的证据。
不能作为“策略级”证据。

这两个层级必须分开。

AIN 这笔非常有价值，因为它满足：

同一笔交易
实盘：
-5.20%
stop_loss

修复后重放：
-2.07%
bear4h

差：

+3.13pp

而且原因链条是完整的：

旧实现
→ enter=1 + exit=1
→ freqtrade 冲突裁决
→ 4h exit 被吞
→ 后续盘中跌到 -5%
→ stoploss

新实现
→ custom_exit
→ 不参与 entry/exit signal 冲突
→ bearish 4h 被识别
→ -2.07% 出场

所以它是很好的：

A 类 bug 的复现 + 修复验证样本。

但它不能证明：

“修复后策略收益会提高 3.13pp。”

因为这是单笔。

更不能证明：

“4h 阴线退出平均能提高 3.13pp。”

更不能证明：

“策略现在已经有效。”

什么才算 A 类修复真正被证明？

我会要求至少三层：

Level 1：代码级

证明：

同根 enter=1 + exit=1

不会再吞掉 intended custom exit。

你现在基本完成。

Level 2：事件级

连续积累多笔：

trade entered
→ 后续出现第一根合格 bearish 4h
→ custom_exit 实际触发
→ exit_reason = 4h_bearish_candle

并且与预期 candle 对得上。

Level 3：反事实级

对多笔交易做：

实际修复后结果
vs
如果旧 bug 仍存在的结果

得到：

Δ exit P&L

并观察分布。

至少几十笔之后，才能开始讨论“修复带来的收益影响”；30 笔可以做第一轮工程/策略判断，但依然不够证明长期 alpha。

6. 最大缺陷仍然是 signal age 吗？
从“科学未知量”来说：是。
从“生产工程风险”来说：不是。现在 D 更优先。

这两个不要混在一起。

当前排名我会改成：
P0：D void dtype

这是生产 bug。

因为它不是：

“可能降低收益。”

而是：

直接让特定 pair 的 entry/exit 信号整轮失效。

而且你的证据已经非常扎实：

n=2
+
未预创建 enter_long
+
tuple assignment
→ dtype V0(void)
→ iloc[-1]
→ void() takes at least 1 positional argument
→ get_latest_candle 捕获
→ (None, None)
→ 该 pair 当轮信号跳过

更严重的是：

683 pairs
169 个中招

如果这个统计准确，那么这不是“小概率边界 bug”。

这是当前生产系统的确定性数据形状缺陷。

P1：AI500 score 是否有预测力

这是策略最核心的科学问题。

目前：

S vs B  -1.55pp
S vs L  -0.81pp

没有提供正向证据。

而且 n=1。

所以：

AI500 score alpha 尚未证明。

P2：signal age

你现在已经把：

signal_age_hours
prior_gain_pct
score_over_peak

逐笔落盘，这是非常正确的。

但注意：

signal age 现在还不能称为“已发现的缺陷”。

它应该叫：

尚未进入决策、但高度可能解释 score 信号异质性的关键协变量。

这个措辞更严谨。

7. 只允许再改一处：我会怎么选？

这里答案和你最后的问题有关：

如果允许修 bug，我会先修 D，而不是加 signal_age。

这是明确的。

原因非常简单：

signal_age 是研究变量；D 是生产正确性问题。

在一个存在：

169 / 683

这种级别的确定性 dataframe bug 的系统上继续积累策略样本，会污染实验。

你甚至无法完全确定：

某个没有入场

究竟是：

score 不满足
还是
void dtype
还是
其他 signal retrieval failure

那么后面做 signal age 分层，很容易出现“拿坏数据研究策略”的问题。

D 应该怎么修？

你这个问题的根因不是 Freqtrade，而是 pandas 的 tuple assignment 形状推断。

当前：

Python
Run
dataframe.loc[
    mask,
    ['enter_long', 'enter_tag'],
] = (1, 'ai500_score_top3')

在正常多行场景没问题，但 n=2 的特殊形状会触发 pandas/numpy 的异常 dtype 行为。

最小、最稳的修法：

先显式创建列。

例如在 populate_entry_trend() 开头：

Python
Run
if 'enter_long' not in dataframe.columns:
    dataframe['enter_long'] = 0

if 'enter_tag' not in dataframe.columns:
    dataframe['enter_tag'] = None

然后再赋值。

但我还会进一步建议：

Python
Run
dataframe['enter_long'] = 0
dataframe['enter_tag'] = None

如果这是你这个策略自己负责生成完整 entry signal 的 dataframe，那么直接初始化反而更干净。

关键目的不是“绕过 n=2”，而是：

明确告诉 pandas 这两个字段各自是什么列，而不是让 pandas 从 (1, 'tag') 这个 tuple 自己推断二维结构。

修完 D 后，必须做一个专门的回归测试

至少覆盖：

n=0
n=1
n=2
n=3
n=100

并且分别测试：

mask = 全 False
mask = 只有第 1 行 True
mask = 只有第 2 行 True
mask = 两行 True
mask = 全 True

然后断言：

Python
Run
enter_long.dtype != void
enter_tag.dtype != void

以及：

Python
Run
df.loc[mask, 'enter_long'].iloc[-1]

不会抛异常。

修 D 后，再做 signal_age

而且我现在不建议把 signal_age 直接塞进交易规则。

你之前决定的：

只落盘，不进规则

现在仍然是正确的。

因为一旦现在写：

Python
Run
if signal_age < X:
    allow

你就同时改变：

入场机会
+
策略定义
+
样本分布

又要重新开实验。

目前最干净的是：

修 D
↓
重新干净起算
↓
继续固定 score >=80
↓
固定 4h bearish exit
↓
固定 -5%
↓
固定 Gate3-C
↓
每笔落盘：
    score
    rank
    signal_age
    prior_gain
    score_over_peak
    exit_reason
    return
    MAE
    holding_time
↓
30笔
↓
先看总体
↓
再按 age 分层
最后，我会把当前研究优先级定成这样
                    当前策略
                       │
              ┌────────┴────────┐
              │                 │
         工程正确性          策略科学性
              │                 │
          P0 修 D          P1 AI500 是否有 alpha
              │                 │
       防止实验污染          P2 exit 是否有增量
                                │
                           P3 signal age
                                │
                     prior_gain / peak 等

而你现在这组数据，我会只写成下面这句话：

A 类修复已经获得代码级验证，并在 AIN 单笔重放中观察到 −5.20% → −2.07% 的改善；但 n=1，不能外推为策略级收益。S−H 的 +85.85pp 强烈说明旧实现的出场机制存在实质性执行缺陷，但由于 H 是无止损裸持 48h 对照，不能将该差值归因于 4h 阴线出场本身。S−B、S−L 当前均未显示 AI500 组合相对基准的正向增量，且同样受 n=1 限制。因此下一阶段首先要恢复生产数据正确性（D），然后在冻结规则下积累样本，同时把“AI500 入场 alpha”和“4h 出场增量”拆成两个独立假设验证；signal age 暂时作为落盘协变量，而不是新的交易门槛。

所以，如果现在只准动一处：我选 D，不选 signal_age。

而且这是一次很关键的优先级调整：D 是“先保证实验是真的”，signal_age 是“实验真的以后，再解释为什么”。