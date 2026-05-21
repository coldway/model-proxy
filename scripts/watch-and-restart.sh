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

HEALTH_URL="http://127.0.0.1:8000/health"
HEALTH_CHECK_INTERVAL=30
HEALTH_FAIL_COUNT=0
HEALTH_FAIL_THRESHOLD=2
POLL_COUNT=0
CHECKS_PER_HEALTH=$((HEALTH_CHECK_INTERVAL / POLL_INTERVAL))

restart_service() {
    local reason="$1"
    log "正在重启 model-proxy（原因: $reason）..."

    STALE_PIDS=$(lsof -ti :8000 2>/dev/null)
    if [ -n "$STALE_PIDS" ]; then
        log "清理占用端口 8000 的进程: $STALE_PIDS"
        echo "$STALE_PIDS" | xargs kill -9 2>/dev/null
        sleep 2
    fi

    launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>> "$LOG_FILE"

    if [ $? -eq 0 ]; then
        for i in $(seq 1 10); do
            sleep 1
            if curl -s --connect-timeout 2 --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
                log "重启成功（${i}s 后就绪）"
                HEALTH_FAIL_COUNT=0
                return 0
            fi
            if [ $i -eq 10 ]; then
                log "重启超时（10s 未响应 /health）"
            fi
        done
    else
        log "重启失败 (exit=$?)"
    fi
    return 1
}

while true; do
    sleep "$POLL_INTERVAL"
    POLL_COUNT=$((POLL_COUNT + 1))

    # --- Git commit 检测 ---
    CURRENT_COMMIT=$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null)
    if [ -n "$CURRENT_COMMIT" ] && [ "$CURRENT_COMMIT" != "$LAST_COMMIT" ]; then
        COMMIT_MSG=$(git -C "$PROJECT_DIR" log -1 --format='%s' 2>/dev/null)
        log "检测到新 commit: ${CURRENT_COMMIT:0:12} - $COMMIT_MSG"
        restart_service "新 commit"
        LAST_COMMIT="$CURRENT_COMMIT"
        POLL_COUNT=0
        continue
    fi

    # --- 健康检查（每 HEALTH_CHECK_INTERVAL 秒一次） ---
    if [ $POLL_COUNT -ge $CHECKS_PER_HEALTH ]; then
        POLL_COUNT=0
        if ! curl -s --connect-timeout 3 --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
            HEALTH_FAIL_COUNT=$((HEALTH_FAIL_COUNT + 1))
            log "健康检查失败 ($HEALTH_FAIL_COUNT/$HEALTH_FAIL_THRESHOLD)"
            if [ $HEALTH_FAIL_COUNT -ge $HEALTH_FAIL_THRESHOLD ]; then
                restart_service "健康检查连续失败 ${HEALTH_FAIL_COUNT} 次"
                HEALTH_FAIL_COUNT=0
            fi
        else
            if [ $HEALTH_FAIL_COUNT -gt 0 ]; then
                log "健康检查恢复正常"
            fi
            HEALTH_FAIL_COUNT=0
        fi
    fi
done
