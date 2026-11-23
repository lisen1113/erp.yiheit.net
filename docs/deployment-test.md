# T011 共机测试部署

**当前交付由 PR #11 统一维护。GitHub Actions 远程部署状态为 NOT YET ACTIVATED。** 首次现场部署在已授权的腾讯云 OrcaTerm/TAT 会话执行。运行记录应保留实际 Git commit SHA、脚本 SHA-256、官网前后基线及验收结果。

## A-1：ERP HTTP-only 与 ACME 探针

1. 从 PR #11 已通过 CI 的提交取得 `scripts/t011-a1-bootstrap-http.sh`。使用现有受控文件传输将该文件放到已登录服务器，并核对随 PR 提供的 SHA-256。
2. 在 OrcaTerm 中执行：

   ```bash
   sha256sum ./t011-a1-bootstrap-http.sh
   bash -n ./t011-a1-bootstrap-http.sh
   bash ./t011-a1-bootstrap-http.sh
   ```

3. 记录脚本输出的官网基线、Nginx 测试、ERP 503、探针 URL 和预期正文。再从服务器外部读取探针 URL，核对返回 200 且正文一致。将证据回传后再决定是否进入 A-2。

成功时脚本创建 ERP HTTP 站点和启用链接、ACME 目录和一个探针，以及时间戳 Nginx 备份。若发现已存在的 ERP 站点或指定目标，立即退出。写入后失败会撤销本次新增的 ERP 站点、探针和本次创建的空目录。已经尝试过 reload 时，回退也先通过 `nginx -t`，再 reload 并复查官网。备份保留供排查，原官网配置和证书保留原路径。A-1 只在服务器通过公网域名读取探针，外部复测单独记录。

| 路径 | A-1 用途 |
| --- | --- |
| `/etc/nginx/sites-available/erp.yiheit.net.conf` | ERP 专用 HTTP 站点 |
| `/etc/nginx/sites-enabled/erp.yiheit.net.conf` | ERP 站点启用链接 |
| `/srv/yihe-erp/infra/acme/.well-known/acme-challenge/t011-a1-<UTC时间>` | ACME 探针及必要的父目录 |
| `/root/t011-a1-nginx-<UTC时间>/nginx-before.tar` | 修改前 Nginx 配置备份 |

## GitHub Actions 部署治理

`deploy-test.yml` 使用 `workflow_dispatch`，输入为 `commit_sha`、`bootstrap_https` 和 `target_ref`。入口只接受 `main` 与 `codex/t011-test-deployment`，校验完整 40 位 SHA、目标分支当前 tip 和该 SHA 的成功 push CI。部署获准后再次校验分支 tip，检出并执行确切 SHA。工作流使用 `test` Environment 和 `yihe-erp-test-deploy` 并发组，不取消正在运行的部署。校验和记录任务不读取 SSH Secrets。

GitHub 要求手动入口先存在于默认分支。首次现场部署无需此入口。现场验收结束后，如需启用自动发布，应在独立受控 Linux 主机注册带 `yihe-erp-test` 标签的仓库专用 self-hosted runner，并验证它能从已授权来源访问测试服务器。仅在入口、runner、分支规则及凭据均经核验后，设置仓库变量 `TEST_DEPLOY_ACTIVATED=true`。变量未设置时，工作流最多记录部署意图，远程部署 Job 跳过。T011 收尾时将部署分支规则收紧为 `main`。

远程部署只在指定 Job 中读取 `TEST_SSH_HOST`、`TEST_SSH_PORT`、`TEST_SSH_USER`、`TEST_SSH_PRIVATE_KEY` 和 `TEST_SSH_KNOWN_HOSTS`。使用独立 CI 部署密钥与核验过的主机公钥，不复用个人 SSH 凭据。保持腾讯云 SSH 来源白名单。

## 后续阶段

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

2026-11-24，OrcaTerm 现场续跑总表为 `T011_SITE_RESULT=PASS`。现场固定提交为 `62612dfa85382fbd8f10b0ea19b1cd4137b8b6eb`，最终续跑脚本 SHA-256 为 `81978563aeecf5a4cb123f557022d6bc727e741c45df74db5dcaca50b06d9837`。

| 项目 | 现场结果 |
| --- | --- |
| 五个基础服务与数据库 | 预检、容器健康、迁移 head `0006_file_brand_assets` 和固定 Seed 均通过 |
| ERP HTTPS | 独立 Nginx 虚拟主机、现有证书和 Certbot dry-run 通过 |
| 官网回归 | HTTPS 主站 200，HTTP 和 www 三个入口均 301 到 HTTPS 主站 |
| 认证与审计 | 沿用前轮 HTTPS 认证审计 PASS。原 403 为验收脚本错误，安全门禁行为正确。system.audit.view requires MFA before audit log access. 修正版验证审计员 MFA 前 403、MFA 后 200、安全事件仍 403。管理员安全事件 200 用前轮证据 |
| 幂等性 | 沿用前轮创建、回放和冲突 PASS |
| MinIO 与 ClamAV | 版本化合成文件、重建后下载与哈希、正常文件、EICAR 和扫描器离线判定通过 |
| PostgreSQL 备份恢复 | 实际备份、独立恢复库、十张表行数、固定 Seed UUID 和恢复库迁移 head 通过 |
| 最终资源 | 现场资源与官网门禁通过。截图未展示主机 RAM、Swap、Load 和磁盘具体读数 |

## 测试运行和待核验项目

部署目录为 /srv/yihe-erp。将 PR #11 中已通过 CI 且已核对 SHA 的提交放在 app，独立测试环境文件放在 env/test.env，权限设为 0600。设置 ERP_ENV=test、ERP_POSTGRES_IMAGE、ERP_REDIS_IMAGE、ERP_API_IMAGE、ERP_WEB_IMAGE、ERP_WORKER_IMAGE 为对应配置和镜像摘要。另设独立数据库凭据、T005 JWT/MFA 密钥和审计 HMAC 密钥。PostgreSQL 数据卷、Redis、API、Web 与默认 Celery worker 归属 yihe-erp-test 项目。import、export、print 队列按需启动，单 worker 并发为 1；当前 print worker 仅提供队列进程，打印与 Chromium 作业由后续打印任务接入。

按需文件验收使用 infra/compose.test.storage.yaml 加载 MinIO/ClamAV，事先准备独立存储环境文件和已验证摘要。现场要验证版本化桶、合成 PNG、HEAD、哈希、下载、容器重建、ClamAV EICAR 检测及失联和超时。当前 overlay 适合容器内对象存储验证；浏览器签名 URL 所需的 HTTPS 对象入口仍须结合现场方案验证。

GitHub Environment test 配置 TEST_SSH_HOST、TEST_SSH_PORT、TEST_SSH_USER、TEST_SSH_PRIVATE_KEY、TEST_SSH_KNOWN_HOSTS。手动工作流要求 main 的同一提交 CI 成功，且现场预先准备同一提交和镜像。脚本先检测官网与磁盘，再启动隔离数据库并备份，显式迁移，最后验证 HTTPS health、readiness、TEST 横幅和官网。

Seed 使用 yihe_erp_seed_ 前缀的独立测试数据库。现场由受控临时方式向 Compose 的一次性 API 容器传入 ERP_SEED_DEFAULT_PASSWORD，运行 python -m yihe_api.cli seed-dev。不要在长驻 API 环境文件中保存 Seed 密码。用户认证和 MFA 验收完成后通过受控入口管理测试账号。

备份脚本生成 UTC 命名的 gzip 归档与 SHA-256；恢复脚本在独立恢复库还原并核对十张表行数。现场已通过恢复库的固定 Seed UUID 校验。定时策略应每天运行备份，并保留最近七个日归档和四个周归档。

## 首次现场镜像交付

GitHub Actions Artifact 存储配额优化维持 DEFERRED。API 和 Worker 使用现场已构建的固定提交镜像。Web 在 GitHub Actions 构建并发布至 GHCR，manifest digest 为 `sha256:82fd993a471b99123d533ededb272c0a0db09816e7ecb380679c229343e58b4f`。现场通过校验过的离线包导入 Web、MinIO 与 ClamAV。ERP HTTPS 复用既有 HTTP bootstrap、ACME 路径和证书。
