# 部署指南

## 本地开发部署

### 环境要求

- Python 3.11+
- 操作系统：Windows / macOS / Linux

### 开发模式启动

```bash
# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境（按平台选择）
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Windows CMD
.venv\Scripts\activate.bat
# macOS / Linux
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 复制配置
cp conf/config.yaml.example conf/config.yaml  # Windows: copy conf\config.yaml.example conf\config.yaml

# 启动服务（热重载）
python main.py
```

开发模式默认启用 Uvicorn 热重载（`reload=True`），修改代码后服务自动重启。

---

## 生产部署（Docker，推荐）

### 架构

```
客户端请求 → Caddy (:80) → Docker 容器 (model-proxy:8000)
                                  ↓
                         conf/config.yaml (volume 挂载)
                         data/ (持久化目录)
```

### 1. 准备服务器

```bash
# 安装 Docker（如未安装）
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker

# 安装 Caddy（如未安装）
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

### 2. 克隆代码

```bash
# 从 Gitea 克隆（推荐路径）
git clone https://YOUR_GITEA_IP/gitea/admin/model-proxy.git ~/services/model-proxy
cd ~/services/model-proxy

# 或通过 rsync 上传
rsync -avz --exclude='__pycache__' --exclude='.venv' --exclude='data/' \
  ./model-proxy/ user@SERVER_IP:~/services/model-proxy/
```

### 3. 配置

```bash
cd ~/services/model-proxy

# 复制配置模板
cp conf/config.yaml.example conf/config.yaml

# 编辑配置，填入 API Keys
vim conf/config.yaml
```

关键配置项：

```yaml
settings:
  host: "0.0.0.0"
  port: 8000
  auto_switch: true
  log_level: "warning"

  # ─── 认证配置（公网暴露时必须启用） ───

  # 多 Key 模式（推荐：多用户/多服务独立限流）
  api_keys:
    - key: "sk-novel-engine-abc123"
      name: "novel-engine"
      rpm: 120
    - key: "sk-external-client-xyz789"
      name: "external-client"
      rpm: 30

  # 管理面板认证
  admin_token: "adm-your-admin-token"

  # 全局限流（api_keys 中未设置 rpm 的 key 使用此值）
  per_consumer_rpm: 60

providers:
  - name: google
    api_key: "your-google-api-key"
    # ...
```

生成强访问密钥：

```bash
openssl rand -hex 32
```

### 4. Docker Compose 配置

项目已包含 `docker-compose.yml`：

```yaml
services:
  model-proxy:
    build: .
    container_name: model-proxy
    ports:
      - "127.0.0.1:8000:8000"   # 仅本机可访问，由 Caddy 代理公网流量
    volumes:
      - ./conf/config.yaml:/app/conf/config.yaml:ro
      - ./data:/app/data
    restart: unless-stopped
    environment:
      - PYTHONUNBUFFERED=1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/v1/models"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    deploy:
      resources:
        limits:
          memory: 512M
```

> **安全**: `ports` 绑定 `127.0.0.1`，确保外部无法绕过 Caddy 直连。

### 5. 构建并启动

```bash
cd ~/services/model-proxy
docker compose up -d --build

# 验证
docker compose ps
docker compose logs -f --tail=20
```

### 6. 配置 Caddy 反向代理

```bash
sudo tee /etc/caddy/Caddyfile << 'EOF'
:80 {
    # LLM API（OpenAI/Anthropic 协议）
    handle /v1/* {
        reverse_proxy 127.0.0.1:8000 {
            transport http {
                read_timeout 0
            }
            flush_interval -1
        }
    }

    # 管理面板（公网可访问，由 admin_token 保护）
    handle /ui* {
        reverse_proxy 127.0.0.1:8000
    }

    # Swagger 文档（公网可访问）
    handle /docs* {
        reverse_proxy 127.0.0.1:8000
    }
    handle /redoc* {
        reverse_proxy 127.0.0.1:8000
    }
    handle /openapi.json {
        reverse_proxy 127.0.0.1:8000
    }

    # 健康检查（公开）
    handle /health {
        reverse_proxy 127.0.0.1:8000
    }

    # 静态资源（favicon 等）
    handle /favicon.ico {
        reverse_proxy 127.0.0.1:8000
    }

    handle {
        respond "Not Found" 404
    }
}
EOF

sudo systemctl restart caddy
```

> **关键**: `flush_interval -1` 确保 SSE 流式输出不被 Caddy 缓冲。

> **安全说明**: 管理面板 (`/ui`) 和 Swagger (`/docs`) 已开放公网访问。管理面板操作需要 `admin_token` 认证（在 `config.yaml` 中配置），确保使用强 Token。Swagger 为只读文档，暴露无安全风险。

### 7. 防火墙

```bash
sudo ufw allow 80/tcp     # Caddy 公网入口
sudo ufw allow 443/tcp    # HTTPS（可选）
sudo ufw allow 22/tcp     # SSH
sudo ufw deny 8000/tcp    # 禁止直接访问容器端口
sudo ufw enable
```

### 8. 验证

```bash
# 检查可用模型
curl http://YOUR_SERVER_IP/v1/models \
  -H "Authorization: Bearer your-access-api-key"

# 测试推理
curl http://YOUR_SERVER_IP/v1/chat/completions \
  -H "Authorization: Bearer your-access-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"你好"}]}'
```

---

## 日常运维

### 常用命令

```bash
cd ~/services/model-proxy

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f --tail=50

# 重启
docker compose restart

# 更新部署（代码更新后）
git pull origin main
docker compose up -d --build

# 停止
docker compose down

# 清理旧镜像
docker image prune -f
```

### 配置变更

修改 `conf/config.yaml` 后无需重新构建镜像（volume 挂载）：

```bash
# 修改配置
vim conf/config.yaml

# 重启容器使配置生效
docker compose restart
```

### 日志管理

```bash
# Docker 日志自动轮转，配置 /etc/docker/daemon.json
sudo tee /etc/docker/daemon.json << 'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF

sudo systemctl restart docker
```

---

## 一键部署脚本 (deploy.sh)

项目提供 `deploy.sh` 脚本封装 Docker 部署操作：

| 命令 | 用途 |
|------|------|
| `bash deploy.sh install` | 首次安装（检查 Docker + 构建 + 启动 + Caddy 配置） |
| `bash deploy.sh start` | 启动容器 |
| `bash deploy.sh stop` | 停止容器 |
| `bash deploy.sh restart` | 重启容器 |
| `bash deploy.sh update` | 拉取代码 + 重新构建 + 重启 |
| `bash deploy.sh status` | 查看容器状态 |
| `bash deploy.sh logs` | 查看实时日志 |
| `bash deploy.sh logs 100` | 查看最近 100 行日志 |
| `bash deploy.sh caddy` | 配置 Caddy 反向代理 |
| `bash deploy.sh test` | 测试推理接口 |

---

## 安全加固

| 措施 | 说明 |
|------|------|
| 多 API Key 鉴权 | `config.yaml` 中 `api_keys` 列表，各 Key 独立限流，请求需 `Authorization: Bearer <key>` |
| 管理面板 Token | `/ui*` 需要 `admin_token` 认证，未登录无法操作 |
| 端口绑定 127.0.0.1 | Docker 容器端口不暴露公网，仅 Caddy 可达 |
| 防火墙 | UFW 只开放 80/443/22 |
| 配置不入镜像 | `config.yaml` 通过 volume 注入，镜像不含密钥 |
| 请求历史 | `data/request_history.jsonl` 仅存元数据，不记录消息内容 |

---

## 与 Novel Engine 联合部署

Novel Engine 通过 OpenAI 兼容协议连接 model-proxy：

```yaml
# novel_engine.yaml
llm_backend:
  active: openai_compatible
  openai_compatible:
    base_url: http://YOUR_SERVER_IP/v1      # 公网地址
    api_key: sk-novel-engine-abc123          # 对应 api_keys 中配置的 key
```

如果两者在同一台服务器，Novel Docker 容器可通过 Docker 网络直连：

```yaml
# novel docker-compose.prod.yml
services:
  novel-api:
    environment:
      - LLM_BASE_URL=http://host.docker.internal:8000/v1
      - LLM_API_KEY=sk-novel-engine-abc123
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

---

## Gitea 集成（自动部署）

### Gitea Actions 自动部署

项目已包含 `.gitea/workflows/deploy.yml`，push 到 `main` 分支后自动执行部署：

```yaml
# .gitea/workflows/deploy.yml
name: Deploy Model Proxy
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: self-hosted
    steps:
      - name: Pull & Deploy
        run: |
          cd ~/services/model-proxy
          git fetch origin main
          git reset --hard origin/main
          bash deploy.sh update
      - name: Health Check
        run: |
          for i in $(seq 1 30); do
            curl -sf http://127.0.0.1:8000/health > /dev/null && exit 0
            sleep 1
          done
          echo "::error::健康检查失败"
          exit 1
```

### 前置条件

1. Gitea 已启用 Actions（`app.ini` 中 `[actions] ENABLED = true`）
2. 服务器上已注册 Act Runner（`self-hosted` 标签）
3. 代码已克隆到 `~/services/model-proxy`

### 手动部署

```bash
cd ~/services/model-proxy
git pull origin main
bash deploy.sh update
```

---

## 健康检查

```bash
# 服务存活
curl http://127.0.0.1:8000/v1/models

# 查看使用量
curl http://127.0.0.1:8000/v1/usage
```

Docker Compose 已内置健康检查（每 30 秒 `/v1/models`），容器异常会自动重启。

---

## 重要设计约束

> **单进程设计**: model-proxy 所有状态（dispatcher、rate_limiter、history、session）以进程内单例持有。
> 不支持多 Worker/多实例。如需水平扩展，应在反向代理层做负载均衡（多容器各自独立状态），或改用外部存储。
