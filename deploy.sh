#!/usr/bin/env bash
# Created by model-proxy on 2026/07/27
# Copyright © 2026
#
# Model Proxy 独立部署脚本
# 用法: bash deploy.sh [命令]
# 命令:
#   install   - 首次安装（Python 环境 + 依赖 + Systemd + Caddy）
#   start     - 启动服务
#   stop      - 停止服务
#   restart   - 重启服务
#   status    - 查看服务状态
#   logs      - 查看实时日志
#   update    - 更新代码后重启
#   caddy     - 配置 Caddy 反向代理
#   test      - 测试推理是否正常

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="model-proxy"
VENV_DIR="$SCRIPT_DIR/.venv"
SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${CYAN}[STEP]${NC} $1"; }

check_config() {
    if [ ! -f "$SCRIPT_DIR/conf/config.yaml" ]; then
        log_error "conf/config.yaml 不存在！"
        echo "  请从 config.yaml.example 复制并填入 API Keys："
        echo "  cp conf/config.yaml.example conf/config.yaml"
        echo "  vim conf/config.yaml"
        exit 1
    fi

    # 检查是否有至少一个有效的 provider key
    local has_key=0
    if grep -q "api_key: .\+" "$SCRIPT_DIR/conf/config.yaml" 2>/dev/null; then
        has_key=1
    fi
    if [ "$has_key" -eq 0 ]; then
        log_warn "conf/config.yaml 中未发现任何 API Key，服务可能无法正常工作"
    fi
}

cmd_install() {
    log_info "=== Model Proxy 首次安装 ==="

    # 检查 Python
    log_step "检查 Python 环境..."
    if ! command -v python3 &>/dev/null; then
        log_info "安装 Python3..."
        sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
    fi

    local py_version
    py_version=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
    log_info "Python 版本: $py_version"

    # 创建虚拟环境
    log_step "创建 Python 虚拟环境..."
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
    fi

    # 安装依赖
    log_step "安装依赖..."
    "$VENV_DIR/bin/pip" install --no-cache-dir -r "$SCRIPT_DIR/requirements.txt"

    # 检查配置
    check_config

    # 修改 config.yaml 中的 host 为 0.0.0.0
    log_step "调整生产配置..."
    if grep -q 'host: 127.0.0.1' "$SCRIPT_DIR/conf/config.yaml"; then
        sed -i 's/host: 127.0.0.1/host: 0.0.0.0/' "$SCRIPT_DIR/conf/config.yaml"
        log_info "已将 host 改为 0.0.0.0"
    fi
    if grep -q 'log_level: debug' "$SCRIPT_DIR/conf/config.yaml"; then
        sed -i 's/log_level: debug/log_level: info/' "$SCRIPT_DIR/conf/config.yaml"
        log_info "已将 log_level 改为 info"
    fi

    # 创建 data 目录
    mkdir -p "$SCRIPT_DIR/data"

    # 安装 Systemd 服务
    log_step "配置 Systemd 服务..."
    sudo tee "$SYSTEMD_FILE" > /dev/null << EOF
[Unit]
Description=Model Proxy - LLM 推理代理
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$SCRIPT_DIR
Environment=PATH=$VENV_DIR/bin:/usr/bin:/bin
ExecStart=$VENV_DIR/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"

    # 启动服务
    log_step "启动服务..."
    sudo systemctl start "$SERVICE_NAME"
    sleep 3

    # 验证
    cmd_status
    echo ""
    cmd_test

    log_info "=== 安装完成 ==="
    log_info "服务地址: http://$(hostname -I | awk '{print $1}'):8000"
    log_info "管理面板: http://$(hostname -I | awk '{print $1}'):8000/ui"
    log_info "API 文档: http://$(hostname -I | awk '{print $1}'):8000/docs"
}

cmd_start() {
    check_config
    log_info "启动 $SERVICE_NAME..."
    sudo systemctl start "$SERVICE_NAME"
    sleep 2
    cmd_status
}

cmd_stop() {
    log_info "停止 $SERVICE_NAME..."
    sudo systemctl stop "$SERVICE_NAME"
    log_info "服务已停止"
}

cmd_restart() {
    check_config
    log_info "重启 $SERVICE_NAME..."
    sudo systemctl restart "$SERVICE_NAME"
    sleep 2
    cmd_status
}

cmd_status() {
    echo ""
    if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
        log_info "服务状态: ${GREEN}运行中${NC}"
        echo "  PID: $(sudo systemctl show -p MainPID --value "$SERVICE_NAME")"
        echo "  内存: $(sudo systemctl show -p MemoryCurrent --value "$SERVICE_NAME" 2>/dev/null || echo "N/A")"
        echo "  运行时间: $(sudo systemctl show -p ActiveEnterTimestamp --value "$SERVICE_NAME")"
    else
        log_warn "服务状态: ${RED}已停止${NC}"
        echo "  最近日志:"
        sudo journalctl -u "$SERVICE_NAME" --no-pager -n 5
    fi
}

cmd_logs() {
    local lines="${1:-50}"
    sudo journalctl -u "$SERVICE_NAME" -f -n "$lines"
}

cmd_update() {
    log_info "更新代码后重启..."

    # 更新依赖（如果 requirements.txt 变化）
    "$VENV_DIR/bin/pip" install --no-cache-dir -r "$SCRIPT_DIR/requirements.txt" --quiet

    # 重启服务
    sudo systemctl restart "$SERVICE_NAME"
    sleep 3

    cmd_status
    cmd_test
}

cmd_caddy() {
    log_info "=== 配置 Caddy 反向代理 ==="

    if ! command -v caddy &>/dev/null; then
        log_info "安装 Caddy..."
        sudo apt-get update
        sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
        curl -1sLf 'https://dl.cloudflare.com/cloudflare-main.gpg' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null || true
        echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudflare.com/cloudflare-main.list * *" | sudo tee /etc/apt/sources.list.d/caddy-stable.list 2>/dev/null || true
        sudo apt-get update && sudo apt-get install -y caddy || {
            log_warn "APT 安装失败，尝试直接下载..."
            local arch="amd64"
            [[ "$(uname -m)" == "aarch64" ]] && arch="arm64"
            curl -o /tmp/caddy.tar.gz -L "https://github.com/caddyserver/caddy/releases/latest/download/caddy_2.9.1_linux_${arch}.tar.gz"
            sudo tar -xzf /tmp/caddy.tar.gz -C /usr/local/bin caddy
            sudo chmod +x /usr/local/bin/caddy
        }
    fi

    log_info "写入 Caddyfile..."
    sudo tee /etc/caddy/Caddyfile > /dev/null << 'CADDYEOF'
# Model Proxy - IP 访问模式
:80 {
    # API 推理接口（SSE 流式）
    handle /v1/* {
        reverse_proxy localhost:8000 {
            transport http {
                read_timeout 0
            }
            flush_interval -1
        }
    }

    # Web 管理面板 + 文档
    handle /ui* {
        reverse_proxy localhost:8000
    }
    handle /docs* {
        reverse_proxy localhost:8000
    }
    handle /openapi.json {
        reverse_proxy localhost:8000
    }
    handle /redoc* {
        reverse_proxy localhost:8000
    }

    # 默认
    handle {
        reverse_proxy localhost:8000
    }
}
CADDYEOF

    sudo systemctl restart caddy
    sudo systemctl enable caddy

    local server_ip
    server_ip=$(hostname -I | awk '{print $1}')
    log_info "Caddy 配置完成"
    log_info "访问地址:"
    log_info "  推理接口: http://${server_ip}/v1/chat/completions"
    log_info "  模型列表: http://${server_ip}/v1/models"
    log_info "  管理面板: http://${server_ip}/ui"
    log_info "  API 文档: http://${server_ip}/docs"
}

cmd_test() {
    log_info "测试推理..."
    local port=8000
    local response

    # 先检查模型列表
    response=$(curl -sf "http://localhost:$port/v1/models" 2>/dev/null || echo "FAILED")
    if [[ "$response" == "FAILED" ]]; then
        log_error "无法连接到 model-proxy (port $port)"
        return 1
    fi

    local model_count
    model_count=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])))" 2>/dev/null || echo "0")
    log_info "可用模型数: $model_count"

    # 测试推理
    response=$(curl -sf -X POST "http://localhost:$port/v1/chat/completions" \
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
            local error
            error=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('message','')[:100])" 2>/dev/null || echo "$response")
            log_warn "推理返回异常: $error"
        fi
    fi
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
    *)
        echo "Model Proxy 部署管理脚本"
        echo ""
        echo "用法: bash deploy.sh <命令>"
        echo ""
        echo "命令:"
        echo "  install     首次安装（Python 环境 + 依赖 + Systemd 服务）"
        echo "  start       启动服务"
        echo "  stop        停止服务"
        echo "  restart     重启服务"
        echo "  status      查看服务状态"
        echo "  logs [N]    查看实时日志（默认最近 50 行）"
        echo "  update      更新代码后重启（重装依赖 + 重启）"
        echo "  caddy       安装并配置 Caddy 反向代理"
        echo "  test        测试推理是否正常"
        echo ""
        echo "首次部署:"
        echo "  1. vim conf/config.yaml   # 填入 API Keys"
        echo "  2. bash deploy.sh install # 安装 + 启动"
        echo "  3. bash deploy.sh caddy   # 配置反向代理（可选）"
        echo "  4. bash deploy.sh test    # 验证"
        ;;
esac
