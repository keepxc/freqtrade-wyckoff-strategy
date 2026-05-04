#!/bin/bash
# 检查 P&F 目标位元数据是否被正确保存
# 用于验证 2026-05-04 的 leverage() 修复

echo "=========================================="
echo "P&F 目标位元数据日志检查"
echo "=========================================="
echo ""

LOG_FILE="${1:-dryrun.log}"

if [ ! -f "$LOG_FILE" ]; then
    echo "❌ 日志文件不存在: $LOG_FILE"
    echo "用法: $0 [日志文件路径]"
    echo "示例: $0 dryrun.log"
    exit 1
fi

echo "📁 日志文件: $LOG_FILE"
echo ""

echo "1️⃣ 检查 P&F 目标位锁定（新修复）"
echo "-------------------------------------------"
PNF_LOCKED=$(grep "✅ P&F 目标位已锁定" "$LOG_FILE" | wc -l)
echo "找到 $PNF_LOCKED 条记录"
if [ $PNF_LOCKED -gt 0 ]; then
    echo "✅ 修复生效！最近 3 条："
    grep "✅ P&F 目标位已锁定" "$LOG_FILE" | tail -3
else
    echo "⚠️  未找到锁定记录（可能还没有新入场）"
fi
echo ""

echo "2️⃣ 检查 P&F 目标位提取失败"
echo "-------------------------------------------"
PNF_FAILED=$(grep "❌ P&F 目标位提取失败" "$LOG_FILE" | wc -l)
echo "找到 $PNF_FAILED 条记录"
if [ $PNF_FAILED -gt 0 ]; then
    echo "❌ 有提取失败记录："
    grep "❌ P&F 目标位提取失败" "$LOG_FILE" | tail -3
fi
echo ""

echo "3️⃣ 检查旧的 PnF 目标位已存储（confirm_trade_entry 路径）"
echo "-------------------------------------------"
OLD_STORED=$(grep "PnF 目标位已存储" "$LOG_FILE" | wc -l)
echo "找到 $OLD_STORED 条记录"
if [ $OLD_STORED -gt 0 ]; then
    echo "ℹ️  confirm_trade_entry 被调用了（意外）："
    grep "PnF 目标位已存储" "$LOG_FILE" | tail -3
else
    echo "✅ 确认 confirm_trade_entry 未被调用（符合预期）"
fi
echo ""

echo "4️⃣ 检查 SOS 元数据存储"
echo "-------------------------------------------"
SOS_STORED=$(grep "SOS 元数据已存储" "$LOG_FILE" | wc -l)
echo "找到 $SOS_STORED 条记录"
if [ $SOS_STORED -gt 0 ]; then
    echo "ℹ️  SOS 交易元数据："
    grep "SOS 元数据已存储" "$LOG_FILE" | tail -3
else
    echo "ℹ️  无 SOS 交易（正常）"
fi
echo ""

echo "5️⃣ 检查 P&F 退出（元数据链）"
echo "-------------------------------------------"
PNF_EXIT=$(grep "\[元数据链\]" "$LOG_FILE" | wc -l)
echo "找到 $PNF_EXIT 条记录"
if [ $PNF_EXIT -gt 0 ]; then
    echo "✅ 有 P&F 退出使用元数据链："
    grep "\[元数据链\]" "$LOG_FILE" | tail -3
else
    echo "ℹ️  暂无 P&F 退出（等待中）"
fi
echo ""

echo "=========================================="
echo "总结"
echo "=========================================="
if [ $PNF_LOCKED -gt 0 ]; then
    echo "✅ 修复已生效：P&F 目标位在 leverage() 中被锁定"
    if [ $PNF_EXIT -gt 0 ]; then
        echo "✅ 已有 P&F 退出使用元数据链"
        echo "🎯 修复完全验证成功！"
    else
        echo "⏳ 等待 P&F 退出验证元数据链是否生效"
    fi
else
    echo "⏳ 等待新的入场交易验证修复"
fi

if [ $PNF_FAILED -gt 0 ]; then
    echo "⚠️  有 $PNF_FAILED 次提取失败，需要检查错误原因"
fi
echo ""
