#!/usr/bin/env bash
# Created by model-proxy on 2026/07/27
# Copyright © 2026
#
# Model Proxy Docker 部署脚本
# 用法: bash deploy.sh [命令]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTAINER_NAME="model-proxy"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${CYAN}[STEP]${NC} $1"; }

check_docker() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker 未安装"
        echo "  安装: curl -fsSL https://get.docker.com | sh"
        exit 1
    fi
    if ! docker compose version &>/dev/null; then
        log_error "Docker Compose V2 未安装"
        exit 1
    fi
}

check_config() {
    if [ ! -f "$SCRIPT_DIR/conf/config.yaml" ]; then
        log_error "conf/config.yaml 不存在！"
        echo "  请从模板复制并填入 API Keys："
        echo "  cp conf/config.yaml.example conf/config.yaml"
        echo "  vim conf/config.yaml"
        exit 1
    fi

    local has_key=0
    if grep -q "api_key: .\+" "$SCRIPT_DIR/conf/config.yaml" 2>/dev/null; then
        has_key=1
    fi
    if [ "$has_key" -eq 0 ]; then
        log_warn "conf/config.yaml 中未发现任何 API Key，服务可能无法正常工作"
    fi
}

cmd_install() {
    log_info "=== Model Proxy Docker 部署 ==="
    check_docker
    check_config

    # 创建持久化目录
    mkdir -p "$SCRIPT_DIR/data"

    # 构建并启动
    log_step "构建 Docker 镜像..."
    cd "$SCRIPT_DIR"
    docker compose build

    log_step "启动容器..."
    docker compose up -d

    # 等待健康检查通过
    log_step "等待服务就绪..."
    for i in $(seq 1 30); do
        if curl -sf http://127.0.0.1:8000/mp/health > /dev/null 2>&1; then
            log_info "服务就绪 (${i}s)"
            break
        fi
        sleep 1
    done

    cmd_status
    echo ""
    cmd_test

    local server_ip
    server_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
    log_info "=== 部署完成 ==="
    log_info "本机访问: http://127.0.0.1:8000"
    log_info "如需公网访问，请执行: bash deploy.sh caddy"
}

cmd_start() {
    check_docker
    check_config
    log_info "启动容器..."
    cd "$SCRIPT_DIR"
    docker compose up -d
    sleep 2
    cmd_status
}

cmd_stop() {
    check_docker
    log_info "停止容器..."
    cd "$SCRIPT_DIR"
    docker compose down
    log_info "容器已停止"
}

cmd_restart() {
    check_docker
    check_config
    log_info "重启容器..."
    cd "$SCRIPT_DIR"
    docker compose restart
    sleep 3
    cmd_status
}

cmd_status() {
    echo ""
    cd "$SCRIPT_DIR"
    if docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null | grep -q "$CONTAINER_NAME"; then
        local status
        status=$(docker inspect --format='{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "unknown")
        local health
        health=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "none")

        if [ "$status" = "running" ]; then
            log_info "容器状态: ${GREEN}运行中${NC} (health: $health)"
            echo "  镜像: $(docker inspect --format='{{.Config.Image}}' "$CONTAINER_NAME" 2>/dev/null)"
            echo "  端口: $(docker port "$CONTAINER_NAME" 2>/dev/null | head -1)"
            echo "  启动: $(docker inspect --format='{{.State.StartedAt}}' "$CONTAINER_NAME" 2>/dev/null | cut -d. -f1)"
            echo "  内存: $(docker stats --no-stream --format '{{.MemUsage}}' "$CONTAINER_NAME" 2>/dev/null)"
        else
            log_warn "容器状态: ${RED}$status${NC}"
            echo "  最近日志:"
            docker compose logs --tail=5 2>/dev/null
        fi
    else
        log_warn "容器未运行"
    fi
}

cmd_logs() {
    local lines="${1:-50}"
    cd "$SCRIPT_DIR"
    docker compose logs -f --tail="$lines"
}

cmd_update() {
    check_docker
    check_config
    log_info "更新部署..."

    cd "$SCRIPT_DIR"

    # 拉取最新代码（如果是 git 仓库）
    if [ -d .git ]; then
        log_step "拉取最新代码..."
        git pull origin main 2>/dev/null || git pull 2>/dev/null || true
    fi

    # 重新构建并启动
    log_step "重新构建镜像..."
    docker compose up -d --build --remove-orphans

    # 等待就绪
    log_step "等待服务就绪..."
    for i in $(seq 1 30); do
        if curl -sf http://127.0.0.1:8000/mp/health > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    # 清理旧镜像
    docker image prune -f --filter "dangling=true" > /dev/null 2>&1 || true

    cmd_status
    echo ""
    cmd_test
}

cmd_caddy() {
    log_info "=== 配置 Caddy 反向代理 ==="

    if ! command -v caddy &>/dev/null; then
        log_step "安装 Caddy..."
        sudo apt-get update -qq
        sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl > /dev/null

        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
            sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
            sudo tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null
        sudo apt-get update -qq && sudo apt-get install -y -qq caddy > /dev/null

        if ! command -v caddy &>/dev/null; then
            log_warn "APT 安装失败，尝试二进制安装..."
            local arch="amd64"
            [[ "$(uname -m)" == "aarch64" ]] && arch="arm64"
            curl -sL "https://github.com/caddyserver/caddy/releases/latest/download/caddy_2.9.1_linux_${arch}.tar.gz" | \
                sudo tar -xz -C /usr/local/bin caddy
            sudo chmod +x /usr/local/bin/caddy
        fi
    fi

    log_step "写入 Caddyfile..."
    sudo mkdir -p /etc/caddy
    sudo tee /etc/caddy/Caddyfile > /dev/null << 'CADDYEOF'
:80 {
    # LLM API（SSE 流式输出）
    handle /v1/* {
        reverse_proxy 127.0.0.1:8000 {
            transport http {
                read_timeout 0
            }
            flush_interval -1
        }
    }

    # 管理路由（统一 /mp 前缀，包含文档）
    handle /mp/* {
        reverse_proxy 127.0.0.1:8000
    }

    handle {
        respond "Not Found" 404
    }
}
CADDYEOF

    sudo systemctl restart caddy
    sudo systemctl enable caddy

    local server_ip
    server_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_IP")
    log_info "Caddy 配置完成"
    log_info "公网访问:"
    log_info "  推理: http://${server_ip}/v1/chat/completions"
    log_info "  模型: http://${server_ip}/v1/models"
    log_info "  面板: http://${server_ip}/mp/ui"
    log_info "  文档: http://${server_ip}/mp/docs"
}

cmd_test() {
    log_info "测试推理接口..."
    local port=8000
    local response

    response=$(curl -sf "http://127.0.0.1:$port/mp/health" 2>/dev/null || echo "FAILED")
    if [[ "$response" == "FAILED" ]]; then
        log_error "无法连接到 model-proxy (port $port)"
        return 1
    fi

    local model_count
    model_count=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])))" 2>/dev/null || echo "0")
    log_info "可用模型数: $model_count"

    response=$(curl -sf -X POST "http://127.0.0.1:$port/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d '{"model":"auto","messages":[{"role":"user","content":"说一个字"}],"max_tokens":10}' \
        --max-time 30 2>/dev/null || echo "FAILED")

    if [[ "$response" == "FAILED" ]]; then
        log_warn "推理测试超时或失败（可能所有模型配额已用完）"
    else
        local content
        content=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('choices',[{}])[0].get('message',{}).get('content','')[:50])" 2>/dev/null || echo "")
        if [ -n "$content" ]; then
            log_info "推理测试通过: \"$content\""
        else
            log_warn "推理返回异常"
        fi
    fi
}

cmd_clean() {
    check_docker
    log_info "清理旧镜像和缓存..."
    cd "$SCRIPT_DIR"
    docker compose down --rmi local --volumes 2>/dev/null || true
    docker image prune -f
    docker builder prune -f
    log_info "清理完成"
}

# 主入口
case "${1:-help}" in
    install)  cmd_install ;;
    start)    cmd_start ;;
    stop)     cmd_stop ;;
    restart)  cmd_restart ;;
    status)   cmd_status ;;
    logs)     shift; cmd_logs "${1:-50}" ;;
    update)   cmd_update ;;
    caddy)    cmd_caddy ;;
    test)     cmd_test ;;
    clean)    cmd_clean ;;
    *)
        echo "Model Proxy Docker 部署管理脚本"
        echo ""
        echo "用法: bash deploy.sh <命令>"
        echo ""
        echo "命令:"
        echo "  install     首次部署（构建镜像 + 启动容器）"
        echo "  start       启动容器"
        echo "  stop        停止并移除容器"
        echo "  restart     重启容器"
        echo "  status      查看容器状态"
        echo "  logs [N]    查看实时日志（默认最近 50 行）"
        echo "  update      拉取代码 + 重新构建 + 重启"
        echo "  caddy       安装并配置 Caddy 反向代理"
        echo "  test        测试推理是否正常"
        echo "  clean       清理所有容器和镜像"
        echo ""
        echo "首次部署:"
        echo "  1. cp conf/config.yaml.example conf/config.yaml"
        echo "  2. vim conf/config.yaml     # 填入 API Keys"
        echo "  3. bash deploy.sh install   # 构建 + 启动"
        echo "  4. bash deploy.sh caddy     # 配置反向代理（公网访问）"
        echo "  5. bash deploy.sh test      # 验证"
        ;;
esac
