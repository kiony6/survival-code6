# 生存代码 - 联机肉鸽割草游戏

## 文件说明

| 文件 | 说明 |
|---|---|
| `game_pygame.py` | 游戏客户端（登录→大厅→联机对战） |
| `survival_server.py` | 游戏服务器（账号/房间/消息中继） |
| `tunnel.py` | SSH 隧道工具（NAT 不通时救命用） |
| `deploy_vps.py` | 一键部署服务器到 VPS |
| `framesync_demo.py` | 帧同步 vs 状态同步对比演示 |

## 架构说明

- **同步方案**：事件驱动 + 本地视觉推测（Event-Driven + Local Visual Prediction）
- 网络只传"发生了什么"，不同步"看起来什么样"
- 子弹、粒子、爆炸特效均为 guest 本地模拟，无需每帧传输

## 上传到 GitHub

### 方法一：GitHub 网页上传（最简单）

1. 打开 https://github.com/kiony6
2. 点「Repositories」→ 绿色按钮「New」
3. 仓库名填 `survival-code`，公开/私有随意，不要勾任何模板
4. 创建后进仓库，点「uploading an existing file」
5. 把 `C:\Users\VOS-User\Desktop\生存代码_完整项目` 里的文件全部拖进去
6. 等上传完，点「Commit changes」

### 方法二：命令行（推荐）

```bash
# 1. 先登录 GitHub，在 https://github.com/settings/tokens 创建一个 token
#    勾 repo 权限

# 2. 打开终端（Win+R → cmd）

cd C:\Users\VOS-User\Desktop\生存代码_完整项目
git init
git add -A
git commit -m "生存代码 v1.0 - 事件驱动联机同步"

# 3. 在 GitHub 网页创建仓库 survival-code（不要勾 README/.gitignore）
# 4. 复制仓库页面的 git remote 地址，替换下面执行
git remote add origin https://github.com/kiony6/survival-code.git
git push -u origin master
```

## 快速启动

```bash
# 先开服务器（本机测试用）
python survival_server.py

# 再开两个客户端
python game_pygame.py
```

## VPS 部署

```bash
python deploy_vps.py
```
