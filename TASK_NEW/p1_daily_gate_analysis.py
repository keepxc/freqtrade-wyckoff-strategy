import argparse
import importlib
import json
import logging
import os
import sys
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from freqtrade.configuration import Configuration
from freqtrade.configuration.timerange import TimeRange
from freqtrade.data.converter import trim_dataframe
from freqtrade.data.dataprovider import DataProvider
from freqtrade.data.history import load_data
from freqtrade.enums import RunMode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_BINS = [0.5, 1.0, 1.25, 1.5, 2.0, 2.5, 3.01]
DEFAULT_LABELS = [
    "0.50-0.99",
    "1.00-1.24",
    "1.25-1.49",
    "1.50-1.99",
    "2.00-2.49",
    "2.50-3.00",
]


@dataclass
class RunParams:
    config_path: str
    timerange: str
    pair: str
    gate_mode: str
    gate_k: float
    tr_conflict_score: float
    tr_window_main: int
    tr_window_fast: int
    tr_window_slow: int
    tr_use_slow_check: bool
    tr_min_overlap: float
    weak_threshold: float = 1.6


class StaticPairlists:
    def __init__(self, whitelist: list[str]):
        self.whitelist = whitelist


@contextmanager
def temporary_env(env_updates: dict[str, str]):
    original: dict[str, str | None] = {}
    for key, value in env_updates.items():
        original[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def build_env(params: RunParams) -> dict[str, str]:
    return {
        "WY_P1_USE_DAILY_GATE": "1",
        "WY_P1_DAILY_GATE_MODE": params.gate_mode,
        "WY_P1_DAILY_GATE_K": str(params.gate_k),
        "WY_P1_DAILY_TR_CONFLICT_SCORE": str(params.tr_conflict_score),
        "WY_P1_DAILY_TR_WINDOW_MAIN": str(params.tr_window_main),
        "WY_P1_DAILY_TR_WINDOW_FAST": str(params.tr_window_fast),
        "WY_P1_DAILY_TR_WINDOW_SLOW": str(params.tr_window_slow),
        "WY_P1_DAILY_TR_USE_SLOW_CHECK": "1" if params.tr_use_slow_check else "0",
        "WY_P1_DAILY_TR_MIN_OVERLAP": str(params.tr_min_overlap),
        "WY_P1_DAILY_GATE_WEAK_THRESHOLD": str(params.weak_threshold),
    }


def load_strategy_dataframe(params: RunParams) -> pd.DataFrame:
    args = {
        "config": [params.config_path],
        "strategy": "WyckoffV7_TrueMethod",
        "timerange": params.timerange,
        "user_data_dir": "user_data",
        "datadir": "user_data/data/binance",
        "pairs": [params.pair],
        "command": "backtesting",
        "export": "none",
    }

    with temporary_env(build_env(params)):
        strategy_module = importlib.import_module("user_data.strategies.WyckoffV7_TrueMethod")
        strategy_module = importlib.reload(strategy_module)
        strategy_cls = strategy_module.WyckoffV7_TrueMethod
        config = Configuration(args, RunMode.BACKTEST).get_config()
        strategy = strategy_cls(config)
        config["startup_candle_count"] = strategy.startup_candle_count
        dp = DataProvider(config, exchange=None, pairlists=StaticPairlists([params.pair]))
        strategy.dp = dp

        timerange = TimeRange.parse_timerange(config["timerange"])
        data = load_data(
            datadir=config["datadir"],
            pairs=[params.pair],
            timeframe=config["timeframe"],
            timerange=timerange,
            startup_candles=strategy.startup_candle_count,
            fail_without_data=True,
            data_format=config["dataformat_ohlcv"],
            candle_type=config.get("candle_type_def"),
        )
        preprocessed = strategy.advise_all_indicators(data)
        dataframe = strategy.ft_advise_signals(
            preprocessed[params.pair].copy(),
            {"pair": params.pair},
        )
        dataframe = trim_dataframe(
            dataframe,
            timerange,
            startup_candles=strategy.startup_candle_count,
        ).reset_index(drop=True)
        dataframe.attrs["p1_params"] = params.__dict__.copy()
        return dataframe


def bucket_signal_quality(series: pd.Series) -> dict[str, int]:
    if series.empty:
        return {label: 0 for label in DEFAULT_LABELS}
    buckets = pd.cut(
        series.astype(float),
        bins=DEFAULT_BINS,
        labels=DEFAULT_LABELS,
        include_lowest=True,
        right=False,
    )
    counts = buckets.value_counts(sort=False)
    return {label: int(counts.get(label, 0)) for label in DEFAULT_LABELS}


def summarize_gate_distribution(series: pd.Series) -> dict[str, int]:
    counts = series.astype(float).round(3).value_counts(dropna=False).sort_index()
    return {f"{key:.1f}": int(value) for key, value in counts.items()}


def summarize_run(name: str, dataframe: pd.DataFrame) -> dict[str, Any]:
    entry_mask = dataframe.get("enter_long", pd.Series(0, index=dataframe.index)).fillna(0).astype(int) == 1
    gate_score = dataframe["daily_gate_score"].astype(float)
    tr_exists = dataframe["daily_tr_exists"].fillna(False).astype(bool)
    entry_gate = gate_score[entry_mask]
    entry_signal_quality = dataframe.loc[entry_mask, "signal_quality"].astype(float)
    params = dataframe.attrs.get("p1_params", {})
    conflict_score = float(params.get("tr_conflict_score", 0.3))
    conflict_mask = gate_score.round(3) == round(conflict_score, 3)

    summary = {
        "name": name,
        "bar_count": int(len(dataframe)),
        "entry_signal_count": int(entry_mask.sum()),
        "daily_tr_exists": {
            "count": int(tr_exists.sum()),
            "ratio": round(float(tr_exists.mean()), 4),
        },
        "gate_score_distribution_all": summarize_gate_distribution(gate_score),
        "gate_score_distribution_entry": summarize_gate_distribution(entry_gate),
        "conflict_ratio_all": round(float(conflict_mask.mean()), 4),
        "conflict_ratio_within_tr": round(
            float(conflict_mask[tr_exists].mean()) if tr_exists.any() else 0.0,
            4,
        ),
        "signal_quality_all": {
            "mean": round(float(dataframe["signal_quality"].mean()), 4),
            "median": round(float(dataframe["signal_quality"].median()), 4),
            "bucket_counts": bucket_signal_quality(dataframe["signal_quality"]),
        },
        "signal_quality_entry": {
            "mean": round(float(entry_signal_quality.mean()), 4) if not entry_signal_quality.empty else 0.0,
            "median": round(float(entry_signal_quality.median()), 4) if not entry_signal_quality.empty else 0.0,
            "bucket_counts": bucket_signal_quality(entry_signal_quality),
        },
    }
    return summary


def compare_off_vs_soft(off_df: pd.DataFrame, soft_df: pd.DataFrame) -> dict[str, Any]:
    off_entry = off_df.get("enter_long", pd.Series(0, index=off_df.index)).fillna(0).astype(int) == 1
    soft_entry = soft_df.get("enter_long", pd.Series(0, index=soft_df.index)).fillna(0).astype(int) == 1
    if not off_entry.equals(soft_entry):
        raise RuntimeError("off/soft entry masks differ, cannot compare signal_quality injection cleanly.")

    off_quality = off_df.loc[off_entry, "signal_quality"].astype(float)
    soft_quality = soft_df.loc[soft_entry, "signal_quality"].astype(float)
    delta = soft_quality - off_quality

    return {
        "entry_signal_count": int(off_entry.sum()),
        "boosted_count": int((delta > 1e-9).sum()),
        "penalized_count": int((delta < -1e-9).sum()),
        "unchanged_count": int((delta.abs() <= 1e-9).sum()),
        "avg_delta": round(float(delta.mean()), 4) if not delta.empty else 0.0,
        "median_delta": round(float(delta.median()), 4) if not delta.empty else 0.0,
        "off_entry_signal_quality": {
            "mean": round(float(off_quality.mean()), 4) if not off_quality.empty else 0.0,
            "median": round(float(off_quality.median()), 4) if not off_quality.empty else 0.0,
            "bucket_counts": bucket_signal_quality(off_quality),
        },
        "soft_entry_signal_quality": {
            "mean": round(float(soft_quality.mean()), 4) if not soft_quality.empty else 0.0,
            "median": round(float(soft_quality.median()), 4) if not soft_quality.empty else 0.0,
            "bucket_counts": bucket_signal_quality(soft_quality),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P1 daily gate diagnostic analysis")
    parser.add_argument("--config", default="user_data/config_backtest.json")
    parser.add_argument("--timerange", default="20230101-20260228")
    parser.add_argument("--pair", default="BTC/USDT:USDT")
    parser.add_argument("--gate-k", type=float, default=0.25)
    parser.add_argument("--tr-conflict-score", type=float, default=0.30)
    parser.add_argument("--tr-window-main", type=int, default=60)
    parser.add_argument("--tr-window-fast", type=int, default=20)
    parser.add_argument("--tr-window-slow", type=int, default=120)
    parser.add_argument("--tr-use-slow-check", type=int, choices=[0, 1], default=1)
    parser.add_argument("--tr-min-overlap", type=float, default=0.65)
    parser.add_argument("--gate-weak-threshold", type=float, default=1.6)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)
    logging.disable(logging.CRITICAL)

    args = parse_args()
    base = RunParams(
        config_path=args.config,
        timerange=args.timerange,
        pair=args.pair,
        gate_mode="off",
        gate_k=args.gate_k,
        tr_conflict_score=args.tr_conflict_score,
        tr_window_main=args.tr_window_main,
        tr_window_fast=args.tr_window_fast,
        tr_window_slow=args.tr_window_slow,
        tr_use_slow_check=bool(args.tr_use_slow_check),
        tr_min_overlap=args.tr_min_overlap,
        weak_threshold=args.gate_weak_threshold,
    )
    soft = RunParams(**{**base.__dict__, "gate_mode": "soft"})

    off_df = load_strategy_dataframe(base)
    soft_df = load_strategy_dataframe(soft)

    result = {
        "params": {
            "pair": args.pair,
            "timerange": args.timerange,
            "gate_k": args.gate_k,
            "tr_conflict_score": args.tr_conflict_score,
            "tr_window_main": args.tr_window_main,
            "tr_window_fast": args.tr_window_fast,
            "tr_window_slow": args.tr_window_slow,
            "tr_use_slow_check": bool(args.tr_use_slow_check),
            "tr_min_overlap": args.tr_min_overlap,
            "weak_signal_threshold": args.gate_weak_threshold,
            "clip": [0.5, 3.0],
        },
        "off": summarize_run("off", off_df),
        "soft": summarize_run("soft", soft_df),
        "soft_vs_off": compare_off_vs_soft(off_df, soft_df),
    }

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
