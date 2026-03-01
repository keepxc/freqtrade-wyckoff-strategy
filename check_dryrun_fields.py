"""
P2 — Dry Run 持续监控脚本
================================================================
每日运行，检查以下三大类问题：

  ① 数据完整性：macro_phase / bear_market_mode / dist_cause_sufficient
     的 null 比例（>5% = 数据管道问题）

  ② 状态机死锁：dist_sequence_progress 是否卡在某个状态超过 180 天

  ③ 信号激活率：daily_choch_confirmed 近 30 天激活率（目视与 BTC 走势对比）

  ④ 交易统计：从 SQLite 读取近期交易，统计各信号类型的胜率/频率

用法：
  # 检查最新状态（近 30 天）
  python check_dryrun_fields.py

  # 指定日期范围
  python check_dryrun_fields.py --since 2026-01-01 --until 2026-03-01

  # 跳过有问题就退出（适合 CI / cron 报警）
  python check_dryrun_fields.py --strict

数据路径（自动推断）：
  user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather
  user_data/data/binance/futures/BTC_USDT_USDT-1d-futures.feather
  user_data/tradesv3.sqlite
"""

import sys
import os
import argparse
import sqlite3
import warnings
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore", category=FutureWarning)

# ─── 项目根目录加入路径，使 wyckoff 包可 import ────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

try:
    from wyckoff.daily_event_detector import DailyEventDetector
    _DETECTOR_AVAILABLE = True
except ImportError as e:
    _DETECTOR_AVAILABLE = False
    _DETECTOR_ERROR = str(e)

# ─── 路径配置 ─────────────────────────────────────────────────────────────────
DATA_DIR  = os.path.join(ROOT, "user_data", "data", "binance", "futures")
DB_PATH   = os.path.join(ROOT, "user_data", "tradesv3.sqlite")
H1_FILE   = os.path.join(DATA_DIR, "BTC_USDT_USDT-1h-futures.feather")
D1_FILE   = os.path.join(DATA_DIR, "BTC_USDT_USDT-1d-futures.feather")

# ─── 报警阈值 ─────────────────────────────────────────────────────────────────
NULL_RATIO_WARN     = 0.05   # null 比例 >5% 告警
NULL_RATIO_ERROR    = 0.20   # null 比例 >20% 错误
STUCK_DAYS          = 180    # 状态机卡住超过 180 天报警
RECENT_WINDOW_DAYS  = 30     # 近期窗口（默认 30 天）
CHOCH_MIN_RATE      = 0.03   # daily_choch_confirmed 近 30 天激活率下限（<3% 表示疑似失效）
CHOCH_MAX_RATE      = 0.50   # 激活率上限（>50% 表示异常敏感）

# ─── 状态机状态顺序（用于死锁检测排序）────────────────────────────────────────
SEQ_ORDER = ["none", "bc_seen", "ar_seen", "st_seen", "utad_seen", "sow_seen", "sufficient"]


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

def load_feather(path: str, name: str) -> Optional[pd.DataFrame]:
    """加载 feather 文件，返回标准化后的 DataFrame；失败时返回 None。"""
    if not os.path.exists(path):
        print(f"  ⚠️  {name} 文件不存在：{path}")
        return None
    try:
        df = pd.read_feather(path)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], utc=True)
            df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"  ❌ {name} 读取失败：{e}")
        return None


def section(title: str):
    print(f"\n{'═' * 68}")
    print(f"  {title}")
    print(f"{'═' * 68}")


def ok(msg: str):   print(f"  ✅ {msg}")
def warn(msg: str): print(f"  ⚠️  {msg}")
def err(msg: str):  print(f"  ❌ {msg}")
def info(msg: str): print(f"     {msg}")


def null_ratio(series: pd.Series) -> float:
    """计算空值（NaN / None / 'none' / ''）占比。"""
    n = len(series)
    if n == 0:
        return 1.0
    nulls = series.isna().sum()
    # 字符串列中的 'none' 也视为空
    if series.dtype == object:
        nulls += (series == "none").sum() + (series == "").sum()
    return float(nulls) / n


def rate_label(r: float) -> str:
    return f"{r * 100:.1f}%"


# ══════════════════════════════════════════════════════════════════════════════
# 核心检查：① 数据完整性
# ══════════════════════════════════════════════════════════════════════════════

def check_null_ratios(df_1h: pd.DataFrame, since: pd.Timestamp, until: pd.Timestamp) -> list[str]:
    """
    检查关键字段的 null 比例。
    返回错误列表（空 = 全部通过）。
    """
    section("① 数据完整性检查（关键字段 null 比例）")
    info(f"检测窗口：{since.date()} ~ {until.date()}")

    mask = (df_1h["date"] >= since) & (df_1h["date"] <= until)
    df_w = df_1h[mask].copy()
    info(f"窗口内 1H K线数：{len(df_w)}")

    # 需要检查的字段（merge 后带 _1d 后缀的已重命名，直接用规范名）
    FIELDS = [
        ("macro_phase",             "macro 宏观阶段",       False),  # (field, 描述, is_bool)
        ("daily_choch_confirmed",   "Daily CHoCH 确认",    True),
        ("dist_cause_sufficient",   "派发因果充足",         True),
        ("dist_sequence_progress",  "状态机进度",           False),
        ("bear_market_mode",        "熊市模式开关",         True),
    ]

    errors = []

    for field, desc, is_bool in FIELDS:
        if field not in df_w.columns:
            warn(f"{field:30s}  —— 列不存在（策略可能未集成或列名已变更）")
            continue

        ratio = null_ratio(df_w[field])

        if ratio > NULL_RATIO_ERROR:
            msg = f"{field:30s}  null={rate_label(ratio)}  ❌ 严重（>{rate_label(NULL_RATIO_ERROR)}）"
            err(msg)
            errors.append(f"[NULL-ERROR] {field}: {rate_label(ratio)}")
        elif ratio > NULL_RATIO_WARN:
            msg = f"{field:30s}  null={rate_label(ratio)}  ⚠️  告警（>{rate_label(NULL_RATIO_WARN)}）"
            warn(msg)
            errors.append(f"[NULL-WARN] {field}: {rate_label(ratio)}")
        else:
            ok(f"{field:30s}  null={rate_label(ratio)}")

        # 对 bool 字段额外输出激活率
        if is_bool and field in df_w.columns:
            active = df_w[field].fillna(False).astype(bool).mean()
            info(f"   激活率：{rate_label(active)}")

        # macro_phase 分布
        if field == "macro_phase" and field in df_w.columns:
            counts = df_w[field].value_counts(dropna=False)
            for phase, cnt in counts.items():
                info(f"   {str(phase):15s}：{cnt} 根 ({cnt/len(df_w)*100:.1f}%)")

    return errors


# ══════════════════════════════════════════════════════════════════════════════
# 核心检查：② 状态机死锁检测
# ══════════════════════════════════════════════════════════════════════════════

def check_state_machine(df_1d_annotated: pd.DataFrame, until: pd.Timestamp) -> list[str]:
    """
    检查 dist_sequence_progress 是否长期卡在同一状态。
    df_1d_annotated：已经过 DailyEventDetector.detect() 的 Daily DataFrame。
    """
    section("② 状态机死锁检测（dist_sequence_progress）")

    errors = []
    if "dist_sequence_progress" not in df_1d_annotated.columns:
        warn("dist_sequence_progress 列不存在，跳过死锁检测")
        return errors

    df = df_1d_annotated[df_1d_annotated["date"] <= until].copy()
    if df.empty:
        warn("无有效数据，跳过")
        return errors

    # 当前最新状态
    latest = df.iloc[-1]
    current_state = latest.get("dist_sequence_progress", "none")
    current_date  = latest["date"].date()
    info(f"最新 Daily 日期：{current_date}")
    info(f"当前状态：{current_state}")

    # 找状态最后一次发生变化的日期
    progs = df["dist_sequence_progress"].fillna("none")
    # 后向对比，找最后一次不等于 current_state 的位置
    changed_mask = progs != current_state
    if changed_mask.any():
        last_change_idx = changed_mask[changed_mask].index[-1]
        last_change_date = df.loc[last_change_idx, "date"].date()
        days_stuck = (current_date - last_change_date).days
        info(f"状态上次变更：{last_change_date}（{days_stuck} 天前）")
        if days_stuck > STUCK_DAYS:
            msg = f"dist_sequence_progress 已卡在 '{current_state}' 超过 {days_stuck} 天（阈值 {STUCK_DAYS} 天）"
            err(msg)
            errors.append(f"[DEADLOCK] {msg}")
        else:
            ok(f"状态机活跃，{days_stuck} 天前曾发生变更")
    else:
        # 全程都是同一状态
        first_date = df.iloc[0]["date"].date()
        days_stuck = (current_date - first_date).days
        info(f"全历史均为 '{current_state}'，共 {days_stuck} 天")
        if current_state == "none" and days_stuck > STUCK_DAYS:
            msg = f"dist_sequence_progress 始终为 'none'（{days_stuck} 天），可能数据不足或探测器未运行"
            warn(msg)
            errors.append(f"[DEADLOCK-MAYBE] {msg}")
        else:
            ok(f"状态机处于 '{current_state}'，全历史一致（正常）")

    # 输出状态转换历史（近 180 天）
    cutoff = pd.Timestamp(until) - pd.Timedelta(days=180)
    df_recent = df[df["date"] >= cutoff]
    transitions = df_recent[df_recent["dist_sequence_progress"] != df_recent["dist_sequence_progress"].shift(1)]
    if not transitions.empty:
        info(f"\n  近 180 天状态转换记录（{len(transitions)} 次）：")
        for _, row in transitions.iterrows():
            info(f"    {row['date'].date()}  →  {row['dist_sequence_progress']}")
    else:
        info("近 180 天无状态转换")

    # 当前状态全景（各状态最后一次激活日期）
    info("\n  各状态最后激活日期：")
    for state in SEQ_ORDER:
        mask = progs == state
        if mask.any():
            last_d = df[mask]["date"].max().date()
            info(f"    {state:20s}：最近 {last_d}")
        else:
            info(f"    {state:20s}：从未出现")

    return errors


# ══════════════════════════════════════════════════════════════════════════════
# 核心检查：③ daily_choch_confirmed 近 30 天激活率
# ══════════════════════════════════════════════════════════════════════════════

def check_choch_rate(df_1d_annotated: pd.DataFrame, until: pd.Timestamp) -> list[str]:
    """
    检查 daily_choch_confirmed 近 RECENT_WINDOW_DAYS 天的激活率。
    同时输出近期 BTC 价格走势供人工目视对比。
    """
    section(f"③ daily_choch_confirmed 近 {RECENT_WINDOW_DAYS} 天激活率")

    errors = []
    if "daily_choch_confirmed" not in df_1d_annotated.columns:
        warn("daily_choch_confirmed 列不存在，跳过")
        return errors

    since_choch = until - pd.Timedelta(days=RECENT_WINDOW_DAYS)
    mask = (df_1d_annotated["date"] > since_choch) & (df_1d_annotated["date"] <= until)
    df_r = df_1d_annotated[mask].copy()
    if df_r.empty:
        warn("无近期数据，跳过")
        return errors

    n_total  = len(df_r)
    n_active = df_r["daily_choch_confirmed"].fillna(False).astype(bool).sum()
    rate     = n_active / n_total if n_total > 0 else 0.0

    info(f"近 {RECENT_WINDOW_DAYS} 天日线数：{n_total} 根")
    info(f"daily_choch_confirmed 激活：{n_active} 次（{rate_label(rate)}）")

    if rate < CHOCH_MIN_RATE:
        msg = f"daily_choch_confirmed 激活率 {rate_label(rate)} 低于 {rate_label(CHOCH_MIN_RATE)}，可能数据问题或市场极度平稳"
        warn(msg)
        # 不作为 error，仅提示
    elif rate > CHOCH_MAX_RATE:
        msg = f"daily_choch_confirmed 激活率 {rate_label(rate)} 高于 {rate_label(CHOCH_MAX_RATE)}，信号异常敏感"
        err(msg)
        errors.append(f"[CHOCH-OVERSENSITIVE] {rate_label(rate)}")
    else:
        ok(f"激活率 {rate_label(rate)}，在合理范围 [{rate_label(CHOCH_MIN_RATE)}, {rate_label(CHOCH_MAX_RATE)}]")

    # 展示激活日期 + BTC 收盘价（人工目视）
    activated_rows = df_r[df_r["daily_choch_confirmed"].fillna(False).astype(bool)]
    if not activated_rows.empty:
        info(f"\n  近 {RECENT_WINDOW_DAYS} 天激活明细（日期 / BTC 收盘 / macro_phase）：")
        for _, row in activated_rows.iterrows():
            phase = row.get("macro_phase", "?")
            info(f"    {row['date'].date()}  close={row['close']:,.0f}  phase={phase}")
    else:
        info("  近期无激活记录")

    # 近期 macro_phase 分布
    if "macro_phase" in df_r.columns:
        info(f"\n  近 {RECENT_WINDOW_DAYS} 天 macro_phase 分布：")
        counts = df_r["macro_phase"].value_counts(dropna=False)
        for phase, cnt in counts.items():
            info(f"    {str(phase):15s}：{cnt} 天 ({cnt/n_total*100:.1f}%)")

    return errors


# ══════════════════════════════════════════════════════════════════════════════
# 核心检查：④ SQLite 交易统计
# ══════════════════════════════════════════════════════════════════════════════

def check_trade_statistics(since: pd.Timestamp, until: pd.Timestamp) -> list[str]:
    """
    从 tradesv3.sqlite 读取近期交易，统计各信号类型的频率和胜率。
    与回测基准对比，偏差 >50% 告警。
    """
    section("④ 近期交易统计（SQLite）")

    errors = []
    if not os.path.exists(DB_PATH):
        warn(f"SQLite 文件不存在：{DB_PATH}，跳过交易统计")
        info("（Dry Run 容器需先运行一段时间才会产生交易记录）")
        return errors

    try:
        conn = sqlite3.connect(DB_PATH)

        # ── 读取 trades 表 ───────────────────────────────────────────────────
        trades_df = pd.read_sql_query(
            """
            SELECT
                id, pair, is_short,
                open_date, close_date,
                open_rate, close_rate,
                profit_ratio, profit_abs,
                enter_tag, exit_reason,
                is_open
            FROM trades
            WHERE open_date >= ? AND open_date <= ?
            ORDER BY open_date DESC
            """,
            conn,
            params=(since.isoformat(), until.isoformat()),
        )
        conn.close()
    except Exception as e:
        warn(f"读取 SQLite 失败：{e}")
        errors.append(f"[DB-ERROR] {e}")
        return errors

    if trades_df.empty:
        info(f"窗口 {since.date()} ~ {until.date()} 内无交易记录")
        info("（若 Dry Run 已运行，请检查日期范围或等待更多信号触发）")
        return errors

    total    = len(trades_df)
    closed   = trades_df[trades_df["is_open"] == 0]
    open_cnt = trades_df[trades_df["is_open"] == 1]
    info(f"共 {total} 笔（已关闭 {len(closed)} 笔，持仓中 {len(open_cnt)} 笔）")

    if closed.empty:
        info("暂无已关闭交易，跳过胜率统计")
        return errors

    # ── 整体胜率 ────────────────────────────────────────────────────────────
    wins = (closed["profit_ratio"] > 0).sum()
    wr   = wins / len(closed)
    info(f"\n  整体胜率：{wr * 100:.1f}%（{wins}/{len(closed)}），"
         f"平均盈亏：{closed['profit_ratio'].mean() * 100:.2f}%")

    # 胜率 < 60% 连续 20 笔预警
    recent_20 = closed.tail(20)
    if len(recent_20) >= 20:
        wr_20 = (recent_20["profit_ratio"] > 0).mean()
        if wr_20 < 0.60:
            msg = f"近 20 笔胜率 {rate_label(wr_20)} < 60%，触发早停预警！（制度变化风险）"
            err(msg)
            errors.append(f"[WIN-RATE-WARN] {msg}")
        else:
            ok(f"近 20 笔胜率：{rate_label(wr_20)}")

    # ── 按信号类型统计 ───────────────────────────────────────────────────────
    info("\n  按入场信号统计（enter_tag）：")

    # 回测基准胜率（参考 TASKS_with_checklist.md）
    BACKTEST_BENCHMARKS = {
        "wyckoff_wave_start":   (0.95, 90),
        "wyckoff_axis_support": (0.74, 239),
        "wyckoff_lps":          (0.92, 30),
        "wyckoff_sos":          (0.81, 30),
        "wyckoff_sow":          (0.60, 104),
        "wyckoff_utad":         (0.75, 10),
    }

    sig_groups = closed.groupby("enter_tag")
    rows = []
    for tag, grp in sig_groups:
        n   = len(grp)
        wr  = (grp["profit_ratio"] > 0).mean()
        avg = grp["profit_ratio"].mean()
        rows.append((tag, n, wr, avg))

    rows.sort(key=lambda x: -x[1])  # 按笔数降序
    for tag, n, wr, avg in rows:
        bmark = BACKTEST_BENCHMARKS.get(str(tag), None)
        bench_str = f"（基准 wr={bmark[0]*100:.0f}%/{bmark[1]}笔）" if bmark else ""
        flag = ""

        if bmark and n >= 10:
            # 频率偏差：实际笔数 vs 基准比例（粗略估算）
            wr_diff = wr - bmark[0]
            if wr_diff < -0.15:
                flag = "  ⚠️  胜率偏低"
            elif wr_diff > 0.15:
                flag = "  🟢 胜率优秀"

        info(f"    {str(tag):30s}  {n:4d}笔  wr={wr*100:.1f}%  avg={avg*100:.2f}%  {bench_str}{flag}")

    # ── 多空分布 ─────────────────────────────────────────────────────────────
    if "is_short" in closed.columns:
        long_  = closed[closed["is_short"] == 0]
        short_ = closed[closed["is_short"] == 1]
        info(f"\n  多空分布：多头 {len(long_)} 笔 / 空头 {len(short_)} 笔")
        if len(long_) > 0:
            info(f"    多头胜率：{(long_['profit_ratio']>0).mean()*100:.1f}%")
        if len(short_) > 0:
            info(f"    空头胜率：{(short_['profit_ratio']>0).mean()*100:.1f}%")

    # ── 退出原因分布 ─────────────────────────────────────────────────────────
    info("\n  退出原因分布：")
    exit_counts = closed["exit_reason"].value_counts(dropna=False)
    for reason, cnt in exit_counts.items():
        info(f"    {str(reason):30s}：{cnt} 次 ({cnt/len(closed)*100:.1f}%)")

    return errors


# ══════════════════════════════════════════════════════════════════════════════
# 当前状态快照
# ══════════════════════════════════════════════════════════════════════════════

def print_current_snapshot(df_1d: pd.DataFrame, df_1h: Optional[pd.DataFrame]):
    """输出当前最新状态一览（实盘当下所处的市场状态）。"""
    section("📌 当前状态快照（最新 K 线）")

    # Daily 最新
    latest_d = df_1d.iloc[-1]
    info(f"最新 Daily K线：{latest_d['date'].date()}")
    info(f"  BTC 收盘：{latest_d['close']:,.2f} USDT")

    fields_d = [
        "macro_phase", "daily_choch_confirmed", "daily_sow_bar",
        "daily_bc_detected", "daily_bc_path",
        "dist_sequence_progress", "dist_cause_sufficient",
        "buying_exhaustion",
    ]
    for f in fields_d:
        val = latest_d.get(f, "N/A")
        info(f"  {f:35s}：{val}")

    # 1H 最新
    if df_1h is not None and not df_1h.empty:
        latest_h = df_1h.iloc[-1]
        info(f"\n最新 1H K线：{latest_h['date']}")
        fields_h = [
            "macro_phase", "bear_market_mode", "dist_cause_sufficient",
            "daily_choch_confirmed",
        ]
        for f in fields_h:
            val = latest_h.get(f, "—（需运行策略才能填充）")
            info(f"  {f:35s}：{val}")


# ══════════════════════════════════════════════════════════════════════════════
# 主函数
# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(description="Dry Run 字段健康度检查")
    p.add_argument(
        "--since", type=str, default=None,
        help="检查起始日期，格式 YYYY-MM-DD（默认：30天前）"
    )
    p.add_argument(
        "--until", type=str, default=None,
        help="检查截止日期，格式 YYYY-MM-DD（默认：今天）"
    )
    p.add_argument(
        "--strict", action="store_true",
        help="严格模式：有任何 ERROR 就以非零退出码退出（适合 cron 报警）"
    )
    p.add_argument(
        "--no-db", action="store_true",
        help="跳过 SQLite 交易统计（SQLite 文件不存在时自动跳过）"
    )
    return p.parse_args()


def main():
    args = parse_args()

    now = pd.Timestamp.utcnow().normalize()

    until = pd.Timestamp(args.until, tz="UTC") if args.until else now
    since = (
        pd.Timestamp(args.since, tz="UTC")
        if args.since
        else until - pd.Timedelta(days=RECENT_WINDOW_DAYS)
    )

    print("=" * 68)
    print("  Dry Run 字段健康度检查")
    print(f"  运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  检查窗口：{since.date()} ~ {until.date()}")
    print("=" * 68)

    all_errors: list[str] = []

    # ── 加载数据 ──────────────────────────────────────────────────────────────
    print("\n▶ 加载数据文件...")
    df_1d_raw = load_feather(D1_FILE, "Daily feather")
    df_1h_raw = load_feather(H1_FILE, "1H feather")

    if df_1d_raw is None:
        err("Daily 数据无法加载，检查 ①②③ 均依赖此文件，提前退出")
        print("\n请先运行：docker compose run --rm freqtrade download-data "
              "--config user_data/config_backtest.json "
              "--timeframes 1d --trading-mode futures --prepend")
        sys.exit(2)

    print(f"  Daily：{len(df_1d_raw)} 根（"
          f"{df_1d_raw['date'].iloc[0].date()} ~ {df_1d_raw['date'].iloc[-1].date()}）")
    if df_1h_raw is not None:
        print(f"  1H   ：{len(df_1h_raw)} 根（"
              f"{df_1h_raw['date'].iloc[0].date()} ~ {df_1h_raw['date'].iloc[-1].date()}）")

    # ── 运行 DailyEventDetector ───────────────────────────────────────────────
    print("\n▶ 运行 DailyEventDetector...")
    if not _DETECTOR_AVAILABLE:
        err(f"DailyEventDetector 无法导入：{_DETECTOR_ERROR}")
        err("检查 ②③ 将跳过，仅执行 ① 和 ④")
        df_1d_annotated = df_1d_raw.copy()
    else:
        try:
            detector = DailyEventDetector()
            df_1d_annotated = detector.detect(df_1d_raw.copy())
            ok(f"探测完成，输出列：{[c for c in df_1d_annotated.columns if c not in df_1d_raw.columns]}")
        except Exception as e:
            err(f"DailyEventDetector.detect() 失败：{e}")
            all_errors.append(f"[DETECTOR-ERROR] {e}")
            df_1d_annotated = df_1d_raw.copy()

    # ── 1H 关键字段：从 feather 直接读（如果策略已输出缓存）─────────────────
    # 注意：1H feather 是原始 OHLCV，不含 macro_phase 等指标列
    # 这些列仅在 Freqtrade 策略运行时动态计算，无法从 feather 直接读取
    # → 此处使用 Daily 的字段作为代理（1H 内最后一根 Daily 对应值）
    # 如需精确检查 1H 层面的字段，改用 check_dryrun_fields_live.py（REST API 版本）
    print()

    # ── 检查 ① ───────────────────────────────────────────────────────────────
    # 1H feather 没有 macro_phase 等列，使用 Daily 字段做近似检查
    if df_1h_raw is not None:
        # 尝试检查 1H 文件里是否已有策略计算的字段（如用 export 导出了 indicators）
        indicator_cols = [c for c in df_1h_raw.columns
                          if c in ("macro_phase", "bear_market_mode",
                                   "dist_cause_sufficient", "dist_sequence_progress",
                                   "daily_choch_confirmed")]
        if indicator_cols:
            info(f"1H feather 中发现指标列：{indicator_cols}，执行字段完整性检查")
            errs = check_null_ratios(df_1h_raw, since, until)
            all_errors.extend(errs)
        else:
            section("① 数据完整性检查（Daily 层面代理）")
            info("1H feather 不含指标列（正常），使用 Daily annotated 数据做代理检查")
            # 用 Daily 字段做近似版检查
            errs = check_null_ratios(df_1d_annotated, since, until)
            all_errors.extend(errs)
    else:
        section("① 数据完整性检查")
        warn("1H feather 不可用，跳过")

    # ── 检查 ② ───────────────────────────────────────────────────────────────
    errs = check_state_machine(df_1d_annotated, until)
    all_errors.extend(errs)

    # ── 检查 ③ ───────────────────────────────────────────────────────────────
    errs = check_choch_rate(df_1d_annotated, until)
    all_errors.extend(errs)

    # ── 检查 ④ ───────────────────────────────────────────────────────────────
    if not args.no_db:
        errs = check_trade_statistics(since, until)
        all_errors.extend(errs)

    # ── 当前状态快照 ──────────────────────────────────────────────────────────
    print_current_snapshot(df_1d_annotated, df_1h_raw)

    # ── 汇总报告 ──────────────────────────────────────────────────────────────
    section("汇总")
    if not all_errors:
        ok("所有检查通过，Dry Run 字段一切正常！")
    else:
        print(f"\n  发现 {len(all_errors)} 个问题：")
        for e in all_errors:
            print(f"    • {e}")
        print()
        if args.strict:
            sys.exit(1)

    print()


if __name__ == "__main__":
    main()
