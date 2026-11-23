# T011 测试部署入口

## 默认分支注册

`main` 上的 `.github/workflows/deploy-test.yml` 注册 GitHub Actions 手动入口。它校验目标分支白名单、输入的完整 SHA、目标分支当前 tip 及其成功的 push CI，然后在 `test` Environment 记录部署意图。本入口没有 SSH、部署密钥读取、远程执行或部署转发步骤。

输入包括 `commit_sha`、`bootstrap_https`、`target_ref`。允许的目标为 `main` 和 `codex/t011-test-deployment`。所有运行与实际部署共用 `yihe-erp-test-deploy` 并发组。

## 后续首次部署

默认分支入口合并后，实际部署应显式选中 `codex/t011-test-deployment` 作为工作流运行分支。GitHub 使用该分支的工作流定义。填写该分支已通过 CI 的完整 SHA，并启用 `bootstrap_https`。该分支的工作流用 `--ref` 选择运行版本，自行验证触发 SHA、CI 和实际 checkout。其实际部署输入为 `commit_sha` 和 `bootstrap_https`。在默认分支注册入口填写 `target_ref` 只做核验和记录。

`test` Environment 应限制可部署分支仅为两条白名单分支，并对部署分支实施受控写入及审查。T011 验收结束且完整部署代码进入 `main` 后，移除临时分支的权限。

## Runner 门禁

推荐长期使用带 `yihe-erp-test` 标签的仓库专用 self-hosted runner，安装于独立受控主机。实际部署前验证 runner 能从已授权的来源访问服务器 SSH。入口注册无需该 runner 在线，也不验证网络连通性。腾讯云 SSH 白名单按现有授权范围管理。
