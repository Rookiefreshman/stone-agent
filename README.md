# Stone Agent — v0.8 WES 调度诊断

本分支继承 v0.7 增量模块，并包含 v0.8 WES 规则、测试与可应用升级补丁。完整历史 runtime 仍未导入 GitHub，因此当前远程分支不是完整可运行工程；使用本轮完整 ZIP 或在已验收的 v0.7 基线上应用补丁。真实 PG/MySQL/LLM 联调尚未完成。

- Stone 对应分支：https://github.com/Rookiefreshman/Stone/tree/agent/wes-diagnostics-v0.8
- 新模块：agent/integrations/wes.py；tests/test_wes.py；patches/v0.7-to-v0.8.patch
- 本地完整测试：251 passed / 4 skipped，新增41项，历史210项；三份增量文件 Git Blob SHA 与本地一致。
- 在 v0.7 完整工程复制新增源码和测试，git apply --check 并应用补丁，再运行 python -m pytest -q -rs。
- 当前为 Public 仓库，严禁提交真实数据或凭据；仅结构化查询，不允许业务写入。
