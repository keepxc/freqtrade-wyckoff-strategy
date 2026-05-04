"""
验证历史 SC 事件：区分"参数过严" vs "时间框架错误"

已知的教科书级 SC 事件：
1. 2022-11-08 至 2022-11-10: FTX 崩盘
2. 2022-05-12: LUNA 崩盘
3. 2020-03-12: COVID-19 黑色星期四

目标：
- 检查这些时间段的 1H K线是否满足 SC 条件1∧2（趋势+新低）
- 检查成交量和宽幅的实际值 vs 阈值
- 判断是"阈值过严"还是"1H 图结构性无法识别"
"""

import sys
import os
sys.path.insert(0, os.path.abspath('.'))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# 已知 SC 事件
KNOWN_SC_EVENTS = [
    {
        'name': 'FTX 崩盘',
        'start': '2022-11-08',
        'end': '2022-11-10',
        'description': 'FTX 交易所崩盘，BTC 从 $21k 跌至 $15.5k'
    },
    {
        'name': 'LUNA 崩盘',
        'start': '2022-05-09',
        'end': '2022-05-13',
        'description': 'Terra/LUNA 生态崩溃，BTC 从 $40k 跌至 $26k'
    },
    {
        'name': 'COVID-19 黑色星期四',
        'start': '2020-03-12',
        'end': '2020-03-13',
        'description': 'COVID-19 恐慌，BTC 从 $8k 跌至 $3.8k'
    },
]


def load_btc_data(start_date, end_date):
    """
    加载 BTC 1H 数据
    
    Args:
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
    
    Returns:
        DataFrame with OHLCV data
    """
    # 扩展时间范围以计算指标（需要前60根K线）
    start_dt = pd.to_datetime(start_date, utc=True) - timedelta(days=10)
    end_dt = pd.to_datetime(end_date, utc=True) + timedelta(days=1)
    
    # 尝试多种数据格式
    data_paths = [
        'user_data/data/binance/futures/BTC_USDT_USDT-1h-futures.feather',
        'user_data/data/binance/BTC_USDT_USDT-1h.feather',
        'user_data/data/binance/BTC_USDT_USDT-1h.json',
    ]
    
    df = None
    for data_path in data_paths:
        if os.path.exists(data_path):
            logger.info(f"加载数据: {data_path}")
            if data_path.endswith('.feather'):
                df = pd.read_feather(data_path)
            elif data_path.endswith('.json'):
                df = pd.read_json(data_path)
                df['date'] = pd.to_datetime(df['date'], unit='ms')
            
            if 'date' in df.columns:
                df = df.set_index('date')
            break
    
    if df is None:
        logger.error(f"数据文件不存在，尝试过: {data_paths}")
        return None
    
    # 过滤时间范围
    df = df[(df.index >= start_dt) & (df.index <= end_dt)]
    
    if len(df) == 0:
        logger.warning(f"时间范围 {start_date} 至 {end_date} 无数据")
        return None
    
    return df


def calculate_sc_conditions(df):
    """
    计算 SC 检测条件
    
    Returns:
        DataFrame with SC condition columns
    """
    # 计算基础指标
    df['atr_20'] = df['high'].rolling(20).max() - df['low'].rolling(20).min()
    df['volume_sma_20'] = df['volume'].rolling(20).mean()
    df['bar_range'] = df['high'] - df['low']
    df['close_position'] = (df['close'] - df['low']) / (df['high'] - df['low'] + 1e-9)
    
    # SC 条件1：趋势前提（过去20根K线跌幅 > 5%）
    prior_close = df['close'].shift(20)
    price_drop_ratio = (prior_close - df['close']) / prior_close
    df['sc_cond1_trend'] = price_drop_ratio > 0.05
    df['price_drop_ratio'] = price_drop_ratio
    
    # SC 条件2：价格创60根K线新低
    rolling_low = df['low'].rolling(60).min().shift(1)
    df['sc_cond2_new_low'] = df['low'] <= rolling_low
    
    # SC 条件3：极高成交量（当前阈值 2.0x）
    volume_ratio = df['volume'] / df['volume_sma_20']
    df['sc_cond3_volume_2x'] = volume_ratio > 2.0
    df['sc_cond3_volume_1_5x'] = volume_ratio > 1.5
    df['volume_ratio'] = volume_ratio
    
    # SC 条件4：宽幅K线（当前阈值 1.5x）
    range_ratio = df['bar_range'] / df['atr_20']
    df['sc_cond4_range_1_5x'] = range_ratio > 1.5
    df['sc_cond4_range_1_2x'] = range_ratio > 1.2
    df['range_ratio'] = range_ratio
    
    # SC 条件5：买方接管（收盘位置 > 30%）
    df['sc_cond5_close_pos'] = df['close_position'] >= 0.30
    
    # 组合条件
    df['sc_cond_1_2'] = df['sc_cond1_trend'] & df['sc_cond2_new_low']
    df['sc_current_threshold'] = (
        df['sc_cond1_trend'] & 
        df['sc_cond2_new_low'] & 
        df['sc_cond3_volume_2x'] & 
        df['sc_cond4_range_1_5x'] & 
        df['sc_cond5_close_pos']
    )
    df['sc_relaxed_threshold'] = (
        df['sc_cond1_trend'] & 
        df['sc_cond2_new_low'] & 
        df['sc_cond3_volume_1_5x'] & 
        df['sc_cond4_range_1_2x'] & 
        df['sc_cond5_close_pos']
    )
    
    return df


def analyze_event(event):
    """
    分析单个 SC 事件
    
    Args:
        event: dict with 'name', 'start', 'end', 'description'
    
    Returns:
        dict with analysis results
    """
    logger.info("=" * 80)
    logger.info(f"分析事件: {event['name']}")
    logger.info(f"时间范围: {event['start']} 至 {event['end']}")
    logger.info(f"描述: {event['description']}")
    logger.info("=" * 80)
    
    # 加载数据
    df = load_btc_data(event['start'], event['end'])
    if df is None:
        return None
    
    # 计算条件
    df = calculate_sc_conditions(df)
    
    # 过滤到事件时间范围
    event_start = pd.to_datetime(event['start'], utc=True)
    event_end = pd.to_datetime(event['end'], utc=True) + timedelta(days=1)
    event_df = df[(df.index >= event_start) & (df.index < event_end)]
    
    if len(event_df) == 0:
        logger.warning("事件时间范围内无数据")
        return None
    
    # 统计结果
    total_bars = len(event_df)
    
    results = {
        'event_name': event['name'],
        'total_bars': total_bars,
        'cond1_trend': int(event_df['sc_cond1_trend'].sum()),
        'cond2_new_low': int(event_df['sc_cond2_new_low'].sum()),
        'cond_1_2': int(event_df['sc_cond_1_2'].sum()),
        'cond3_volume_2x': int(event_df['sc_cond3_volume_2x'].sum()),
        'cond3_volume_1_5x': int(event_df['sc_cond3_volume_1_5x'].sum()),
        'cond4_range_1_5x': int(event_df['sc_cond4_range_1_5x'].sum()),
        'cond4_range_1_2x': int(event_df['sc_cond4_range_1_2x'].sum()),
        'cond5_close_pos': int(event_df['sc_cond5_close_pos'].sum()),
        'sc_current': int(event_df['sc_current_threshold'].sum()),
        'sc_relaxed': int(event_df['sc_relaxed_threshold'].sum()),
    }
    
    # 输出统计
    logger.info(f"\n事件期间共 {total_bars} 根 1H K线")
    logger.info(f"  条件1 [趋势前提 跌幅>5%]: {results['cond1_trend']}/{total_bars} ({results['cond1_trend']/total_bars*100:.1f}%)")
    logger.info(f"  条件2 [新低突破 60根]: {results['cond2_new_low']}/{total_bars} ({results['cond2_new_low']/total_bars*100:.1f}%)")
    logger.info(f"  条件1∧2: {results['cond_1_2']}/{total_bars} ({results['cond_1_2']/total_bars*100:.1f}%)")
    logger.info(f"")
    logger.info(f"  条件3 [成交量>2.0x]: {results['cond3_volume_2x']}/{total_bars} ({results['cond3_volume_2x']/total_bars*100:.1f}%)")
    logger.info(f"  条件3 [成交量>1.5x]: {results['cond3_volume_1_5x']}/{total_bars} ({results['cond3_volume_1_5x']/total_bars*100:.1f}%)")
    logger.info(f"  条件4 [宽幅>1.5x ATR]: {results['cond4_range_1_5x']}/{total_bars} ({results['cond4_range_1_5x']/total_bars*100:.1f}%)")
    logger.info(f"  条件4 [宽幅>1.2x ATR]: {results['cond4_range_1_2x']}/{total_bars} ({results['cond4_range_1_2x']/total_bars*100:.1f}%)")
    logger.info(f"  条件5 [收盘位置>=30%]: {results['cond5_close_pos']}/{total_bars} ({results['cond5_close_pos']/total_bars*100:.1f}%)")
    logger.info(f"")
    logger.info(f"  ✅ SC检测（当前阈值 2.0x/1.5x）: {results['sc_current']}/{total_bars}")
    logger.info(f"  ✅ SC检测（放宽阈值 1.5x/1.2x）: {results['sc_relaxed']}/{total_bars}")
    
    # 找出满足条件1∧2的K线，查看它们的成交量和宽幅
    cond_1_2_bars = event_df[event_df['sc_cond_1_2']]
    if len(cond_1_2_bars) > 0:
        logger.info(f"\n满足条件1∧2的K线详情（共 {len(cond_1_2_bars)} 根）:")
        for idx, row in cond_1_2_bars.iterrows():
            logger.info(f"  {idx}: 跌幅={row['price_drop_ratio']:.2%}, 成交量={row['volume_ratio']:.2f}x, 宽幅={row['range_ratio']:.2f}x, 收盘位置={row['close_position']:.2%}")
    else:
        logger.info(f"\n⚠️ 没有K线满足条件1∧2（趋势+新低）")
    
    # 找出成交量和宽幅的峰值
    logger.info(f"\n事件期间成交量和宽幅统计:")
    logger.info(f"  成交量倍数: min={event_df['volume_ratio'].min():.2f}x, max={event_df['volume_ratio'].max():.2f}x, mean={event_df['volume_ratio'].mean():.2f}x")
    logger.info(f"  宽幅倍数: min={event_df['range_ratio'].min():.2f}x, max={event_df['range_ratio'].max():.2f}x, mean={event_df['range_ratio'].mean():.2f}x")
    
    # 找出成交量最大的3根K线
    top_volume = event_df.nlargest(3, 'volume_ratio')
    logger.info(f"\n成交量最大的3根K线:")
    for idx, row in top_volume.iterrows():
        logger.info(f"  {idx}: 成交量={row['volume_ratio']:.2f}x, 宽幅={row['range_ratio']:.2f}x, 跌幅={row['price_drop_ratio']:.2%}, 满足1∧2={row['sc_cond_1_2']}")
    
    return results


def main():
    """主函数"""
    logger.info("开始验证历史 SC 事件")
    logger.info("目标：区分'参数过严' vs '时间框架错误'\n")
    
    all_results = []
    
    for event in KNOWN_SC_EVENTS:
        result = analyze_event(event)
        if result:
            all_results.append(result)
        logger.info("\n")
    
    # 汇总结论
    logger.info("=" * 80)
    logger.info("汇总结论")
    logger.info("=" * 80)
    
    for result in all_results:
        logger.info(f"\n{result['event_name']}:")
        logger.info(f"  条件1∧2通过: {result['cond_1_2']}/{result['total_bars']} 根K线")
        logger.info(f"  当前阈值检出: {result['sc_current']} 根")
        logger.info(f"  放宽阈值检出: {result['sc_relaxed']} 根")
        
        if result['cond_1_2'] == 0:
            logger.info(f"  ❌ 判断: 1H 图在结构上无法识别此事件（连条件1∧2都不满足）")
        elif result['sc_current'] == 0 and result['sc_relaxed'] > 0:
            logger.info(f"  ⚠️ 判断: 阈值过严（放宽后可检出）")
        elif result['sc_current'] > 0:
            logger.info(f"  ✅ 判断: 当前阈值可检出")
        else:
            logger.info(f"  ❌ 判断: 即使放宽阈值也无法检出")
    
    # 最终建议
    logger.info("\n" + "=" * 80)
    logger.info("最终建议")
    logger.info("=" * 80)
    
    total_events = len(all_results)
    cond_1_2_pass = sum(1 for r in all_results if r['cond_1_2'] > 0)
    current_detect = sum(1 for r in all_results if r['sc_current'] > 0)
    relaxed_detect = sum(1 for r in all_results if r['sc_relaxed'] > 0)
    
    logger.info(f"共验证 {total_events} 个历史 SC 事件:")
    logger.info(f"  条件1∧2通过: {cond_1_2_pass}/{total_events} 个事件")
    logger.info(f"  当前阈值检出: {current_detect}/{total_events} 个事件")
    logger.info(f"  放宽阈值检出: {relaxed_detect}/{total_events} 个事件")
    
    if cond_1_2_pass == 0:
        logger.info("\n🔴 结论: 时间框架错误")
        logger.info("  1H 图在结构上无法识别教科书级 SC 事件")
        logger.info("  建议: 在日线图上做 Wyckoff 检测，1H 图只做执行")
    elif cond_1_2_pass > 0 and current_detect == 0 and relaxed_detect > 0:
        logger.info("\n🟡 结论: 参数过严")
        logger.info("  放宽阈值后可以检出历史 SC 事件")
        logger.info("  建议: 降低成交量和宽幅阈值（1.5x / 1.2x）")
    elif current_detect > 0:
        logger.info("\n🟢 结论: 当前阈值合理")
        logger.info("  当前阈值可以检出部分历史 SC 事件")
        logger.info("  建议: 保持当前阈值，或微调以提高检出率")
    else:
        logger.info("\n🔴 结论: 1H 图不适合 SC 检测")
        logger.info("  即使放宽阈值也无法检出历史 SC 事件")
        logger.info("  建议: 切换到日线图做 Wyckoff 检测")


if __name__ == '__main__':
    main()
