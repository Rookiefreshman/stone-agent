# v0.7 出库分配诊断设计和验收 SOP

## 业务边界

Stone `OutboundAllocationApplicationService.allocate` 先查成功分配回执，再要求首次分配为 APPROVED，逐行分配剩余量，按库位、SKU、批次排序，以 AVAILABLE 库存维度预占。已配置的冻结源位拒绝，未配置源位不是冻结。库存版本冲突或业务异常可导致整单回滚。当前快照不还原历史失败原因，不替代写事务。

## 双仓库改造

- Stone: `https://github.com/Rookiefreshman/Stone/tree/agent/outbound-diagnostics-v0.7`，新增只读 DDD 查询。业务原分支保持不变。提交 `267423b5deda05aac5fa01c167109427d67f1132` 和 `ba80d46ac5d0d0dcd1cab8127bbdd06ecd70fa84`。
- stone-agent: `https://github.com/Rookiefreshman/stone-agent/tree/agent/outbound-diagnostics-v0.7`，新增 `agent/integrations/outbound.py`、六文件升级补丁、34 项测试源码，提交 `cdae4b11b5dd9f93212a8147da8a325cee8d466a`。本地完整工程已集成；远程基线未完整导入。

## 协议与运行

`GET /api/wms/warehouses/{warehouseId}/outbound-orders/{orderId}/allocation-diagnostic`。单条 SELECT、REPEATABLE_READ 只读事务。最多 200 行，超限返回 422。DTO 包含 `warehouseId, orderId, orderNo, orderStatus, receiptPresent, lines`。库存 ID 为 null 时所有库存数量/版本字段为 null；库位状态 OPEN/FROZEN/UNCONFIGURED。严格结构校验；按 inventoryId 合并需求，不能用 Python 批次字符串相等替代 MySQL collation。输出 `historical_root_cause=UNDETERMINED`、`write_allowed=false`，存在成功回执时以该分支优先。观察时间为 Agent 接收时刻，不是业务事件发生时刻。

### 本地模拟验证

```bash
python -m pytest -q -rs
python -m agent.main --db demo-v07.sqlite outbound-diagnose '已确认出库单201' --warehouse 1 --order 201
```

### Stone 测试环境

1. 在一次性测试库应用 Stone V1–V4 migration，禁止在生产库直接运行带固定样例 ID 的集成测试。
2. 设置 STONE_TEST_MYSQL_URL、STONE_TEST_MYSQL_USER、STONE_TEST_MYSQL_PASSWORD，执行 `mvn -B -C -ntp clean verify`；确认历史回执与新诊断单测及 OutboundDiagnosticMySqlTest 均通过。
3. 开发环境通过外部安全配置设置 `stone.agent.outbound-query.enabled=true`、`stone.agent.outbound-query.token`（至少24字符）、`stone.agent.outbound-query.warehouses`（允许仓库 ID，逗号分隔）。历史回执接口相应 `stone.agent.event-query.*` 配置。默认关闭。两接口可使用相同开发凭据，但生产必须替换为正式身份及数据域授权。不得将凭据提交 GitHub。
4. Agent 设置 `STONE_LIVE_DOMAIN=1`、`STONE_BASE_URL`（可信 Stone 服务 origin）、`STONE_API_TOKEN`（通过安全环境注入）及 `STONE_ALLOWED_WAREHOUSES`。将订单 ID/仓 ID 替换为授权测试数据。验证 GET 授权、401/403/404/422、快照数量、无写入副作用。
5. PostgreSQL 设置 `TEST_POSTGRES_DSN` 至隔离测试库并执行 `alembic upgrade head`、`pytest` PG 集成测试。真实 LLM 仍须单独评测。

结构化 Run API 示例：

```json
{"client_request_id":"outbound-001","message":"已确认出库单201诊断","goal":{"kind":"DIAGNOSE_OUTBOUND_ALLOCATION","entity":{"warehouse_id":1,"order_id":201},"provenance":"CONFIRMED_STRUCTURED_INPUT"}}
```

提交到未托管 session 的 `/api/v1/sessions/{session_id}/runs`；Worker 自动领取，或开发时 advance。托管会话的目标提议与确认尚未支持出库，不通过文字直接触发任何业务操作。生产禁止直接公开开发身份 API。

## 版本升级与待办

数据库 schema 不变（0003_rule_calls）；停止旧 API/Worker，再部署新版本。`patches/v0.6.1-to-v0.7.patch` 仅用于干净旧版，完整 ZIP 已集成。旧目标字段序列化不变，老工具 schema 不变；新目标仅允许 RULE 执行。完整部署必须通过真实 PostgreSQL、MySQL、Java、LLM、身份授权验收。无独立通用库存查询或历史失败审计数据时明确报告缺失，不伪造接口。不引入 MCP、Multi-Agent、RAG 或低代码 DAG。

## 实际提交及验收状态（2026-11-26）

- Stone Draft PR #4: https://github.com/Rookiefreshman/Stone/pull/4
- Agent Draft PR #1: https://github.com/Rookiefreshman/stone-agent/pull/1
- 本地完整 Python suite: 210 passed, 4 skipped (TEST_POSTGRES_DSN 未配置)；新增 34 项全部通过。
- Agent 三个新增源码/补丁/测试 Blob SHA 已逐一核对一致。两个 PR 均未合并，Java 测试源码已提交但未执行。完整 Agent 基线远程导入仍未完成。
