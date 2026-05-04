@echo off
echo Starting full dataset analysis...
echo.

python analyze_axis_full_dataset.py backtest-result-2026-05-04_04-00-26.zip > axis_support_full_analysis.log 2>&1

echo.
echo Analysis complete. Check axis_support_full_analysis.log for results.
echo.
pause
