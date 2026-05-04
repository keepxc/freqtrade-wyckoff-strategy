# Wyckoff 检测诊断快速启动脚本 (PowerShell 版本)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Wyckoff 结构检测诊断工具" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "此脚本将运行回测并输出详细的 SC/BC 检测诊断信息"
Write-Host ""

# 检查是否在正确的目录
if (-not (Test-Path "user_data/strategies/WyckoffV7_TrueMethod.py")) {
    Write-Host "❌ 错误：未找到策略文件" -ForegroundColor Red
    Write-Host "请在项目根目录运行此脚本"
    exit 1
}

# 备份原始检测器（如果还没备份）
if (-not (Test-Path "wyckoff/climatic_action_detector.py.original")) {
    Write-Host "📦 备份原始检测器..." -ForegroundColor Yellow
    Copy-Item "wyckoff/climatic_action_detector.py" "wyckoff/climatic_action_detector.py.original"
}

# 临时替换为诊断版本
Write-Host "🔧 启用诊断版检测器..." -ForegroundColor Yellow
Copy-Item "wyckoff/climatic_action_detector_diagnostic.py" "wyckoff/climatic_action_detector.py" -Force

# 运行回测（使用较短的时间范围以加快速度）
Write-Host ""
Write-Host "🚀 开始运行诊断回测..." -ForegroundColor Green
Write-Host "   时间范围: 2024-01-01 至 2026-03-30"
Write-Host "   策略: WyckoffV7_TrueMethod"
Write-Host ""

docker compose run --rm freqtrade backtesting `
    --config user_data/config_backtest.json `
    --strategy WyckoffV7_TrueMethod `
    --timerange 20240101-20260330 `
    --cache none `
    2>&1 | Tee-Object -FilePath "wyckoff_diagnostic_output.log"

# 恢复原始检测器
Write-Host ""
Write-Host "🔄 恢复原始检测器..." -ForegroundColor Yellow
Copy-Item "wyckoff/climatic_action_detector.py.original" "wyckoff/climatic_action_detector.py" -Force

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "诊断完成！" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📊 诊断报告已保存到: wyckoff_diagnostic_output.log"
Write-Host ""
Write-Host "🔍 查看关键信息："
Write-Host "   Select-String '🔍 Wyckoff' wyckoff_diagnostic_output.log"
Write-Host "   Select-String 'SC \(Selling Climax\)' wyckoff_diagnostic_output.log"
Write-Host "   Select-String 'BC \(Buying Climax\)' wyckoff_diagnostic_output.log"
Write-Host ""
Write-Host "📈 查看条件通过率："
Write-Host "   Select-String '条件' wyckoff_diagnostic_output.log | Select-String '%'"
Write-Host ""
