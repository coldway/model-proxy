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

### 2. 使用 Gunicorn + Uvicorn Workers（Linux/macOS）

生产环境建议使用 Gunicorn 管理多 Worker 进程：

```bash
pip install gunicorn

gunicorn main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile -
```

> Windows 不支持 Gunicorn，可直接使用 Uvicorn：`uvicorn main:app --host 0.0.0.0 --port 8000`

### 3. 反向代理（Nginx）

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

### 5. macOS launchd 服务（开机自启 + 崩溃拉起 + 代码变更重启）

macOS 原生方案，零额外依赖（仅需 fswatch 用于双保险代码监听）。

> **隐私说明**：launchd 配置文件（plist、管理脚本）包含用户名和本地路径等私密信息，
> 已从仓库 `.gitignore` 中排除。文件保存在 `~/.config/model-proxy-launchd/`。

#### 架构

```
launchd (macOS 原生进程管理)
  ├── com.<username>.model-proxy        ← 主服务（开机自启 + 崩溃拉起）
  │     └── uvicorn reload=True         ← 代码变更自动热重载（内置）
  └── com.<username>.model-proxy-watcher ← fswatch 文件监听（可选双保险）
        └── watch-and-restart.sh        ← 检测 .py/.yaml 变更后 kickstart 重启
```

三层保障：
- **开机自启**：launchd `RunAtLoad=true`，开机后自动启动
- **崩溃拉起**：launchd `KeepAlive`，进程异常退出后 3 秒自动重启
- **代码变更重启**：uvicorn `reload=True`（主）+ fswatch（备）

#### 文件存储位置

| 位置 | 文件 | 说明 |
|------|------|------|
| `~/.config/model-proxy-launchd/` | `*.plist`, `*.sh` | 私密配置主副本（不提交 Git） |
| `~/Library/LaunchAgents/` | `com.<username>.model-proxy*.plist` | launchd 加载位置（由 manage.sh 自动复制） |

#### 管理命令

```bash
cd ~/.config/model-proxy-launchd/
./manage.sh install    # 安装并启动服务
./manage.sh uninstall  # 卸载全部 launchd 服务
./manage.sh start      # 启动服务
./manage.sh stop       # 停止服务
./manage.sh restart    # 重启 model-proxy
./manage.sh status     # 查看服务状态
./manage.sh logs       # 查看实时日志
```

#### 日志位置

```
<project>/logs/
├── model-proxy.log         # 应用日志（Python RotatingFileHandler，50MB 轮转）
├── model-proxy.stderr.log  # launchd 捕获的未处理错误
├── watcher.stdout.log      # fswatch 监听输出
├── watcher.stderr.log      # fswatch 监听错误
└── watch-restart.log       # 代码变更重启记录
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
