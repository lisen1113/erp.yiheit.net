# T002 本地 Docker 开发环境

在仓库根目录执行。需要 Docker Engine 和支持 `up --wait` 的 Docker Compose v2。首次启动需要下载镜像与项目依赖。

1. 复制 `.env.example` 为 `.env`（PowerShell：`Copy-Item .env.example .env`，Bash：`cp .env.example .env`）。
2. 在 `.env` 设置唯一的本地开发密码。可用 `openssl rand -hex 24` 生成。保持该文件在本地。
3. 执行 `docker compose up -d --build --wait`。

| 操作 | 命令 |
| --- | --- |
| 状态 | `docker compose ps` |
| 停止 | `docker compose stop` |
| 重启已停止的服务 | `docker compose up -d --wait` |
| 重建容器与应用镜像 | `docker compose up -d --build --force-recreate --wait` |
| 查看日志 | `docker compose logs -f` 或 `docker compose logs -f api nginx` |
| 移除容器与网络并保留数据卷 | `docker compose down` |

默认入口为 [Web](http://127.0.0.1:8080/) 和 [API 健康检查](http://127.0.0.1:8080/api/health)。前端镜像构建后运行 Next.js，代码修改需要重建对应镜像。

- `edge` 网络连接 Nginx、Web 和 API。`data` 为内部网络，连接 API、PostgreSQL 和 Redis。只有 Nginx 发布宿主机端口，且绑定 IPv4 回环地址。
- PostgreSQL 挂载命名卷 `postgres-data` 至 `/var/lib/postgresql/data`。重建容器和普通 `down` 会保留数据。带 `--volumes` 的 `down` 会删除数据卷。
- PostgreSQL 健康检查执行经身份验证的 TCP 查询，Redis 检查 PONG，API 和 Web 检查各自 HTTP 入口，Nginx 检查两条代理路径。API 的 `/health` 为进程存活检查。
- Redis 为内部服务。账务事实的持久化归 PostgreSQL。

| 变量 | 含义 |
| --- | --- |
| `POSTGRES_DB` | PostgreSQL 初始化数据库名，必填 |
| `POSTGRES_USER` | PostgreSQL 本地初始化用户，必填 |
| `POSTGRES_PASSWORD` | 本地随机密码，必填且无默认值 |
| `ERP_DEV_PORT` | Nginx 本地端口，默认 8080 |

PostgreSQL 镜像只在初始化空卷时应用上述三个数据库变量。已有数据时，修改 .env 不会修改数据库中的用户或密码。此 Compose 专供本地开发，测试与生产各自管理配置。

在 Bash 环境运行 `bash infra/verify-compose.sh` 执行与 GitHub Actions 相同的验收。它需要 curl、openssl 和空闲的 18080 端口，使用单独的 Compose 项目和临时凭据，退出时清理该测试项目及其数据卷。
