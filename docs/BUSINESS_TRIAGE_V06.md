# v0.6 库存作业异常助手与确定性执行

## 业务范围与来源

Stone 分支 `codex/implement-java-version-of-design-rules-cqmdqu` 的实际 `DispatchController.stockWork` 接口：`GET /api/wms/warehouses/{warehouseId}/stock-work/{taskId}`。状态与处理规则依据该分支 [库存作业文档](https://github.com/Rookiefreshman/Stone/blob/codex/implement-java-version-of-design-rules-cqmdqu/docs/stock-work-execution.md) 与 [调度容量文档](https://github.com/Rookiefreshman/Stone/blob/codex/implement-java-version-of-design-rules-cqmdqu/docs/concurrency-dispatch.md)。本版没有增加或假定其他 Stone 接口存在。只读取授权开发环境，禁止业务写入。

## 决策与执行

- 自然语言：生成待确认 `goal_kind`、warehouse_id、task_id。目标类型和实体必须匹配才能确认；普通 READ 的旧确认格式兼容。诊断/分析必须显式确认 kind，不允许降级为查询。有限语法支持完整的“诊断仓库1任务103”、“分析1号仓库103号库存作业状态及流水”；诊断目标的模糊追问需重新明确类型与实体。自然语言比较仍未实现，保留结构化 CLI/API。
- 新 API/CLI/Worker 通过 `build()` 创建 `STOCK_WORK_RULES_V1` 模式。目标确认后，固定计划由 `agent/integrations/stock_work.py` 的 StockWorkPolicy 生成。Runtime 仍复用原 Registry、Dispatcher、授权、检查点及证据校验，非任意 SQL 或函数执行。
- 每步骤 reservation 记录 `origin=RULE`、`direct_snapshot`、`args_hash`，不插入 model_attempt，也不伪造 provider_call_id。`(run_id,step_no)` 唯一。业务输入不必发送给 LLM；模型提议（真实模型配置时）仍单独发生在确认前，尚未纳入统一费用账本。
- 既有 Run 迁移为 `LEGACY_MODEL_V1`，其模型接受和调用顺序保持原约束。Run 首次提交固定 execution_mode；幂等重放保持原模式。底层 Store 构造默认 legacy，供历史回归/兼容调用；应用入口明确选择规则模式。禁止新旧进程混跑。
- 规则调用与模型调用共用 Invocation，后者仍需要有效接受记录；数据库 CHECK 限定两类记录的来源字段，避免混用。新版本不需要独立 Planner、Reflection 或工具市场。

## 业务诊断输出

`triage.observed_state` 是 GET 返回的事实；`business_rule_effects` 是按规则推导的影响，不是现场库存/设备实时观测；`next_checks` 是建议；`missing_evidence` 是还未读取的证据；`root_cause=UNDETERMINED`。记录 `source` 和原始证据 hash。

| 状态 | 规则影响 | 下一步 |
|---|---|---|
| READY | 未领取，待移入容量保留 | 检查仓、设备容量及优先级 |
| IN_FLIGHT | 已领取，未确定完成 | 检查设备回执和实物进度 |
| UNKNOWN | 保留仓/设备槽及目标容量；移库源预占保留 | 现场核对，不自动重派；部分作业按业务规则处理 |
| SUCCEEDED | 领域状态成功 | 如需实时库存再查余额 |
| FAILED | 失败结案，不能推断根因 | 查询历史回执及原因 |
| CANCELLED | READY 取消按规则释放预占 | 必要时查余额、容量 |
| NOT_FOUND | 无有效作业详情 | 确认仓库和任务编号；不能推断实际状态 |

本次规则未改变 Stone 业务状态机。流水汇总只代表该作业的变化量，不能用作全仓余额。NOT_FOUND 的诊断不满足目标。没有历史回执、设备实时状态、库存余额证据时，不能诊断具体根因。

## 升级和运行

停旧 API/Worker，备份原 Agent 数据库。SQLite 初始化自动迁移；PostgreSQL 使用 `alembic upgrade head`，head=`0003_rule_calls`。迁移保留旧请求和调用。先验证备份恢复，禁止运行旧版本 Worker；降级通过受控备份恢复。PostgreSQL 在线迁移仍待验收；离线 SQL 见 `migrations/0003_agent_head_offline.sql`。

```bash
python -m pytest -q -rs
python -m agent.main --db demo-v06.sqlite analyze '已确认：状态及流水分析' --warehouse 1 --task 103
```

默认 fixture；任务编号仅为演示。`RULE_AUDIT_DEMO.json` 为本地真实运行记录，`TRIAGE_DEMO.json` 为模拟领域诊断结果。

托管会话示例：先创建 session，再 POST `/api/v1/sessions/{sid}/goal-proposals`，正文 `{"client_request_id":"d1","message":"诊断仓库1任务103"}`。人工核对候选和目标类型后 POST `/api/v1/sessions/{sid}/goal-proposals/{pid}/confirm`，正文 `{"entity":{"warehouse_id":1,"task_id":103},"kind":"DIAGNOSE_STOCK_WORK"}`。独立 Worker 自动领取，或开发环境通过 advance 触发。历史追问只继承安全的明确查询实体，诊断不隐式转换为查询。

## v0.5.1 真实环境验收（未执行）

使用隔离 PostgreSQL、真实 Stone Java/MySQL 开发服务及私有化模型。配置 AGENT_DATABASE_URL、STONE_BASE_URL、STONE_LIVE_DOMAIN=1、STONE_ALLOWED_WAREHOUSES、MODEL_BASE_URL、MODEL_NAME；按需配置 MODEL_API_KEY、STONE_API_TOKEN。不得使用生产数据。先在隔离 Stone 库准备一个已知稳定状态的作业，使用实际仓库/任务编号。执行：

```bash
alembic upgrade head
TEST_POSTGRES_DSN="$AGENT_DATABASE_URL" python -m pytest -q -rs
python -m agent.verify_live --confirm-isolated-dev --warehouse "$WAREHOUSE_ID" --task "$TASK_ID" --expected-state UNKNOWN
```

命令不调用 Stone POST，但会创建 Agent 测试会话/Run；仅在专用开发库执行。烟测会校验实际模型提议与操作者提供的目标一致、HTTP 返回状态与预期一致、规则执行无模型账本记录。不替代真实模型语义黄金集、数据库压力测试、企业权限和生产安全评估。工具服务端授权必须由 Stone 或可信网关实施；当前开发 token 和静态仓库配置不是生产认证。

## 下个业务增量

优先在 Stone DDD 层补齐 **指定仓库、任务的历史设备回执查询**，并在隔离数据库验证授权及顺序/分页。建议 `GET /api/wes/warehouses/{warehouseId}/tasks/{taskId}/events`（尚不存在），包含稳定 eventKey、状态、核对标识、原因及真实可得的时间字段；不得虚构数据库没有记录的时间或故障码。先确认 DTO、权限、回执与任务归属以及事件是否保留，再新增 Adapter 和证据关联测试。缺失记录只报告缺失，不把 UNKNOWN 归因于设备故障。库存余额、出库诊断、WES 容量查询按后续真实业务场景顺序推进。
