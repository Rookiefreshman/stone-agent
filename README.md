# Stone Agent — v0.7 出库诊断开发分支

本分支包含 v0.7 新增业务规则模块、v0.6.1 至 v0.7 的运行时升级补丁，以及新增的 34 项测试源码。**完整 v0.6.1 runtime 尚未导入远程仓库，本分支不是完整可部署工程。** 完整 v0.7 工程通过本次 ZIP 交付，导入后才能执行完整 CI。

- Stone 对应分支：https://github.com/Rookiefreshman/Stone/tree/agent/outbound-diagnostics-v0.7
- 新模块：`agent/integrations/outbound.py`
- 升级补丁：`patches/v0.6.1-to-v0.7.patch`（仅修改原有六个文件）
- 测试：`tests/test_outbound.py`（依赖完整 v0.6.1 基线）

在干净的 v0.6.1 本地工程上复制新增模块及测试，再执行 `git apply --check` / `git apply` 补丁后运行 `python -m pytest -q -rs`。本地完整工程已分别通过历史 176 项和新增 34 项测试，4 项 PG 集成因 DSN 缺失跳过；GitHub CI 尚未运行。新增出库诊断使用结构化 CLI/API，托管会话自然语言入口待开发。生产写操作关闭，不能把当前快照推断为历史失败原因。
