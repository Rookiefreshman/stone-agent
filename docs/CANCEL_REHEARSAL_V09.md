# v0.9 READY 作业取消预检与审批演练

## 业务边界
Stone cancelStockWork 已在锁仓事务中校验 READY 并释放对应库存预占；CANCELLED 状态重放不重复释放。当前缺乏独立 operation_id 可信回执与企业服务端授权。此版不修改 Stone 写事务，不注册写工具，不开放 execute 路由，不创建写 Job。审批仅对 SYNTHETIC_FIXTURE 开启，真实业务 HTTP 读取只能产生未派发草案。所有响应 write_allowed=false、dispatch_state=NEVER_DISPATCHED。草案 operation_id 不是 Stone 回执，EXPIRED 是查询时按有效期派生状态。Hash 用于可信数据库内的一致性校验，不是数字签名；管理员能同时改写数据和 Hash，生产应有审计隔离。

## 运行与迁移
停止旧 API/Worker、备份数据库。SQLite 自动建 operation_draft 与 operation_event。PostgreSQL 需 `alembic upgrade head` 至 0004_cancel_drafts；在线迁移待验证。冻结 SQL 位于 migrations/0004_agent_head_offline.sql，仅用于新库离线初始化，旧库走增量 Alembic。禁止混合版本 Worker。完整 ZIP 才是可运行基线，GitHub 分支目前为增量源码。

在隔离开发环境设置 POC_DEV_TOKEN 与 POC_REVIEW_TOKEN（至少16字符且不同）、POC_REHEARSAL_ENABLED=1、STONE_ALLOWED_WAREHOUSES=1、POC_REVIEW_WAREHOUSES=1，保持 STONE_LIVE_DOMAIN=0 和 POC_LIVE_MODEL=0；启动 uvicorn agent.api:create_app --factory --host 127.0.0.1 --port 18080。不要提交 token。默认审批关闭。

## 使用步骤（Swagger /docs）
1. 申请人 POST /api/v1/sessions/unmanaged/runs，client_request_id 为独立请求键，message 为查询说明，goal 为 READ_STOCK_WORK / CONFIRMED_STRUCTURED_INPUT，entity warehouse_id=1,task_id=101。
2. Worker 或 POST /api/v1/runs/{id}/advance 完成已确认只读查询。GET /api/v1/operations/preflight/{run_id}?warehouse_id=1&task_id=101，验证 READY。证据来源为 Invocation 而非用户传入的事实；要求观测时间带时区且在120秒内，检查当前工具绑定。
3. 申请人 POST /api/v1/operations/cancel-drafts，body 包含 run_id、client_request_id、warehouse_id、task_id、reason。请求键重复且参数一致返回原草案；参数不同冲突。
4. 独立 reviewer token GET /api/v1/rehearsal/reviews/{operation_id} 检查参数，再 POST 同路径，payload_hash 为实际草案 Hash，decision=APPROVE_REHEARSAL 或 REJECT；审批仅演练、不能自审、越权拒绝。审批截止时间与预检观测时间绑定。没有真实执行入口。
5. 申请人 GET /api/v1/operations/{operation_id} 查询；POST /api/v1/operations/{operation_id}/withdraw 撤回仍有效的未派发草案。超时需重新读取业务证据并使用新请求键，不能复活旧草案。
6. 重新运行 python -m pytest -q -rs。已分别验证 263 passed / 5 skipped 与 Worker 10 passed，累计 273 passed / 5 skipped；PG、Java/MySQL、真实 LLM 未验收。

## 生产放行门禁
参考 Stone docs/agent-ready-cancel-gate.md。业务方确认权限和审批职责；Stone 企业授权、锁内业务身份/状态复查、operation_id 与请求指纹及取消原子事务、可信查询回执；Agent 持久化审批消耗、派发意图、UNKNOWN 对账、独立维护身份及故障注入。不得将此版演练状态迁移为真实授权。开发静态身份及仓库白名单不能替代企业身份或实时撤权检查。普通取消预检不证明实际库存/库位满足事务要求，也不保证 READY 在下一刻仍有效。
