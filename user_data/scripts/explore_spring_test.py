"""
探索性脚本——非结论，禁止引用为证据 (CLAUDE.md 探索性/确认性两阶段纪律)

摸第二版结构性低点定义：威科夫 Spring/Test 语义——
"二次探底不创显著新低 + 缩量 + 随后突破短期高点确认"，
比上一版单纯"量比"定义更贴近结构（第零·五原则：量价结构优先于粗代理）。

定义（探索性，仍是初版，未经论证）：
1. climax候选 C：day t，close[t] 是过去 N_CLIMAX=20 日最低收盘
2. 在 t+1..t+T_WINDOW(15) 天内找 test 点 t2：
   - low[t2] 相对 low[t] 在 [-3%, +3%] 区间内（不创显著新低，也不用远离前低）
   - volume[t2] < volume[t] * VOL_TEST_RATIO(0.7)（缩量测试，抛压衰竭）
3. 确认 E：t2 之后 T_CONFIRM(5) 天内首次 close 突破 t2 前 5 日高点
4. 测 E 点 fwd10/fwd20 收益，对比无条件基线与"无缩量确认"的对照组（只满足1+3，不要求2的缩量条件）
   —— 对照组的作用是隔离"缩量"这个条件本身是否贡献增量，不是"任意二次测试都行"
"""
import pandas as pd
import numpy as np

PATHS = {
    'spot': 'user_data/data/binance/BTC_USDT-1d.feather',
    'futures': 'user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather',
}

N_CLIMAX = 20
T_WINDOW = 15
VOL_TEST_RATIO = 0.7
TOL = 0.03
T_CONFIRM = 5
FWD = [10, 20]


def find_patterns(df, require_dry_volume):
    df = df.reset_index(drop=True)
    n = len(df)
    close = df['close'].values
    low = df['low'].values
    high = df['high'].values
    vol = df['volume'].values
    roll_low = df['close'].rolling(N_CLIMAX).min().values

    events = []
    t = N_CLIMAX
    while t < n:
        is_climax = close[t] <= roll_low[t]
        if not is_climax:
            t += 1
            continue
        # 在窗口内找 test 点
        found_test = None
        for t2 in range(t + 1, min(t + 1 + T_WINDOW, n)):
            near_low = abs(low[t2] - low[t]) / low[t] <= TOL
            if not near_low:
                continue
            if require_dry_volume:
                if vol[t2] < vol[t] * VOL_TEST_RATIO:
                    found_test = t2
                    break
            else:
                found_test = t2
                break
        if found_test is None:
            t += 1
            continue
        t2 = found_test
        # 在 t2 后找确认突破点
        if t2 < 5:
            t += 1
            continue
        swing_high = high[max(0, t2 - 5):t2].max()
        confirm_e = None
        for e in range(t2 + 1, min(t2 + 1 + T_CONFIRM, n)):
            if close[e] > swing_high:
                confirm_e = e
                break
        if confirm_e is not None:
            events.append(confirm_e)
        t += 1  # 允许climax候选重叠扫描（探索阶段不做去重复杂化）
    return sorted(set(events))


def analyze(path, label):
    df = pd.read_feather(path)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    for f in FWD:
        df[f'fwd{f}'] = df['close'].shift(-f) / df['close'] - 1.0

    print(f"\n=== {label} ===  总样本 {len(df)} 天")

    def report(name, idx_list):
        idx_list = [i for i in idx_list if i < len(df)]
        sub = df.iloc[idx_list].dropna(subset=['fwd10', 'fwd20'])
        if len(sub) == 0:
            print(f"  {name}: n=0，无法评估")
            return
        for f in FWD:
            s = sub[f'fwd{f}']
            print(f"  {name} n={len(s):>4}  fwd{f}均值={s.mean()*100:+.2f}%  "
                  f"中位={s.median()*100:+.2f}%  正比例={((s>0).mean()*100):.1f}%")

    all_days = df.dropna(subset=['fwd10', 'fwd20'])
    report('全体交易日(基线)', all_days.index.tolist())

    spring_events = find_patterns(df, require_dry_volume=True)
    control_events = find_patterns(df, require_dry_volume=False)
    print(f"  Spring(缩量测试+突破确认) 触发 {len(spring_events)} 次； "
          f"对照(任意二次测试+突破确认，不要求缩量) 触发 {len(control_events)} 次")
    report('Spring缩量测试组', spring_events)
    report('对照组(无缩量条件)', control_events)


for label, path in PATHS.items():
    analyze(path, label)
