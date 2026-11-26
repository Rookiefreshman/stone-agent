# Stone Agent v0.9.11

完整 Python Runtime、Session、Worker、Registry、HTTP/LOCAL 诊断、SQLite/PostgreSQL、Alembic 和 CI。v0.9.2 新增仅合成数据的持久化故障演练：一次性模拟审批消费、持久模拟发送意图、租约/fencing、迟到回执和有界对账。无真实写 dispatcher 或生产审批授权。

- Python 3.11+：`pip install -r requirements.txt`，`python -m pytest -q -rs`
- CI：[v0.9.10 代码验收 395 passed，1 warning](https://github.com/Rookiefreshman/stone-agent/actions/runs/45774817602)；单轮合成33/44，多轮14/14场景、30/30回合，错误确认0。保留1条 Starlette/AnyIO 上游弃用警告；真实模型未配置。
- 数据库：Agent PostgreSQL head 为 0007_receipt_review；旧版 API/Worker 停止、备份后运行 alembic upgrade head。Stone MySQL V6 不变。
- 启动与部署见 [INTEGRATION](docs/INTEGRATION.md)、[PERSISTENCE_WORKER](docs/PERSISTENCE_WORKER.md)、[SESSION_PROTOCOL](docs/SESSION_PROTOCOL.md)。
- 诊断：[历史回执](docs/DISPATCH_HISTORY_V061.md)、[出库](docs/OUTBOUND_DIAGNOSTIC_V07.md)、[WES](docs/WES_DIAGNOSTIC_V08.md)。
- 本轮：[故障演练 SOP](docs/RECONCILIATION_REHEARSAL_V092.md)、[项目实施清单](docs/PROJECT_CHECKLIST.md)。
- Stone：[业务开发分支](https://github.com/Rookiefreshman/Stone/tree/agent/business-language-v0.9.7)。

Stone Java 8 / MySQL 5.7 与 8.0 各38项 Maven 测试及6组真实 HTTP 契约检查已在隔离 CI 通过（[Stone CI](https://github.com/Rookiefreshman/Stone/actions/runs/45762289693)）。目标部署环境、真实 LLM、企业授权和生产部署仍待验收。合成演练状态不等于实际业务提交；所有真实写入继续关闭。不要提交真实 token、数据库、业务数据。

- v0.9.3：[实际 Java/MySQL 与跨服务验收](docs/BUSINESS_VALIDATION_V093.md)。Agent 客户端代码提交 [305 passed](https://github.com/Rookiefreshman/stone-agent/actions/runs/45762213821)。

- v0.9.4：[审批绑定与业务域唯一消费](docs/APPROVAL_CONSUMPTION_V094.md)。Stone 双 MySQL 各45项及6组实际 HTTP 检查通过，Agent 核心运行时未改动，生产授权与发送继续关闭。

- v0.9.5：[真实只读回执核对与恢复](docs/RECEIPT_RECOVERY_V095.md)，Agent head `0006_receipt_check`，Stone V6 不变。[317项 Agent CI](https://github.com/Rookiefreshman/stone-agent/actions/runs/45763923739)；[双 MySQL 45项及7组实际 HTTP](https://github.com/Rookiefreshman/Stone/actions/runs/45763988260)。维护人员提供的参考不是实际发送账本，真实写入关闭。

- v0.9.6：[UNKNOWN 人工复核与受控只读恢复](docs/MANUAL_RECEIPT_REVIEW_V096.md)。Agent head `0007_receipt_review`，Stone V6 不变；[Agent CI](https://github.com/Rookiefreshman/stone-agent/actions/runs/45765456480) / [Stone CI](https://github.com/Rookiefreshman/Stone/actions/runs/45765488016)。仅受控本地维护，真实业务写入关闭。

- v0.9.7：[自然语言业务目标与类型化确认](docs/BUSINESS_LANGUAGE_V097.md)。复用历史回执、出库、WES 只读工具，无新 schema；[修复后 Agent CI](https://github.com/Rookiefreshman/stone-agent/actions/runs/45766300805)，[Stone CI](https://github.com/Rookiefreshman/Stone/actions/runs/45766336138)。真实 LLM 与生产授权未验收。

- v0.9.8：[独立业务语义评测](docs/SEMANTIC_EVALUATION_V098.md)：44条人工合成黄金样例，`python -m agent.eval_goals --mode fixture`；真实模型模式显式配置后执行，未进行外部真实模型评测。无业务执行或 schema 变化。[CI](https://github.com/Rookiefreshman/stone-agent/actions/runs/45767119437)。

- v0.9.9：[多轮会话评测](docs/MULTITURN_SESSION_EVALUATION_V099.md)：`python -m agent.eval_sessions --mode fixture --fail-on-critical`。使用本地 SQLite 与 FixtureStoneService，不访问真实 Stone，不产生业务写入。

- v0.9.10：[模型 HTTP 协议与评测失败分类](docs/MODEL_HTTP_CONTRACT_V0910.md)；复用既有目标及执行策略，增加隔离 HTTP 合同测试与评测归因。真实模型未验收。

- v0.9.11：[人工复核预检与单次请求状态](docs/REVIEW_PREFLIGHT_V0911.md)。新增 review-preview、明确人工查询可用性、保留原 request_id 的查询状态与证据；GET 总预算不变，真实写入关闭。本轮代码 CI 待验收。
