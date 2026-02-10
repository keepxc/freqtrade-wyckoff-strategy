# 快速 Git 提交脚本
# 使用方法: .\git_commit.ps1 "提交信息"

param(
    [Parameter(Mandatory=$false)]
    [string]$Message = ""
)

Write-Host "=== Git 快速提交 ===" -ForegroundColor Green
Write-Host ""

# 如果没有提供消息，提示输入
if ([string]::IsNullOrEmpty($Message)) {
    $Message = Read-Host "请输入提交信息"
}

# 显示当前状态
Write-Host "当前更改:" -ForegroundColor Yellow
git status --short
Write-Host ""

# 询问是否继续
$confirm = Read-Host "是否提交这些更改? (y/n)"
if ($confirm -ne 'y') {
    Write-Host "已取消" -ForegroundColor Red
    exit
}

# 添加所有更改
Write-Host "添加文件..." -ForegroundColor Yellow
git add -A

# 提交
Write-Host "提交更改..." -ForegroundColor Yellow
git commit -m "$Message"

# 显示最近的提交
Write-Host ""
Write-Host "最近的提交:" -ForegroundColor Green
git log --oneline -5

Write-Host ""
Write-Host "✓ 提交完成！" -ForegroundColor Green
