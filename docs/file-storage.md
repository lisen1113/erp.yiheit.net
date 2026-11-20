# T009 文件对象存储与品牌版本

PostgreSQL 只存元数据。业务通过 Storage 接口签名 PUT/GET、读取语义化元数据、按版本读及删除指定版本。MinIO 使用 SigV4 path-style，COS 使用 HTTPS 虚拟主机式 SigV4 和带 APPID 的私有桶。存储端必须开启版本控制；COS 走兼容接口，本任务不使用真实云账号，上线前由运维在测试桶验证已配置的 API、版本头和 IAM。生产账号限于所需对象读取与上传权限，普通 API 无对象删除调用。参考：https://www.tencentcloud.com/document/product/436/34688

本地通过 `docker compose -f compose.yaml -f infra/compose.migrations.yaml -f infra/compose.storage.yaml up -d --build --wait` 加入 MinIO。对象服务仅绑定宿主机 127.0.0.1，使用独立命名卷，管理端口不映射。创建私有 bucket 后开启 versioning。配置 ERP_STORAGE_PROVIDER、ERP_STORAGE_REGION、ERP_STORAGE_BUCKET、ERP_STORAGE_ACCESS_KEY、ERP_STORAGE_SECRET_KEY；临时凭证可配 ERP_STORAGE_SESSION_TOKEN。MinIO 的 ERP_STORAGE_ENDPOINT 为容器内地址，ERP_STORAGE_PUBLIC_ENDPOINT 为浏览器可达地址。本地浏览器上传需对象服务的 CORS 允许 ERP UI 的准确 Origin 和 Content-Type。生产 COS 按运维配置同源名单，不开启公开读取。扫描使用 ERP_CLAMD_HOST 与 ERP_CLAMD_PORT（默认 3310），ClamAV 需配置满足 50 MiB 对象扫描且不超过资源上限。

上传先 POST /api/v1/files 创建幂等回执，再由原上传人 POST /files/{id}/upload-ticket 领取最多 180 秒的 PUT 签名。签名绑定长度、MIME 和服务端生成 key `company/{company_id}/{yyyy}/{mm}/{asset_type}/{uuid4}`。浏览器 PUT 时保留签名约束的 MIME 并发送原文件。POST /files/{id}/confirm 用版本号和幂等 Key。确认先定位精确版本，再读不可变的 versionId、验证长度 SHA-256 及声明 MIME，扫描和解析通过后绑定该版本。旧上传票据即使写出新版本，已确认绑定仍保持。幂等回执只存 UUID 和固定状态，签名独立领取。确认缓存命中不会再次扫描。存储故障不提交确认，扫描未通过或不可用进入 QUARANTINED；格式错误或摘要不符进入 REJECTED。

品牌仅 PNG/JPEG/WebP，拒绝 SVG。附件支持这些图片以及 PDF、DOCX、XLSX、UTF-8 CSV。普通与导入上传分别上限 50、20 MiB，品牌 10 MiB，可配置更低上限。拒绝双扩展、路径穿越、伪装可执行文件、宏 Office、外链关系、ZIP 高压缩比或超出总大小。通用 ZIP、迁移大文件和正式导出生成由后续专用入口接入。扫描前需通过元数据和哈希校验。ClamAV 无响应或不确定返回值不能当作通过；CI 用测试限定扫描器验证通过路径，同时测试实际不可用逻辑。应用扫描的 EICAR 标准测试文件摘要可立即隔离。

文件查询与签名下载按公司、用途、文件状态和敏感权限检查。下载签发绑定用户、文件和版本的同源网关 URL，普通 300 秒，敏感 60 秒。获取正文时仍需 Bearer 并再次校验权限和状态；正文由网关读取私有对象版本并复核 SHA-256，设置 attachment、nosniff 和 no-store。这样对象存储无需支持设置 nosniff。没有外部地址或原始存储 key 入 API 回执、日志或审计。可选公开入口 /api/v1/brand-assets/public/logo 只读 ERP_PUBLIC_BRAND_COMPANY_ID 对应的有效中文横版公开品牌图片，停用后立即不再返回旧图。

品牌每公司每代码版本唯一，类型、语言同一时间只能有一个启用版本。启用时间从现在开始，停用保存结束日期；历史版本不可重新开启，应创建新版本。启用、停用、失效需版本号、幂等与同事务 T007 审计。权限来自 T006，SYSTEM_ADMIN 有普通管理和品牌管理，MANAGEMENT 有品牌查看，没有默认敏感附件及迁移权限。敏感文件另要求 file_asset.view_sensitive。

保留 UPLOADING、AVAILABLE、QUARANTINED、REJECTED、EXPIRED、INVALIDATED、DELETED_FROM_STORAGE 状态。生命周期接口仅标记已超时的未确认上传并检查清理资格。已确认、存在品牌历史或未来外部引用、法定保留的对象拒绝清理。版本桶不得设置可能清掉已绑定版本的全桶清理规则。将来业务快照应保存 file_asset_id 并在事务内检查可用性，清理前核对外部引用。丢失响应可用同一幂等 Key 重放；存储不可用先恢复连通，隔离文件由受控流程核查，不直接公开。

规格要求使用用户提供的正式原图。本任务的 `uv run --frozen python scripts/seed/brand_sample.py local-test-logo.png` 仅生成醒目标记的测试图。正式资料由管理员核验和上传，不在迁移里写入真实 Logo 或公司银行信息。

依赖 boto3 1.44.7（Apache-2.0，用于签名和版本存储）；Pillow 13.0.0（MIT-CMU，用于图片验证）。MinIO 开发容器为 AGPL-3.0，不随生产服务发布。
