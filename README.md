# Stone Agent v0.9.1

完整 Python Runtime、Session、Worker、Registry、HTTP/LOCAL 诊断、SQLite/PostgreSQL 适配、Alembic、测试及 CI 已导入本开发分支。保留 v0.9 的取消预检和合成审批演练；v0.9.1 新增只读操作回执验证与可序列化对账检查点，不包含实际写 dispatcher 或真实审批授权。

- Python 3.11+：`pip install -r requirements.txt`，`python -m pytest -q -rs`
- 本地验证：273 历史测试通过、18 新增通过，累计 291；5 项 PostgreSQL 测试缺少 DSN 跳过。真实 CI、Stone/Maven/MySQL、LLM 尚未验收。
- 启动与部署见 [INTEGRATION](docs/INTEGRATION.md)、[PERSISTENCE_WORKER](docs/PERSISTENCE_WORKER.md)、[SESSION_PROTOCOL](docs/SESSION_PROTOCOL.md) 和 [CANCEL_REHEARSAL_V09](docs/CANCEL_REHEARSAL_V09.md)。
- 诊断：[库存回执](docs/DISPATCH_HISTORY_V061.md)、[出库](docs/OUTBOUND_DIAGNOSTIC_V07.md)、[WES](docs/WES_DIAGNOSTIC_V08.md)。
- 本轮：[回执协议](docs/OPERATION_RECEIPTS_V091.md)、[项目实施清单](docs/PROJECT_CHECKLIST.md)。
- Stone：[业务开发分支](https://github.com/Rookiefreshman/Stone/tree/agent/operation-receipts-v0.9.1)。
- Agent PostgreSQL head 仍为 0004_cancel_drafts；Stone V5 是独立 MySQL schema。不得将现有演练草案作为真实操作发送。

开发环境使用独立测试凭据、合成数据或授权测试仓库，不提交真实 token、数据库、业务记录。保持真实写入关闭。
