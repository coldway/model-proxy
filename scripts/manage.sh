#!/bin/bash
# model-proxy 统一管理脚本
# 用法: ./manage.sh {install|uninstall|start|stop|restart|status|logs}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

PROXY_LABEL="com.yuanrui.model-proxy"
WATCHER_LABEL="com.yuanrui.model-proxy-watcher"
PROXY_PLIST="$SCRIPT_DIR/$PROXY_LABEL.plist"
WATCHER_PLIST="$SCRIPT_DIR/$WATCHER_LABEL.plist"
DOMAIN="gui/$(id -u)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err()  { echo -e "${RED}[ERROR]${NC} $1"; }

case "$1" in
    install)
        echo "=== 安装 model-proxy 服务 ==="
        mkdir -p "$PROJECT_DIR/logs"
        mkdir -p "$LAUNCH_AGENTS"
        chmod +x "$SCRIPT_DIR/watch-and-restart.sh"

        # 注意：launchd 的 plist 必须使用 main.py 而非 reload 模式
        # 因为 launchd 自身已提供崩溃重启能力
        cp "$PROXY_PLIST" "$LAUNCH_AGENTS/"
        cp "$WATCHER_PLIST" "$LAUNCH_AGENTS/"

        launchctl bootstrap "$DOMAIN" "$LAUNCH_AGENTS/$PROXY_LABEL.plist" 2>/dev/null
        launchctl bootstrap "$DOMAIN" "$LAUNCH_AGENTS/$WATCHER_LABEL.plist" 2>/dev/null

        log_ok "已安装并启动:"
        echo "  - $PROXY_LABEL (model-proxy 主服务)"
        echo "  - $WATCHER_LABEL (代码变更监听)"
        echo ""
        echo "服务地址: http://127.0.0.1:8000"
        echo "日志目录: $PROJECT_DIR/logs/"
        ;;

    uninstall)
        echo "=== 卸载 model-proxy 服务 ==="
        launchctl bootout "$DOMAIN/$WATCHER_LABEL" 2>/dev/null
        launchctl bootout "$DOMAIN/$PROXY_LABEL" 2>/dev/null
        rm -f "$LAUNCH_AGENTS/$PROXY_LABEL.plist"
        rm -f "$LAUNCH_AGENTS/$WATCHER_LABEL.plist"
        log_ok "已卸载全部 launchd 服务"
        ;;

    start)
        echo "=== 启动服务 ==="
        launchctl kickstart "$DOMAIN/$PROXY_LABEL" 2>/dev/null && log_ok "model-proxy 已启动" || log_warn "model-proxy 可能已在运行"
        launchctl kickstart "$DOMAIN/$WATCHER_LABEL" 2>/dev/null && log_ok "watcher 已启动" || log_warn "watcher 可能已在运行"
        ;;

    stop)
        echo "=== 停止服务 ==="
        launchctl kill SIGTERM "$DOMAIN/$WATCHER_LABEL" 2>/dev/null && log_ok "watcher 已停止" || log_warn "watcher 未在运行"
        launchctl kill SIGTERM "$DOMAIN/$PROXY_LABEL" 2>/dev/null && log_ok "model-proxy 已停止" || log_warn "model-proxy 未在运行"
        # KeepAlive 会自动重启，如需彻底停止请用 uninstall
        log_warn "注意：KeepAlive 配置会自动拉起服务，如需彻底停止请用 uninstall"
        ;;

    restart)
        echo "=== 重启 model-proxy ==="
        launchctl kickstart -k "$DOMAIN/$PROXY_LABEL" 2>/dev/null && log_ok "model-proxy 已重启" || log_err "重启失败"
        ;;

    status)
        echo "=== 服务状态 ==="
        echo ""
        echo "--- model-proxy ---"
        launchctl print "$DOMAIN/$PROXY_LABEL" 2>/dev/null | grep -E "state|pid|last exit" || log_warn "服务未加载"
        echo ""
        echo "--- watcher ---"
        launchctl print "$DOMAIN/$WATCHER_LABEL" 2>/dev/null | grep -E "state|pid|last exit" || log_warn "服务未加载"
        echo ""

        # 检查端口
        PROXY_PID=$(lsof -ti:8000 2>/dev/null)
        if [ -n "$PROXY_PID" ]; then
            log_ok "端口 8000 被 PID $PROXY_PID 占用 (model-proxy 运行中)"
        else
            log_warn "端口 8000 无进程监听"
        fi
        ;;

    logs)
        echo "=== 查看日志（Ctrl+C 退出）==="
        tail -f "$PROJECT_DIR/logs/model-proxy.stderr.log" "$PROJECT_DIR/logs/watch-restart.log" 2>/dev/null
        ;;

    *)
        echo "用法: $0 {install|uninstall|start|stop|restart|status|logs}"
        echo ""
        echo "  install    安装并启动 launchd 服务（开机自启 + 崩溃拉起 + 代码监听）"
        echo "  uninstall  卸载全部 launchd 服务"
        echo "  start      启动服务"
        echo "  stop       停止服务（KeepAlive 会自动拉起）"
        echo "  restart    重启 model-proxy"
        echo "  status     查看服务状态"
        echo "  logs       查看实时日志"
        exit 1
        ;;
esac
