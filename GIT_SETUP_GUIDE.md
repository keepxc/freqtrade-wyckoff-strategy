# Git 设置指南

## 步骤 1: 配置 Git PATH

由于 Git 安装在 D 盘，需要将其添加到系统 PATH：

### 方法 1: 手动添加到系统环境变量（推荐）

1. 按 `Win + X`，选择"系统"
2. 点击"高级系统设置"
3. 点击"环境变量"
4. 在"系统变量"中找到 `Path`，点击"编辑"
5. 点击"新建"，添加以下路径（根据实际安装位置调整）：
   ```
   D:\Git\cmd
   D:\Git\bin
   ```
   或
   ```
   D:\Program Files\Git\cmd
   D:\Program Files\Git\bin
   ```
6. 点击"确定"保存
7. **重新打开 PowerShell 窗口**

### 方法 2: 临时添加到当前会话

在 PowerShell 中运行（每次打开新窗口都需要运行）：

```powershell
# 根据实际路径调整
$env:Path = "D:\Git\cmd;D:\Git\bin;$env:Path"

# 验证
git --version
```

## 步骤 2: 配置 Git 用户信息

```powershell
# 设置用户名
git config --global user.name "你的名字"

# 设置邮箱
git config --global user.email "your.email@example.com"

# 验证配置
git config --list
```

## 步骤 3: 初始化 Git 仓库

### 方法 1: 使用自动化脚本（推荐）

```powershell
.\git_init.ps1
```

### 方法 2: 手动初始化

```powershell
# 初始化仓库
git init

# 添加文件
git add .gitignore
git add README.md
git add TASKS.md
git add user_data/strategies/WyckoffV7_TrueMethod.py
git add wyckoff/
git add *.md

# 查看状态
git status

# 创建初始提交
git commit -m "Initial commit: Wyckoff V7 TrueMethod Strategy"

# 查看提交历史
git log
```

## 步骤 4: 日常使用

### 快速提交（推荐）

```powershell
.\git_commit.ps1 "你的提交信息"
```

### 手动提交

```powershell
# 查看更改
git status

# 添加所有更改
git add -A

# 或添加特定文件
git add user_data/strategies/WyckoffV7_TrueMethod.py

# 提交
git commit -m "修复: Spring 信号优化"

# 查看历史
git log --oneline -10
```

## 步骤 5: 连接到 GitHub（可选）

### 5.1 创建 GitHub 仓库

1. 访问 https://github.com
2. 点击右上角的 "+" → "New repository"
3. 输入仓库名称（例如：`freqtrade-wyckoff-strategy`）
4. 选择 Private（私有仓库，推荐）
5. 不要勾选 "Initialize this repository with a README"
6. 点击 "Create repository"

### 5.2 连接本地仓库到 GitHub

```powershell
# 添加远程仓库（替换为你的仓库 URL）
git remote add origin https://github.com/你的用户名/freqtrade-wyckoff-strategy.git

# 推送到 GitHub
git branch -M main
git push -u origin main
```

### 5.3 后续推送

```powershell
# 提交更改后推送
git push
```

## 常用 Git 命令速查

### 查看状态
```powershell
git status              # 查看当前状态
git status --short      # 简洁模式
git diff                # 查看未暂存的更改
git diff --staged       # 查看已暂存的更改
```

### 添加和提交
```powershell
git add <file>          # 添加特定文件
git add -A              # 添加所有更改
git commit -m "msg"     # 提交
git commit --amend      # 修改上次提交
```

### 查看历史
```powershell
git log                 # 查看完整历史
git log --oneline       # 简洁模式
git log --oneline -10   # 最近 10 条
git log --graph         # 图形化显示
```

### 撤销更改
```powershell
git checkout <file>     # 撤销文件的更改
git reset HEAD <file>   # 取消暂存
git reset --hard        # 重置到上次提交（危险！）
git revert <commit>     # 撤销某次提交
```

### 分支管理
```powershell
git branch              # 查看分支
git branch <name>       # 创建分支
git checkout <name>     # 切换分支
git checkout -b <name>  # 创建并切换分支
git merge <branch>      # 合并分支
```

### 远程操作
```powershell
git remote -v           # 查看远程仓库
git push                # 推送到远程
git pull                # 拉取远程更改
git fetch               # 获取远程更新
```

## 推荐的提交信息格式

```
类型: 简短描述

详细说明（可选）

相关任务: Task 2.1.1
```

### 类型示例：
- `feat`: 新功能
- `fix`: 修复 bug
- `docs`: 文档更新
- `style`: 代码格式调整
- `refactor`: 重构
- `test`: 测试相关
- `chore`: 构建/工具相关

### 示例：
```
feat: 实现 Spring 质量分级系统

- 添加 Spring #1/2/3 分级逻辑
- 基于跌破深度和成交量分级
- Spring #3 数量从 2 个增加到 6 个

相关任务: Task 2.1.1
```

## 故障排除

### 问题 1: git 命令找不到

**解决方案**: 确保 Git 的 `cmd` 和 `bin` 目录已添加到 PATH，并重新打开 PowerShell。

### 问题 2: 权限错误

**解决方案**: 以管理员身份运行 PowerShell。

### 问题 3: 中文乱码

**解决方案**:
```powershell
git config --global core.quotepath false
git config --global gui.encoding utf-8
git config --global i18n.commit.encoding utf-8
git config --global i18n.logoutputencoding utf-8
```

### 问题 4: 推送到 GitHub 需要密码

**解决方案**: 使用 Personal Access Token (PAT) 代替密码：
1. GitHub → Settings → Developer settings → Personal access tokens
2. Generate new token
3. 选择 `repo` 权限
4. 复制 token
5. 推送时使用 token 作为密码

## 下一步

1. ✅ 配置 Git PATH
2. ✅ 配置用户信息
3. ✅ 初始化仓库
4. ✅ 创建初始提交
5. ⏳ 连接到 GitHub（可选）
6. ⏳ 定期提交更改

---

**提示**: 建议每完成一个功能或修复一个 bug 就提交一次，保持提交历史清晰。
