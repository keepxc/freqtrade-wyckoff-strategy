"""
探索性脚本——非结论，禁止引用为证据 (CLAUDE.md 探索性/确认性两阶段纪律)

目的：粗探"缩量止跌+反转"这类结构性低点信号，在BTC历史上出现频率
与后续表现，为是否值得转入确认阶段(预注册TASK)提供参考。不写入
quant_system_tasks.md 判定表。

定义（探索性，未经论证的初版）：
- 触发日 t：close[t] 是过去 N=20 日最低收盘（20日新低）
- 用当日成交量相对其自身 20 日均量的比值，把触发日分两组：
  - 缩量组（低量测试/威科夫 Spring 语义）：vol_ratio <= 0.8
  - 放量组（抛售高潮/威科夫 SC 语义）：vol_ratio >= 1.5
  - 其余为中性组
- 对每组测 forward 10/20 日收益，与"全样本无条件 20日新低"及"全体交易日"
  基线对比
"""
import pandas as pd
import numpy as np

PATHS = {
    'spot': 'user_data/data/binance/BTC_USDT-1d.feather',
    'futures': 'user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather',
}

N_LOW = 20
FWD = [10, 20]

def analyze(path, label):
    df = pd.read_feather(path)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    df['roll_low'] = df['close'].rolling(N_LOW).min()
    df['is_new_low'] = df['close'] <= df['roll_low']
    df['vol_ma20'] = df['volume'].rolling(N_LOW).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma20']

    for f in FWD:
        df[f'fwd{f}'] = df['close'].shift(-f) / df['close'] - 1.0

    trig = df[df['is_new_low']].copy()
    trig = trig.dropna(subset=['vol_ratio'])

    dry = trig[trig['vol_ratio'] <= 0.8]
    climax = trig[trig['vol_ratio'] >= 1.5]
    neutral = trig[(trig['vol_ratio'] > 0.8) & (trig['vol_ratio'] < 1.5)]

    print(f"\n=== {label} ===")
    print(f"总样本 {len(df)} 天，20日新低触发日 {len(trig)} 次 "
          f"（缩量{len(dry)} / 放量{len(climax)} / 中性{len(neutral)}）")

    all_days = df.dropna(subset=['fwd10', 'fwd20'])

    def report(name, sub):
        if len(sub) == 0:
            print(f"  {name}: n=0，无法评估")
            return
        for f in FWD:
            s = sub[f'fwd{f}'].dropna()
            if len(s) == 0:
                continue
            print(f"  {name} n={len(s):>4}  fwd{f}均值={s.mean()*100:+.2f}%  "
                  f"中位={s.median()*100:+.2f}%  正比例={((s>0).mean()*100):.1f}%")

    report('全体交易日(基线)', all_days)
    report('全部20日新低(无条件)', trig)
    report('缩量新低(vol<=0.8x)', dry)
    report('放量新低(vol>=1.5x)', climax)
    report('中性新低', neutral)

for label, path in PATHS.items():
    analyze(path, label)
