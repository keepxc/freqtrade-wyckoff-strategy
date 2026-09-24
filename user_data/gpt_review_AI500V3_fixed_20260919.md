先给结论：

A 类缺陷基本被结构性消除，但当前最大风险已经从“信号裁决 bug”转移到了“AI500 榜单本身是否提供可交易的信息增量”。
修复是必要的，但它没有证明策略成立；它只是让策略终于执行了文档里写的规则。

下面逐点。

1. custom_exit 修法是否真正消除 A 类缺陷？还有哪些漏洞？
结论：核心缺陷消除了。

旧问题本质：

入场状态和持仓状态共用 dataframe 信号空间。

这是架构错误。

现在：

populate_entry_trend
        ↓
enter_long

持仓后

custom_exit(trade)
        ↓
exit

两个路径隔离。

所以：

enter_long=1 不会再阻塞 exit

阴线不再吞入场

持仓期间仍在 top3 不影响 exit

这个修法方向正确。

但是还有 5 个需要注意的问题。

① custom_exit 是否一定调用？

条件：

Python
Run
use_exit_signal = True

这个设置不是问题。

Freqtrade 退出流程大致：

stoploss
  ↓
ROI
  ↓
exit_signal
  ↓
custom_exit

但要注意：

custom_exit() 是否执行，取决于：

Python
Run
use_custom_exit

不是：

Python
Run
use_exit_signal

不同版本 Freqtrade 配置略有区别。

你的代码里：

Python
Run
def custom_exit(...)

存在即可。

但建议明确：

Python
Run
use_custom_exit = True

否则未来升级版本容易出现行为变化。

这是一个小漏洞。

② dataframe 最后一行是否一定是已收盘 K？

这里你的注释有一点过于绝对。

你写：

dataframe 末行 = 最近一根已收盘 4h K

实际：

通常成立。

但不是 API 保证。

原因：

get_analyzed_dataframe()

返回的是分析后的 dataframe。

实时模式：

交易循环
↓
exchange OHLCV
↓
populate_indicators
↓
缓存

不同 exchange：

candle 更新延迟

websocket / REST 差异

startup candle

数据刷新周期

可能出现：

最后一根实际上刚刚形成。

更稳：

不要假设。

应该检查：

Python
Run
last['date'] + timeframe <= current_time

即：

Python
Run
candle_close <= current_time

否则：

直接 return None。

现在代码：

Python
Run
candle_close <= trade.open_date_utc

只判断入场时间。

没有判断：

K是否已经结束

这是当前 custom_exit 最大的小漏洞。

③ 同一根阴线会不会重复触发？

不会造成重复成交。

原因：

custom_exit 返回：

Python
Run
'4h_bearish_candle'

之后：

trade 消失。

但是如果：

exit_order 未成交

交易所拒单

partial fill

下一轮仍可能触发。

这不是策略 bug。

Freqtrade 会处理。

④ dry-run 和实盘触发时刻差异

这个必须强调。

你的设计：

4h K 收盘
        ↓
custom_exit
        ↓
下一次 bot loop

实际不是：

00:00:00 精确退出。

可能：

00:00:30
00:01:00
00:02:30

如果：

币价快速跌：

你看到：

回测：

-1%

实盘：

-3%

正常。

所以你的指标不要看：

“阴线退出收益”

应该看：

candle_close
-
actual_exit_time

延迟分布。

⑤ 与 stoploss / ROI 顺序

你的理解：

三条路径互不冲突

基本正确。

实际：

优先级：

stoploss
    >
ROI
    >
exit signal/custom_exit

所以：

如果阴线形成之前已经：

-5%

不会等阴线。

这是合理。

2. 修复后入场变化：阴线也可以买，好还是坏？

这是一个重要副作用。

旧版本实际上隐藏形成：

只买阳线后的 AI500

因为：

阴线：

enter=1
exit=1

=> 双废

等价于增加了一个隐藏过滤：

当前 4h candle 必须不是阴线

修复后：

阴线也能买。

这不是一定好。

取决于 AI500 信号生命周期。

如果：

AI500 是：

类型 A：

趋势启动信号

那么：

阴线买可能更好。

因为：

第一根回调阴线可能是吸筹。

如果：

AI500 是：

上涨高潮信号

那么：

阴线买可能更差。

因为：

你买的是：

已经上涨
+
开始回落

BR 样本其实偏向第二种：

signal age 55h
change +200%
MFE 0
MAE -4.35%

它不像启动。

更像：

末端追涨。

所以：

修复后增加阴线入场机会，我认为是中性偏危险。

但不能提前改。

因为你已经冻结规则。

3. 我之前 6/7 两点，哪些修正？
第6点：

最可能亏钱：
score 是拥挤后的结果变量

我现在：

坚持，而且置信度提高。

原因不是 BR 一笔。

而是三件事合并：

证据1：

BR:

episode age:
2天7小时

change:
+200%

MFE:
0
证据2：

榜单结构：

全部资产:
42

ZEC:
3015 行

说明：

榜不是发现机会。

更像：

持续展示热点。

证据3：

top3：

实际上：

top3 ≈ 全榜

排序没有信息量。

所以：

score/rank 很可能只是：

过去上涨后的标签

不是：

未来收益预测变量
第7点：

第一优先改事件时间对齐

我现在需要修正。

当时不知道 A 类 bug。

现在：

优先级下降。

因为：

如果 exit 根本没执行，

讨论：

信号刷新 vs candle

意义有限。

现在排序：

以前：

1 时间对齐
2 score alpha
3 exit

现在：

1 score 是否有预测力
2 信号年龄
3 时间对齐
4. C 数据说明：入场筛选是不是空的？

答案：

非常接近。

但准确说：

不是“完全空”。

而是：

top3 这个动作没有产生有效截面选择。

因为：

数据：

每轮资产数 median=3

意味着：

你的：

rank<=3

实际上：

接近：

存在即可

不是：

筛选。

真正需要验证的框架应该改成：

不是：

top3 vs not top3

因为没有对照。

而是：

episode 级别分析

一个 AI500 episode：

例如：

ZEC
start_time
score_peak
duration

然后计算：

未来：

+4h
+8h
+24h
+48h

收益。

分组：

A：

score 分位

例如：

80-85
85-90
90+
B：

signal age

非常关键：

<6h
6-24h
24-48h
>48h
C：

已有涨幅

0-20%
20-50%
50-100%
100%+

我认为：

最值得看的交叉：

score × age × previous_gain

因为你的风险不是 score 高。

而是：

score 高
+
已经涨很多
+
信号还挂着
5. BR 是否是“拥挤结果变量”的活样本？

答案：

是候选样本，不是证明。

正确记录：

不是：

BR证明AI500追高失败

而是：

记录：

case_id:
BR-20260918

label:
late_signal_candidate

features:

signal_age:
55h

change_pct:
+200%

score:
?

entry:
...

MFE:
0

MAE:
-4.35%

exit:
stoploss

未来累计：

late_signal_candidate
vs
fresh_signal

才有意义。

6. 当前最大缺陷是哪一个？

只选一个：

最大缺陷：
AI500 signal age 没有限制，导致买入对象可能已经完成主要行情。

原因：

不是因为 BR。

而是：

你的系统目前：

唯一时间变量：

start_time

但它只用于观察。

没有进入决策。

结果：

策略允许：

上涨第1小时

和：

上涨第5天

完全同等。

这是比 stoploss、exit 更底层的问题。

因为：

如果信号本身已经衰减：

所有后续优化：

止盈

止损

出场

都是治疗结果。

7. 只允许改一处，我改哪里？

不改参数。

不改 score。

不改止损。

我改：

增加信号年龄记录与分层验证，不进入交易规则。

不是过滤。

只是验证。

原因：

现在最大未知：

AI500 是否：

新出现信号有效

还是：

长期挂榜无效

你现在没有答案。

具体：

入场时落盘：

增加：

signal_age_hours

=
entry_time - start_time

然后：

30笔结束后：

不要只看：

总收益

看：

age bucket:

0-6h
6-24h
24-48h
>48h

如果发现：

48h 全部拖累

下一轮才有证据改规则。

最后整体判断

当前版本：

A 类：

✅ 修复正确，必须保留。

门③方案C：

目前合理。

因为旧 ref_diff_pp 确实更像静态误杀。

最大未解决：

不是僵尸。

不是 stoploss。

不是 4h exit。

而是：

AI500 给你的到底是“早期机会信号”，还是“上涨结果展示”。

现在所有证据都指向后者的可能性增加。

下一阶段不要继续改策略。

先完成：

30笔 + signal_age/MFE/MAE/episode 级归因。

否则任何进一步优化都会再次进入参数试错。