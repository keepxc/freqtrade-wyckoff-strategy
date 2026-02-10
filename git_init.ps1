# Git 初始化和配置脚本
# 使用方法: .\git_init.ps1

Write-Host "=== Freqtrade 威科夫策略 Git 初始化 ===" -ForegroundColor Green
Write-Host ""

# 步骤 1: 配置 Git 用户信息
Write-Host "步骤 1: 配置 Git 用户信息" -ForegroundColor Yellow
$userName = Read-Host "请输入你的 Git 用户名 (例如: YourName)"
$userEmail = Read-Host "请输入你的 Git 邮箱 (例如: your.email@example.com)"

git config --global user.name "$userName"
git config --global user.email "$userEmail"

Write-Host "✓ Git 用户信息配置完成" -ForegroundColor Green
Write-Host ""

# 步骤 2: 初始化 Git 仓库
Write-Host "步骤 2: 初始化 Git 仓库" -ForegroundColor Yellow
if (Test-Path ".git") {
    Write-Host "⚠ Git 仓库已存在，跳过初始化" -ForegroundColor Yellow
} else {
    git init
    Write-Host "✓ Git 仓库初始化完成" -ForegroundColor Green
}
Write-Host ""

# 步骤 3: 添加所有文件
Write-Host "步骤 3: 添加文件到 Git" -ForegroundColor Yellow
git add .gitignore
git add user_data/strategies/WyckoffV7_TrueMethod.py
git add wyckoff/
git add TASKS.md
git add *.md
git add docker-compose.yml

Write-Host "✓ 文件添加完成" -ForegroundColor Green
Write-Host ""

# 步骤 4: 查看状态
Write-Host "步骤 4: 查看 Git 状态" -ForegroundColor Yellow
git status
Write-Host ""

# 步骤 5: 创建初始提交
Write-Host "步骤 5: 创建初始提交" -ForegroundColor Yellow
$commitMessage = "Initial commit: Wyckoff V7 TrueMethod Strategy

- 威科夫交易策略 V7 版本
- 轴线系统 (Axis System)
- Spring 质量分级系统
- 当前利润: 310.92%
- 胜率: 75.3%
"

git commit -m "$commitMessage"
Write-Host "✓ 初始提交完成" -ForegroundColor Green
Write-Host ""

# 步骤 6: 显示提交历史
Write-Host "步骤 6: 查看提交历史" -ForegroundColor Yellow
git log --oneline -5
Write-Host ""

Write-Host "=== Git 初始化完成！ ===" -ForegroundColor Green
Write-Host ""
Write-Host "常用 Git 命令:" -ForegroundColor Cyan
Write-Host "  git status          - 查看当前状态"
Write-Host "  git add <file>      - 添加文件到暂存区"
Write-Host "  git commit -m 'msg' - 提交更改"
Write-Host "  git log             - 查看提交历史"
Write-Host "  git diff            - 查看未提交的更改"
Write-Host "  git checkout <file> - 撤销文件的更改"
Write-Host "  git reset --hard    - 重置到上次提交（危险！）"
Write-Host ""
Write-Host "下一步建议:" -ForegroundColor Cyan
Write-Host "  1. 创建 GitHub 仓库"
Write-Host "  2. 运行: git remote add origin <你的仓库URL>"
Write-Host "  3. 运行: git push -u origin main"
