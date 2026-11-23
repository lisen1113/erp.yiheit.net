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
