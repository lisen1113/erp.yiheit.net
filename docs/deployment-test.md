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

## GitHub Actions 首次部署

部署分支临时允许 main 和 codex/t011-test-deployment，要求指定完整 SHA 与触发事件、成功的 push CI、检出提交完全一致。部署任务使用 test Environment 和独立并发锁。PR #11 合并后恢复仅允许 main，并从 test 的部署分支规则移除临时分支。

GitHub 要求 workflow_dispatch 的工作流文件已经存在于默认分支。当前 PR #11 保持 Draft，main 尚未注册 deploy-test.yml，首次点按或 gh dispatch 仍受这一条件阻断。要在 #11 合并前执行，需另行批准在 main 注册同名、仅触发且不含部署步骤的入口。该入口可以通过独立 PR 审查；实际部署逻辑由选择的 T011 分支提供。

### 一次性配置

推荐在独立受控 Linux 主机上注册仓库 self-hosted runner，添加 yihe-erp-test 标签。该主机需要 Bash、Git、Docker、OpenSSH 及到测试主机 22 端口的已授权网络路径。将 runner 限于受信任仓库工作流。仓库的 PR CI 继续使用 GitHub-hosted runner。备选方案是有固定出口或受控私网连接的 GitHub-hosted runner，只允许该来源按维护窗口访问 SSH，完成后撤销临时访问。不使用全网 SSH 白名单。

生成独立部署 Key：

    ssh-keygen -t ed25519 -f ~/.ssh/yihe-erp-github-test -C yihe-erp-github-test -N ''

将 yihe-erp-github-test.pub 的单行加入授权部署用户的 authorized_keys。可按 runner 固定源地址使用 from= 限制，并禁用端口、agent 和 X11 转发及 PTY。该部署账户需要批准的非交互 sudo，且能在服务器上使用 Docker、Nginx、Certbot、curl、jq、OpenSSL 和 Python 3。独立 Key 的无扩展名文件直接填入 GitHub Environment Secret，禁止复用个人密钥或发到聊天。

从已核验的本机 known_hosts 提取主机条目。先核对指纹，再复制完整行，禁止直接信任未核验的 ssh-keyscan 输出：

    ssh-keygen -F 106.54.124.168 -f ~/.ssh/known_hosts
    ssh-keygen -F 106.54.124.168 -f ~/.ssh/known_hosts | grep -v '^#' | ssh-keygen -lf -

若使用自定义端口，查询名称使用 [服务器地址]:端口，并与 TEST_SSH_HOST 和 TEST_SSH_PORT 一致。

在 Settings → Environments → test 中限制 deployment branches 仅允许 main 与 codex/t011-test-deployment，并设置：

| 名称 | 类型与用途 |
| --- | --- |
| TEST_SSH_HOST | Secret，测试服务器地址 |
| TEST_SSH_PORT | Secret，SSH 端口 |
| TEST_SSH_USER | Secret，授权部署账户 |
| TEST_SSH_PRIVATE_KEY | Secret，独立 CI 部署私钥全文 |
| TEST_SSH_KNOWN_HOSTS | Secret，已核验主机公钥条目全文 |
| TEST_ACME_EMAIL | Variable，首次签发所用的运维邮箱 |
| TEST_RUNNER_LABEL | 可选 Variable，默认 yihe-erp-test |

PostgreSQL、JWT、MFA、审计 HMAC 和 Seed 凭据由目标服务器首次生成，权限为 0600，后续复用。应用镜像由 runner 从精确提交构建并通过 SSH 分发，服务器无需额外镜像仓库 Secret。

### 触发

注册默认分支入口后，Actions → Deploy ERP test → Run workflow。选择 codex/t011-test-deployment，填入已通过 CI 的完整 SHA，首次勾选 bootstrap_https。等价命令：

    gh workflow run deploy-test.yml --ref codex/t011-test-deployment -f commit_sha=完整40位SHA -F bootstrap_https=true

后续从允许分支执行同一操作时将 bootstrap_https 改为 false。后续模式要求 ERP 站点、证书、部署指针和受保护环境文件已存在，直接复用。已有未知配置、证书或项目资源时，首次模式停止。
