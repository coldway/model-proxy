#!/bin/bash
# model-proxy Git Commit 监听脚本
# 检测新的 git commit 后自动重启服务，避免开发中频繁文件变更导致反复重启

LABEL="com.yuanrui.model-proxy"
PROJECT_DIR="/Users/yuanrui/go/src/gitlab.p1staff.com/new/AI-Coding/workspace/AI-agent/model-proxy"
LOG_FILE="$PROJECT_DIR/logs/watch-restart.log"
POLL_INTERVAL=5

mkdir -p "$PROJECT_DIR/logs"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [watch] $1" >> "$LOG_FILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') [watch] $1"
}

log "启动 Git Commit 监听: $PROJECT_DIR"
log "轮询间隔: ${POLL_INTERVAL}s"

LAST_COMMIT=$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null)
log "初始 commit: ${LAST_COMMIT:0:12}"

while true; do
    sleep "$POLL_INTERVAL"

    CURRENT_COMMIT=$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null)

    if [ -z "$CURRENT_COMMIT" ]; then
        continue
    fi

    if [ "$CURRENT_COMMIT" != "$LAST_COMMIT" ]; then
        COMMIT_MSG=$(git -C "$PROJECT_DIR" log -1 --format='%s' 2>/dev/null)
        log "检测到新 commit: ${CURRENT_COMMIT:0:12} - $COMMIT_MSG"
        log "正在重启 model-proxy..."

        # 先清理可能残留的孤儿进程（占用端口 8000）
        STALE_PIDS=$(lsof -ti :8000 2>/dev/null)
        if [ -n "$STALE_PIDS" ]; then
            log "清理占用端口 8000 的进程: $STALE_PIDS"
            echo "$STALE_PIDS" | xargs kill -9 2>/dev/null
            sleep 2
        fi

        launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>> "$LOG_FILE"

        if [ $? -eq 0 ]; then
            # 等待服务就绪
            for i in $(seq 1 10); do
                sleep 1
                if curl -s --connect-timeout 2 --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then
                    log "重启成功（${i}s 后就绪）"
                    break
                fi
                if [ $i -eq 10 ]; then
                    log "重启超时（10s 未响应 /health）"
                fi
            done
        else
            log "重启失败 (exit=$?)"
        fi

        LAST_COMMIT="$CURRENT_COMMIT"
    fi
done
