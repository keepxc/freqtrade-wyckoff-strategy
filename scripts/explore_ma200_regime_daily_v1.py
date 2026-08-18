"""
探索性——非结论，禁止引用为证据。

200日均线regime，换成逐日forward收益口径（而非Donchian离散交易），
检验是否能突破交易笔数太少导致的MDE瓶颈（confirm_ma200_regime_donchian.py中MDE≈10pp）。

方法：每个交易日t，regime(t)=close(t)>SMA200(t)(用shift(1)避免未来函数)；
fwd_ret(t) = close(t+H)/close(t)-1，H=20个交易日。
按regime分组比较fwd_ret分布，因窗口重叠(overlapping H日收益)存在自相关，
用区块自举(块长度>=H，覆盖重叠窗口的自相关半衰期)算CI。

声明：这是换了"问的问题"——不再是"Donchian交易触发时regime有没有用"，
而是"regime本身对任意时点的未来收益有没有预测力"，更接近纯多头择时命题。
"""
import pandas as pd
import numpy as np

FILES = {
    "BTC": "user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather",
    "ETH": "user_data/data/binance/futures/ETH_USDT_USDT-1d-futures.feather",
    "SOL": "user_data/data/binance/futures/SOL_USDT_USDT-1d-futures.feather",
}

MA_N = 200
H = 20
BLOCK_DAYS = 40  # 2倍持有期，覆盖重叠窗口自相关
N_BOOT = 2000


def build(df, sym):
    df = df.sort_values("date").reset_index(drop=True)
    df["sma200"] = df["close"].rolling(MA_N).mean().shift(1)
    df["regime"] = np.where(df["close"] > df["sma200"], "bull", "bear")
    df["fwd_ret"] = df["close"].shift(-H) / df["close"] - 1
    df = df.dropna(subset=["sma200", "fwd_ret"])
    df["sym"] = sym
    return df[["date", "sym", "regime", "fwd_ret"]]


def block_bootstrap(all_df):
    all_df = all_df.copy()
    all_df["block"] = (all_df["date"] - all_df["date"].min()).dt.days // BLOCK_DAYS
    blocks = all_df["block"].unique()
    real_diff = all_df[all_df.regime == "bull"]["fwd_ret"].median() - all_df[all_df.regime == "bear"]["fwd_ret"].median()

    rng = np.random.RandomState(42)
    diffs = []
    for _ in range(N_BOOT):
        sampled = rng.choice(blocks, size=len(blocks), replace=True)
        rows = pd.concat([all_df[all_df.block == b] for b in sampled], ignore_index=True)
        b_bull = rows[rows.regime == "bull"]["fwd_ret"]
        b_bear = rows[rows.regime == "bear"]["fwd_ret"]
        if len(b_bull) == 0 or len(b_bear) == 0:
            continue
        diffs.append(b_bull.median() - b_bear.median())
    diffs = np.array(diffs)
    lo, hi = np.percentile(diffs, [5, 95])
    return real_diff, lo, hi, len(diffs)


def main():
    parts = []
    for sym, path in FILES.items():
        df = pd.read_feather(path)
        d = build(df, sym)
        parts.append(d)
        bull = d[d.regime == "bull"]["fwd_ret"]
        bear = d[d.regime == "bear"]["fwd_ret"]
        print(f"{sym}: bull n={len(bull)} mean={bull.mean()*100:.2f}% median={bull.median()*100:.2f}%  |  "
              f"bear n={len(bear)} mean={bear.mean()*100:.2f}% median={bear.median()*100:.2f}%")

    all_df = pd.concat(parts, ignore_index=True)
    print(f"\n合并总天数(带fwd_ret观测): {len(all_df)}")
    bull_all = all_df[all_df.regime == "bull"]["fwd_ret"]
    bear_all = all_df[all_df.regime == "bear"]["fwd_ret"]
    print(f"合并: bull n={len(bull_all)} mean={bull_all.mean()*100:.2f}% median={bull_all.median()*100:.2f}%  |  "
          f"bear n={len(bear_all)} mean={bear_all.mean()*100:.2f}% median={bear_all.median()*100:.2f}%")

    real_diff, lo, hi, n_eff = block_bootstrap(all_df)
    print(f"\n区块自举(block={BLOCK_DAYS}天,{N_BOOT}次): 中位数差={real_diff*100:.2f}%  90% CI=[{lo*100:.2f}%, {hi*100:.2f}%]  有效{n_eff}")
    print("CI下界>0 → 方向性证据较强; CI跨零 → 不能排除零效应")


if __name__ == "__main__":
    main()
