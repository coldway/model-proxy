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

## Docker 部署

### Dockerfile

在项目根目录创建 `Dockerfile`：

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY conf/providers_catalog.yaml conf/providers_catalog.yaml
COPY main.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 构建与运行

```bash
# 构建镜像
docker build -t model-proxy:latest .

# 运行容器
docker run -d \
  --name model-proxy \
  -p 8000:8000 \
  -v $(pwd)/conf/config.yaml:/app/conf/config.yaml:ro \
  -v $(pwd)/data:/app/data \
  model-proxy:latest
```

关键点说明：
- `conf/config.yaml` 通过 volume 挂载注入，镜像内不包含任何 API Key
- `data/` 目录挂载以持久化请求历史（`request_history.jsonl`）
- `providers_catalog.yaml` 已在构建时复制进镜像，如需动态修改也可改为 volume 挂载

### Docker Compose

```yaml
version: "3.9"

services:
  model-proxy:
    build: .
    container_name: model-proxy
    ports:
      - "8000:8000"
    volumes:
      - ./conf/config.yaml:/app/conf/config.yaml:ro
      - ./data:/app/data
    restart: unless-stopped
    environment:
      - PYTHONUNBUFFERED=1
```

```bash
docker compose up -d
```

---

## 生产环境部署

### 1. 服务配置调优

编辑 `conf/config.yaml` 中的 `settings`：

```yaml
settings:
  host: "0.0.0.0"       # 生产环境监听所有接口
  port: 8000
  default_provider: "google"
  auto_switch: true
  log_level: "warning"   # 生产环境降低日志等级
```

### 2. 使用 Uvicorn 单 Worker 运行（Linux/macOS/Windows）

> **⚠️ 重要：** 本项目为**单 Worker 进程设计**，所有状态（dispatcher、rate_limiter、history、session 等）以进程内单例持有。
> 不支持 `uvicorn --workers N` 或 `gunicorn --workers N` 多进程模式（会导致状态不一致）。
> 如需水平扩展，应在反向代理层做负载均衡（多实例各自独立），或改用外部存储（Redis 等）管理共享状态。

```bash
# 生产环境推荐：单 worker + 反向代理（Nginx/Caddy）
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1

# 或使用 Gunicorn 管理单 Worker（支持优雅重启）
pip install gunicorn
gunicorn main:app \
  --workers 1 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile -
```

### 3. 反向代理

#### Caddy（推荐，自动化配置、无需域名）

编辑 `/etc/caddy/Caddyfile`：

```caddyfile
# model-proxy - IP 访问模式（无域名/HTTPS）
:80 {
    # API 接口 + SSE 流式输出
    handle /v1/* {
        reverse_proxy localhost:8000 {
            transport http {
                read_timeout 0
            }
            flush_interval -1
        }
    }

    # Web 管理面板
    handle /ui* {
        reverse_proxy localhost:8000
    }

    # Swagger 文档
    handle /docs* {
        reverse_proxy localhost:8000
    }
    handle /openapi.json {
        reverse_proxy localhost:8000
    }

    # 默认代理
    handle {
        reverse_proxy localhost:8000
    }
}
```

```bash
sudo systemctl restart caddy
```

> **关键**: `flush_interval -1` 确保 SSE 流式输出不被 Caddy 缓冲。

#### Nginx

推荐在前端放置 Nginx，处理 TLS 终止和请求缓冲：

```nginx
server {
    listen 443 ssl;
    server_name proxy.example.com;

    ssl_certificate     /etc/ssl/certs/proxy.crt;
    ssl_certificate_key /etc/ssl/private/proxy.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式输出需要禁用缓冲
        proxy_buffering off;
        proxy_cache off;
    }
}
```

### 4. Systemd 服务（Linux）

```ini
[Unit]
Description=Model Proxy - 免费大模型推理代理
After=network.target

[Service]
Type=simple
User=model-proxy
WorkingDirectory=/opt/model-proxy
Environment=PATH=/opt/model-proxy/.venv/bin
ExecStart=/opt/model-proxy/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp model-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now model-proxy
```

---

## 安全建议

| 措施 | 说明 |
|------|------|
| **API Key 保护** | `conf/config.yaml` 禁止提交 Git，Docker 部署时通过 volume 或环境变量注入 |
| **网络隔离** | 生产环境建议 Uvicorn 仅监听 127.0.0.1，由 Nginx 统一对外 |
| **TLS 加密** | 通过 Nginx 或云负载均衡器添加 HTTPS |
| **访问控制** | 可在 Nginx 层添加 IP 白名单或 Basic Auth |
| **日志审计** | `data/request_history.jsonl` 不记录消息内容，仅存元数据 |

---

## 健康检查

容器编排和负载均衡可使用以下端点进行健康检查：

```bash
# 检查服务是否存活
curl http://127.0.0.1:8000/v1/models

# 检查使用量（验证调度器正常）
curl http://127.0.0.1:8000/v1/usage
```

Docker 健康检查示例：

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8000/v1/models || exit 1
```

---

## 快速独立部署（完整步骤）

适用于将 model-proxy 作为独立服务部署到服务器（非 Docker 方式）。

### Step 1: 上传代码

```bash
rsync -avz --exclude='__pycache__' --exclude='.venv' --exclude='data/' \
  ./AI-agent/model-proxy/ user@SERVER_IP:/opt/model-proxy/
```

### Step 2: 安装依赖

```bash
ssh user@SERVER_IP
cd /opt/model-proxy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 3: 配置

```bash
# 编辑配置，确保 host 为 0.0.0.0 或 127.0.0.1（取决于是否使用反向代理）
vim conf/config.yaml
```

关键配置项：

```yaml
settings:
  host: "127.0.0.1"    # 有 Caddy/Nginx 时用 127.0.0.1，无则 0.0.0.0
  port: 8000
  log_level: "warning"
  auto_switch: true
```

### Step 4: 配置 Systemd

```bash
sudo tee /etc/systemd/system/model-proxy.service << 'EOF'
[Unit]
Description=Model Proxy - LLM 推理代理
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/model-proxy
Environment=PATH=/opt/model-proxy/.venv/bin:/usr/bin
ExecStart=/opt/model-proxy/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now model-proxy
sudo systemctl status model-proxy
```

### Step 5: 配置 Caddy

```bash
sudo tee /etc/caddy/Caddyfile << 'EOF'
:80 {
    handle /v1/* {
        reverse_proxy localhost:8000 {
            transport http {
                read_timeout 0
            }
            flush_interval -1
        }
    }
    handle {
        reverse_proxy localhost:8000
    }
}
EOF

sudo systemctl restart caddy
```

### Step 6: 验证

```bash
# 检查服务状态
curl http://SERVER_IP/v1/models

# 测试推理
curl http://SERVER_IP/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"你好"}]}'
```

---

## 与 Novel Engine 联合部署

如果 model-proxy 与 Novel Engine 在同一台服务器：

**方式 A**: Docker Compose 内部通信（推荐）

Novel 的 `docker-compose.prod.yml` 已包含 model-proxy 服务。设置：

```env
MODEL_PROXY_URL=http://model-proxy:8000/v1
```

**方式 B**: 宿主机独立 Systemd + Docker 互通

model-proxy 作为宿主机 Systemd 服务运行，Novel Docker 容器通过 `host.docker.internal` 访问：

```env
MODEL_PROXY_URL=http://host.docker.internal:8000/v1
```

Docker Compose 中需添加：

```yaml
services:
  novel-api:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

---

## 一键部署脚本 (deploy.sh)

项目根目录提供 `deploy.sh`，封装所有部署操作。

### 首次部署

```bash
cd /opt/model-proxy

# 1. 确认 API Keys 配置
vim conf/config.yaml

# 2. 一键安装（创建 venv + 安装依赖 + 注册 Systemd + 启动 + 验证）
bash deploy.sh install

# 3. 可选：配置 Caddy 反向代理（80 端口对外）
bash deploy.sh caddy
```

### 命令参考

| 命令 | 用途 |
|------|------|
| `bash deploy.sh install` | 首次安装（Python 环境 + Systemd + 启动） |
| `bash deploy.sh start` | 启动服务 |
| `bash deploy.sh stop` | 停止服务 |
| `bash deploy.sh restart` | 重启服务 |
| `bash deploy.sh status` | 查看运行状态（PID、内存、启动时间） |
| `bash deploy.sh logs` | 查看实时日志（journalctl） |
| `bash deploy.sh logs 100` | 查看最近 100 行日志 |
| `bash deploy.sh update` | 代码更新后重装依赖并重启 |
| `bash deploy.sh caddy` | 安装并配置 Caddy 反向代理 |
| `bash deploy.sh test` | 测试推理是否正常（检查模型数 + 发送测试请求） |

### 脚本功能说明

**`install` 自动执行的操作**:
1. 检查/安装 Python3
2. 创建 `.venv` 虚拟环境
3. 安装 `requirements.txt` 依赖
4. 自动将 `conf/config.yaml` 的 `host` 改为 `0.0.0.0`
5. 创建 `data/` 目录（持久化请求历史）
6. 注册 Systemd 服务（`model-proxy.service`）
7. 启动服务并验证

**`caddy` 自动执行的操作**:
1. 检测并安装 Caddy（支持 amd64/arm64）
2. 写入 Caddyfile（SSE 流式支持 `flush_interval -1`）
3. 重启并 enable Caddy

**`test` 验证内容**:
1. 调用 `/v1/models` 确认服务可达，输出可用模型数
2. 发送测试推理请求，确认能正常响应

### 安全检查

- 脚本在启动前检查 `conf/config.yaml` 是否存在
- 如果未发现任何 API Key，输出警告提示
- 生产环境自动关闭 `reload=True`（通过 Systemd 的 uvicorn 直接运行）

### 日志管理

使用 Systemd journal 管理日志：

```bash
# 实时查看
bash deploy.sh logs

# 查看最近 1 小时
sudo journalctl -u model-proxy --since "1 hour ago"

# 导出日志
sudo journalctl -u model-proxy --since today > /tmp/model-proxy-today.log
```

### 升级流程

```bash
cd /opt/model-proxy

# 1. 拉取/上传最新代码
git pull  # 或 rsync

# 2. 一键升级（自动重装依赖 + 重启）
bash deploy.sh update
```

