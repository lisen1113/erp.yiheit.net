# T011 共机测试部署

## 部署前基线

公开探测中，官网 HTTPS 返回 200，耗时 3.927 秒。ERP 域名 TLS 证书名称不匹配。实际服务器的入口、证书、容器和资源尚待核验。

现场变更前，用 `ss -ltnp` 确认 80/443 监听进程，记录 `docker ps`、`free -m`、`swapon --show`、`df -h`、`uptime`、`docker stats --no-stream`、内核 OOM 记录，以及两个官网域名 HTTP/HTTPS 响应状态和耗时。若入口为宿主机 Nginx，在本地查看 `nginx -T` 并记录两个官网配置和证书路径。部署后重复采集。

## ACME

当前共机域名为 erp.yiheit.net，运行环境为 test。先核对 DNS、官网入口形态和公网 80。仅在确认宿主机 Nginx 后安装独立 ERP HTTP 配置，创建 webroot，执行 `nginx -t` 后 reload，并从公网读取临时 challenge 测试文件。

使用 `certbot certonly --webroot -w /srv/yihe-erp/infra/acme --cert-name erp.yiheit.net -d erp.yiheit.net`。随后安装独立 ERP HTTPS 配置，执行 `nginx -t` 后 reload。Web 和 API 使用独立回环端口 13000 和 18000。确认两个官网域名及证书状态与部署前一致。

将 ERP 续期钩子安装为可执行文件 `/etc/letsencrypt/renewal-hooks/deploy/erp-reload`，确认 Certbot 定时任务并执行 `certbot renew --cert-name erp.yiheit.net --dry-run --run-deploy-hooks`。复查官网及 ERP 可用性。手工续期用 `certbot renew --cert-name erp.yiheit.net`。到期检查用 `openssl x509 -in /etc/letsencrypt/live/erp.yiheit.net/fullchain.pem -noout -enddate`。失败时检查公网挑战路径和 Nginx 验证结果。HTTPS 稳定后再考虑 ERP 专用 HSTS。

## 容量门禁

连续 15 分钟 RAM 超过 80%，Swap 持续增长、OOM、官网 502/504、响应明显变慢或 ERP 容器频繁重启时，停止 ERP 重服务并记录证据。正式环境需要独立服务器，至少 4C8G。

## 现场验收

需要独立测试部署 SSH 凭据和核验过的主机公钥。现场基线、证书签发及续期、官网部署后回归、真实 HTTPS 认证、Seed、MinIO、ClamAV、备份恢复和资源数据均待验证。

## 测试运行和待核验项目

部署目录为 /srv/yihe-erp。将当前 CI 成功的 main 提交放在 app，独立测试环境文件放在 env/test.env，权限设为 0600。设置 ERP_ENV=test、ERP_POSTGRES_IMAGE、ERP_REDIS_IMAGE、ERP_API_IMAGE、ERP_WEB_IMAGE、ERP_WORKER_IMAGE 为对应配置和镜像摘要。另设独立数据库凭据、T005 JWT/MFA 密钥和审计 HMAC 密钥。PostgreSQL 数据卷、Redis、API、Web 与默认 Celery worker 归属 yihe-erp-test 项目。import、export、print 队列按需启动，单 worker 并发为 1；当前 print worker 仅提供队列进程，打印与 Chromium 作业由后续打印任务接入。

按需文件验收使用 infra/compose.test.storage.yaml 加载 MinIO/ClamAV，事先准备独立存储环境文件和已验证摘要。现场要验证版本化桶、合成 PNG、HEAD、哈希、下载、容器重建、ClamAV EICAR 检测及失联和超时。当前 overlay 适合容器内对象存储验证；浏览器签名 URL 所需的 HTTPS 对象入口仍须结合现场方案验证。

GitHub Environment test 配置 TEST_SSH_HOST、TEST_SSH_PORT、TEST_SSH_USER、TEST_SSH_PRIVATE_KEY、TEST_SSH_KNOWN_HOSTS。手动工作流要求 main 的同一提交 CI 成功，且现场预先准备同一提交和镜像。脚本先检测官网与磁盘，再启动隔离数据库并备份，显式迁移，最后验证 HTTPS health、readiness、TEST 横幅和官网。

Seed 使用 yihe_erp_seed_ 前缀的独立测试数据库。现场由受控临时方式向 Compose 的一次性 API 容器传入 ERP_SEED_DEFAULT_PASSWORD，运行 python -m yihe_api.cli seed-dev。不要在长驻 API 环境文件中保存 Seed 密码。用户认证和 MFA 验收完成后通过受控入口管理测试账号。

备份脚本生成 UTC 命名的 gzip 归档与 SHA-256；恢复脚本在独立恢复库还原并核对十张表行数。实际恢复仍需核对关键固定 Seed UUID，并用连接到恢复库的隔离 API 确认数据。定时策略应每天运行备份，并保留最近七个日归档和四个周归档。
