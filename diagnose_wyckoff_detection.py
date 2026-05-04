"""
诊断脚本：为什么 Wyckoff 结构检测从未触发？

目标：
1. 在 SC/BC 检测的每个条件判断处加日志，确认代码走到哪一步停住
2. 打印实际候选值 vs 阈值，确认是"完全没进入检测循环"还是"进入了但每次都差一点"
3. 在 PnF target 计算处加日志，确认 TR 来源路径

使用方法：
1. 运行此脚本生成带诊断日志的临时检测器
2. 在策略中临时替换原检测器
3. 运行回测，查看日志输出
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.abspath('.'))

import pandas as pd
import numpy as np
from pandas import DataFrame
import logging

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def diagnose_climatic_detection(dataframe: DataFrame) -> dict:
    """
    诊断 SC/BC 检测逻辑，返回详细统计
    
    Args:
        dataframe: 包含 OHLCV + atr_20 + volume_sma_20 + close_position + bar_range 的 DataFrame
    
    Returns:
        dict: 包含各条件的通过率和候选值统计
    """
    
    # ========================================================================
    # SC 检测诊断
    # ========================================================================
    logger.info("=" * 80)
    logger.info("开始诊断 SC (Selling Climax) 检测")
    logger.info("=" * 80)
    
    sc_lookback = 60
    sc_trend_bars = 20
    sc_trend_min_drop = 0.05
    sc_volume_mult = 2.0
    sc_atr_mult = 1.5
    sc_close_pos_min = 0.30
    
    # 条件1：趋势前提（过去N根K线存在跌幅）
    prior_close = dataframe['close'].shift(sc_trend_bars)
    price_drop_ratio = (prior_close - dataframe['close']) / prior_close
    downtrend_exists = price_drop_ratio > sc_trend_min_drop
    
    cond1_pass = downtrend_exists.sum()
    cond1_total = len(dataframe)
    logger.info(f"SC 条件1 [趋势前提]: {cond1_pass}/{cond1_total} ({cond1_pass/cond1_total*100:.2f}%)")
    logger.info(f"  - 阈值: 跌幅 > {sc_trend_min_drop*100:.1f}%")
    logger.info(f"  - 实际跌幅统计: min={price_drop_ratio.min():.4f}, max={price_drop_ratio.max():.4f}, mean={price_drop_ratio.mean():.4f}")
    logger.info(f"  - 满足条件的最大跌幅: {price_drop_ratio[downtrend_exists].max() if cond1_pass > 0 else 'N/A'}")
    
    # 条件2：价格创60根K线新低
    rolling_low = dataframe['low'].rolling(sc_lookback).min().shift(1)
    new_n_bar_low = dataframe['low'] <= rolling_low
    
    cond2_pass = new_n_bar_low.sum()
    logger.info(f"SC 条件2 [新低突破]: {cond2_pass}/{cond1_total} ({cond2_pass/cond1_total*100:.2f}%)")
    logger.info(f"  - 阈值: 当前 low <= 过去{sc_lookback}根最低点")
    
    # 条件3：极高成交量
    volume_ratio = dataframe['volume'] / dataframe['volume_sma_20']
    high_volume = volume_ratio > sc_volume_mult
    
    cond3_pass = high_volume.sum()
    logger.info(f"SC 条件3 [极高成交量]: {cond3_pass}/{cond1_total} ({cond3_pass/cond1_total*100:.2f}%)")
    logger.info(f"  - 阈值: volume > 20周期均量 × {sc_volume_mult}")
    logger.info(f"  - 实际倍数统计: min={volume_ratio.min():.2f}, max={volume_ratio.max():.2f}, mean={volume_ratio.mean():.2f}")
    logger.info(f"  - 满足条件的最大倍数: {volume_ratio[high_volume].max() if cond3_pass > 0 else 'N/A'}")
    
    # 条件4：宽幅K线
    range_ratio = dataframe['bar_range'] / dataframe['atr_20']
    wide_range = range_ratio > sc_atr_mult
    
    cond4_pass = wide_range.sum()
    logger.info(f"SC 条件4 [宽幅K线]: {cond4_pass}/{cond1_total} ({cond4_pass/cond1_total*100:.2f}%)")
    logger.info(f"  - 阈值: bar_range > ATR × {sc_atr_mult}")
    logger.info(f"  - 实际倍数统计: min={range_ratio.min():.2f}, max={range_ratio.max():.2f}, mean={range_ratio.mean():.2f}")
    logger.info(f"  - 满足条件的最大倍数: {range_ratio[wide_range].max() if cond4_pass > 0 else 'N/A'}")
    
    # 条件5：买方接管（收盘位置 > 30%）
    buyer_takeover = dataframe['close_position'] >= sc_close_pos_min
    
    cond5_pass = buyer_takeover.sum()
    logger.info(f"SC 条件5 [买方接管]: {cond5_pass}/{cond1_total} ({cond5_pass/cond1_total*100:.2f}%)")
    logger.info(f"  - 阈值: close_position >= {sc_close_pos_min*100:.0f}%")
    logger.info(f"  - 实际位置统计: min={dataframe['close_position'].min():.2f}, max={dataframe['close_position'].max():.2f}, mean={dataframe['close_position'].mean():.2f}")
    
    # 综合判断
    sc_detected = (
        downtrend_exists &
        new_n_bar_low &
        high_volume &
        wide_range &
        buyer_takeover
    )
    
    sc_total = sc_detected.sum()
    logger.info(f"\n✅ SC 综合检测结果: {sc_total}/{cond1_total} ({sc_total/cond1_total*100:.4f}%)")
    
    # 逐步组合分析
    comb_1_2 = (downtrend_exists & new_n_bar_low).sum()
    comb_1_2_3 = (downtrend_exists & new_n_bar_low & high_volume).sum()
    comb_1_2_3_4 = (downtrend_exists & new_n_bar_low & high_volume & wide_range).sum()
    
    logger.info(f"\n逐步组合分析:")
    logger.info(f"  条件1 AND 条件2: {comb_1_2} ({comb_1_2/cond1_total*100:.2f}%)")
    logger.info(f"  条件1 AND 条件2 AND 条件3: {comb_1_2_3} ({comb_1_2_3/cond1_total*100:.2f}%)")
    logger.info(f"  条件1 AND 条件2 AND 条件3 AND 条件4: {comb_1_2_3_4} ({comb_1_2_3_4/cond1_total*100:.2f}%)")
    logger.info(f"  全部条件: {sc_total} ({sc_total/cond1_total*100:.4f}%)")
    
    # ========================================================================
    # BC 检测诊断
    # ========================================================================
    logger.info("\n" + "=" * 80)
    logger.info("开始诊断 BC (Buying Climax) 检测")
    logger.info("=" * 80)
    
    bc_lookback = 60
    bc_trend_bars = 20
    bc_trend_min_rise = 0.05
    bc_volume_mult = 2.0
    bc_atr_mult = 1.5
    bc_close_pos_max = 0.70
    
    # 条件1：趋势前提
    prior_close_bc = dataframe['close'].shift(bc_trend_bars)
    price_rise_ratio = (dataframe['close'] - prior_close_bc) / prior_close_bc
    uptrend_exists = price_rise_ratio > bc_trend_min_rise
    
    bc_cond1_pass = uptrend_exists.sum()
    logger.info(f"BC 条件1 [趋势前提]: {bc_cond1_pass}/{cond1_total} ({bc_cond1_pass/cond1_total*100:.2f}%)")
    logger.info(f"  - 阈值: 涨幅 > {bc_trend_min_rise*100:.1f}%")
    logger.info(f"  - 实际涨幅统计: min={price_rise_ratio.min():.4f}, max={price_rise_ratio.max():.4f}, mean={price_rise_ratio.mean():.4f}")
    logger.info(f"  - 满足条件的最大涨幅: {price_rise_ratio[uptrend_exists].max() if bc_cond1_pass > 0 else 'N/A'}")
    
    # 条件2：价格创60根K线新高
    rolling_high = dataframe['high'].rolling(bc_lookback).max().shift(1)
    new_n_bar_high = dataframe['high'] >= rolling_high
    
    bc_cond2_pass = new_n_bar_high.sum()
    logger.info(f"BC 条件2 [新高突破]: {bc_cond2_pass}/{cond1_total} ({bc_cond2_pass/cond1_total*100:.2f}%)")
    
    # 条件3：极高成交量（与SC相同）
    bc_cond3_pass = high_volume.sum()
    logger.info(f"BC 条件3 [极高成交量]: {bc_cond3_pass}/{cond1_total} ({bc_cond3_pass/cond1_total*100:.2f}%)")
    
    # 条件4：宽幅K线（与SC相同）
    bc_cond4_pass = wide_range.sum()
    logger.info(f"BC 条件4 [宽幅K线]: {bc_cond4_pass}/{cond1_total} ({bc_cond4_pass/cond1_total*100:.2f}%)")
    
    # 条件5：卖方接管（收盘位置 < 70%）
    seller_takeover = dataframe['close_position'] <= bc_close_pos_max
    
    bc_cond5_pass = seller_takeover.sum()
    logger.info(f"BC 条件5 [卖方接管]: {bc_cond5_pass}/{cond1_total} ({bc_cond5_pass/cond1_total*100:.2f}%)")
    logger.info(f"  - 阈值: close_position <= {bc_close_pos_max*100:.0f}%")
    
    # 综合判断
    bc_detected = (
        uptrend_exists &
        new_n_bar_high &
        high_volume &
        wide_range &
        seller_takeover
    )
    
    bc_total = bc_detected.sum()
    logger.info(f"\n✅ BC 综合检测结果: {bc_total}/{cond1_total} ({bc_total/cond1_total*100:.4f}%)")
    
    # 逐步组合分析
    bc_comb_1_2 = (uptrend_exists & new_n_bar_high).sum()
    bc_comb_1_2_3 = (uptrend_exists & new_n_bar_high & high_volume).sum()
    bc_comb_1_2_3_4 = (uptrend_exists & new_n_bar_high & high_volume & wide_range).sum()
    
    logger.info(f"\n逐步组合分析:")
    logger.info(f"  条件1 AND 条件2: {bc_comb_1_2} ({bc_comb_1_2/cond1_total*100:.2f}%)")
    logger.info(f"  条件1 AND 条件2 AND 条件3: {bc_comb_1_2_3} ({bc_comb_1_2_3/cond1_total*100:.2f}%)")
    logger.info(f"  条件1 AND 条件2 AND 条件3 AND 条件4: {bc_comb_1_2_3_4} ({bc_comb_1_2_3_4/cond1_total*100:.2f}%)")
    logger.info(f"  全部条件: {bc_total} ({bc_total/cond1_total*100:.4f}%)")
    
    # ========================================================================
    # 寻找"接近但未触发"的候选K线
    # ========================================================================
    logger.info("\n" + "=" * 80)
    logger.info("寻找接近 SC 条件但未触发的候选K线（Top 10）")
    logger.info("=" * 80)
    
    # 计算每根K线满足的条件数
    sc_score = (
        downtrend_exists.astype(int) +
        new_n_bar_low.astype(int) +
        high_volume.astype(int) +
        wide_range.astype(int) +
        buyer_takeover.astype(int)
    )
    
    # 找出满足4个条件的K线（差一个就能触发）
    near_miss_sc = dataframe[sc_score == 4].copy()
    if len(near_miss_sc) > 0:
        near_miss_sc['sc_score'] = 4
        near_miss_sc['missing_cond'] = ''
        
        for idx in near_miss_sc.index:
            missing = []
            if not downtrend_exists.loc[idx]:
                missing.append(f"趋势(实际跌幅={price_drop_ratio.loc[idx]:.2%})")
            if not new_n_bar_low.loc[idx]:
                missing.append("新低")
            if not high_volume.loc[idx]:
                missing.append(f"成交量(实际={volume_ratio.loc[idx]:.2f}x)")
            if not wide_range.loc[idx]:
                missing.append(f"宽幅(实际={range_ratio.loc[idx]:.2f}x)")
            if not buyer_takeover.loc[idx]:
                missing.append(f"收盘位置(实际={dataframe.loc[idx, 'close_position']:.2%})")
            near_miss_sc.loc[idx, 'missing_cond'] = ', '.join(missing)
        
        logger.info(f"找到 {len(near_miss_sc)} 根K线满足4个SC条件（差1个）:")
        for i, (idx, row) in enumerate(near_miss_sc.head(10).iterrows()):
            logger.info(f"  #{i+1} 索引={idx}, 缺失条件: {row['missing_cond']}")
    else:
        logger.info("未找到满足4个SC条件的K线")
        
        # 尝试找满足3个条件的
        near_miss_sc_3 = dataframe[sc_score == 3].copy()
        if len(near_miss_sc_3) > 0:
            logger.info(f"找到 {len(near_miss_sc_3)} 根K线满足3个SC条件（差2个）")
        else:
            logger.info("甚至没有满足3个SC条件的K线")
    
    logger.info("\n" + "=" * 80)
    logger.info("寻找接近 BC 条件但未触发的候选K线（Top 10）")
    logger.info("=" * 80)
    
    bc_score = (
        uptrend_exists.astype(int) +
        new_n_bar_high.astype(int) +
        high_volume.astype(int) +
        wide_range.astype(int) +
        seller_takeover.astype(int)
    )
    
    near_miss_bc = dataframe[bc_score == 4].copy()
    if len(near_miss_bc) > 0:
        near_miss_bc['bc_score'] = 4
        near_miss_bc['missing_cond'] = ''
        
        for idx in near_miss_bc.index:
            missing = []
            if not uptrend_exists.loc[idx]:
                missing.append(f"趋势(实际涨幅={price_rise_ratio.loc[idx]:.2%})")
            if not new_n_bar_high.loc[idx]:
                missing.append("新高")
            if not high_volume.loc[idx]:
                missing.append(f"成交量(实际={volume_ratio.loc[idx]:.2f}x)")
            if not wide_range.loc[idx]:
                missing.append(f"宽幅(实际={range_ratio.loc[idx]:.2f}x)")
            if not seller_takeover.loc[idx]:
                missing.append(f"收盘位置(实际={dataframe.loc[idx, 'close_position']:.2%})")
            near_miss_bc.loc[idx, 'missing_cond'] = ', '.join(missing)
        
        logger.info(f"找到 {len(near_miss_bc)} 根K线满足4个BC条件（差1个）:")
        for i, (idx, row) in enumerate(near_miss_bc.head(10).iterrows()):
            logger.info(f"  #{i+1} 索引={idx}, 缺失条件: {row['missing_cond']}")
    else:
        logger.info("未找到满足4个BC条件的K线")
        
        near_miss_bc_3 = dataframe[bc_score == 3].copy()
        if len(near_miss_bc_3) > 0:
            logger.info(f"找到 {len(near_miss_bc_3)} 根K线满足3个BC条件（差2个）")
        else:
            logger.info("甚至没有满足3个BC条件的K线")
    
    # 返回统计结果
    return {
        'sc_detected': sc_total,
        'bc_detected': bc_total,
        'sc_near_miss_4': len(near_miss_sc) if len(near_miss_sc) > 0 else 0,
        'bc_near_miss_4': len(near_miss_bc) if len(near_miss_bc) > 0 else 0,
        'volume_max_ratio': volume_ratio.max(),
        'range_max_ratio': range_ratio.max(),
    }


if __name__ == '__main__':
    print("诊断脚本已准备就绪")
    print("\n使用方法:")
    print("1. 从回测日志中提取一段数据到 DataFrame")
    print("2. 调用 diagnose_climatic_detection(dataframe)")
    print("3. 查看详细的条件通过率和候选值统计")
    print("\n或者：将此脚本集成到策略的 populate_indicators 中临时运行")
