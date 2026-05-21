# model-proxy 本地服务管理

本文档记录 macOS 本地开发环境下 model-proxy 的自动化服务管理配置。

## 架构

```
launchd
├── com.yuanrui.model-proxy          # 主服务（开机自启 + 崩溃自愈）
└── com.yuanrui.model-proxy-watcher  # 文件监听（代码变更自动重启）
```

## 配置文件位置

| 文件 | 路径 |
|------|------|
| 主服务 plist | `~/Library/LaunchAgents/com.yuanrui.model-proxy.plist` |
| Watcher plist | `~/Library/LaunchAgents/com.yuanrui.model-proxy-watcher.plist` |
| 监听脚本 | `scripts/watch-and-restart.sh` |

## 功能说明

### 1. 开机自启

主服务和 watcher 均设置 `RunAtLoad: true`，Mac 登录后自动启动。

### 2. 崩溃自愈

主服务设置 `KeepAlive.SuccessfulExit: false`，非正常退出（崩溃）后 3 秒自动拉起。

### 3. Git Commit 触发自动重启

watcher 每 5 秒轮询 `git rev-parse HEAD`，检测到新的 commit 后通过 `launchctl kickstart -k` 触发主服务重启。

**设计理由**：监听文件变更会在开发过程中频繁触发重启，改为监听 commit 后只在代码正式提交时重启，避免开发中断。

## 常用命令

```bash
# 查看服务状态
launchctl list | grep model-proxy

# 手动重启主服务
launchctl kickstart -k gui/$(id -u)/com.yuanrui.model-proxy

# 查看主服务日志
tail -f logs/model-proxy.log

# 查看 watcher 日志
tail -f logs/watch-restart.log

# 停止主服务
launchctl unload ~/Library/LaunchAgents/com.yuanrui.model-proxy.plist

# 停止 watcher
launchctl unload ~/Library/LaunchAgents/com.yuanrui.model-proxy-watcher.plist

# 启动主服务
launchctl load ~/Library/LaunchAgents/com.yuanrui.model-proxy.plist

# 启动 watcher
launchctl load ~/Library/LaunchAgents/com.yuanrui.model-proxy-watcher.plist
```

## 日志文件

| 日志 | 路径 | 说明 |
|------|------|------|
| 应用日志 | `logs/model-proxy.log` | Python RotatingFileHandler 输出 |
| stderr | `logs/model-proxy.stderr.log` | launchd 捕获的 stderr |
| watcher 日志 | `logs/watch-restart.log` | 文件变更检测和重启记录 |

## 依赖

- macOS `launchd`（系统内置）
- `git`（系统内置）

## 注意事项

- Python 路径硬编码为 `.venv/bin/python`，重建虚拟环境后需确认路径不变
- 服务端口 `8000`，与 `conf/config.yaml` 中配置一致
- 配置文件（`.yaml`）变更也会触发重启，修改配置后无需手动操作
