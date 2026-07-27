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
客户端请求 → Caddy 容器 (:443, HTTPS) ──personal_proxy 网络──→ model-proxy 容器 (:8000)
                                                                       ↓
                                                              conf/config.yaml (volume 挂载)
                                                              data/ (持久化目录)
```

### 1. 准备服务器

```bash
# 安装 Docker（如未安装）
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
```

> Caddy 也运行在 Docker 中（已有 `personal-caddy` 容器），无需单独安装。

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

项目已包含 `docker-compose.yml`，配置了 `personal_proxy` 网络使 Caddy 可通过容器名访问：

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
    networks:
      - proxy

networks:
  proxy:
    external: true
    name: personal_proxy    # 与 Caddy 容器共享此网络
```

> **安全**: `ports` 绑定 `127.0.0.1`，确保外部无法绕过 Caddy 直连。
> 
> **网络**: 加入 `personal_proxy` 外部网络后，Caddy 容器可通过 `model-proxy:8000` 直接访问。

### 5. 构建并启动

```bash
cd ~/services/model-proxy

# 确保 personal_proxy 网络已存在（由 Caddy 的 docker compose 创建）
docker network inspect personal_proxy > /dev/null 2>&1 || \
  docker network create personal_proxy

# 构建并启动
docker compose up -d --build

# 验证容器状态
docker compose ps

# 确认已加入 personal_proxy 网络（输出应包含 model-proxy）
docker network inspect personal_proxy --format '{{range .Containers}}{{.Name}} {{end}}'
```

> **重要**: 如果上述命令输出中没有看到 `model-proxy`，手动加入：
> ```bash
> docker network connect personal_proxy model-proxy
> ```
>
> 这通常发生在 `personal_proxy` 网络在 model-proxy 启动后才创建的情况。
> 重启后会自动加入（`docker-compose.yml` 已声明该网络）。

### 6. 配置 Caddy 反向代理

Caddy 运行在 Docker 容器 `personal-caddy-caddy-1` 中，与 model-proxy 通过 `personal_proxy` 网络互通。

编辑 Caddy 配置文件（位于 `~/gitea-platform/caddy/Caddyfile`），在 `https://{$SERVER_IP}` site block 中添加 model-proxy 路由：

```
{
    auto_https disable_redirects
    default_sni {$SERVER_IP}
}

https://{$SERVER_IP} {
    tls internal
    encode zstd gzip

    redir /gitea /gitea/ permanent
    handle_path /gitea/* {
        reverse_proxy gitea:3000
    }

    # ─── model-proxy LLM API（OpenAI/Anthropic 协议）───
    handle /v1/* {
        reverse_proxy model-proxy:8000 {
            transport http {
                read_timeout 0
            }
            flush_interval -1
        }
    }

    # model-proxy 管理面板（由 admin_token 保护）
    handle /ui* {
        reverse_proxy model-proxy:8000
    }

    # model-proxy 静态资源（管理面板 JS/CSS）
    handle /static/* {
        reverse_proxy model-proxy:8000
    }

    # model-proxy API 文档
    handle /docs* {
        reverse_proxy model-proxy:8000
    }
    handle /redoc* {
        reverse_proxy model-proxy:8000
    }
    handle /openapi.json {
        reverse_proxy model-proxy:8000
    }

    # 健康检查
    handle /health {
        reverse_proxy model-proxy:8000
    }

    handle /favicon.ico {
        reverse_proxy model-proxy:8000
    }

    # 兜底：个人站点或 404
    handle {
        reverse_proxy personal-site:80
    }
}
```

重启 Caddy 容器使配置生效：

```bash
docker restart personal-caddy-caddy-1
```

> **前提**: model-proxy 容器必须已加入 `personal_proxy` 网络（`docker-compose.yml` 已配置）。
> 如果是首次启动前手动验证：`docker network connect personal_proxy model-proxy`
>
> **关键**: `flush_interval -1` 确保 SSE 流式输出不被 Caddy 缓冲。`read_timeout 0` 防止长生成超时。
>
> **HTTPS**: Caddy 使用内部 CA 签发的自签名证书，客户端需 `curl -k` 或信任 Caddy 根证书。

### 7. 防火墙

```bash
sudo ufw allow 443/tcp    # Caddy HTTPS 入口
sudo ufw allow 22/tcp     # SSH
sudo ufw deny 8000/tcp    # 禁止直接访问容器端口
sudo ufw enable
```

### 8. 验证

```bash
# 1. 确认网络互通
docker exec personal-caddy-caddy-1 nslookup model-proxy
# 应返回 model-proxy 的 IP 地址

# 2. 健康检查（不需要认证）
curl -k https://YOUR_SERVER_IP/health

# 3. 检查可用模型（-k 跳过自签名证书校验）
curl -k https://YOUR_SERVER_IP/v1/models \
  -H "Authorization: Bearer your-access-api-key"

# 4. 测试推理
curl -k https://YOUR_SERVER_IP/v1/chat/completions \
  -H "Authorization: Bearer your-access-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"你好"}]}'

# 5. 访问管理面板（浏览器打开，需信任自签名证书）
# https://YOUR_SERVER_IP/ui
```

**常见问题排查**:

| 现象 | 原因 | 解决 |
|------|------|------|
| 502 Bad Gateway | model-proxy 不在 personal_proxy 网络 | `docker network connect personal_proxy model-proxy` |
| Connection refused | model-proxy 容器未运行 | `docker compose up -d` |
| nslookup NXDOMAIN | 同上，DNS 无法解析容器名 | 确认网络连接 |
| 401 Unauthorized | API Key 不匹配 | 检查 `conf/config.yaml` 中的 `api_token` 或 `api_keys` |

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
