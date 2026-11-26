# v0.6.1 库存作业深度诊断与联调 SOP

## 业务依据
Stone 分支 `codex/implement-java-version-of-design-rules-cqmdqu` 的 `DispatchRepository`, `DispatchMapper`, `DispatchApplicationService`, V2 `wes_dispatch_event` 和 V4 `completed_qty` 已存在。`report` 在事务内保存回执和任务状态。现有 POST 回执接口不能用于读取。新增独立 GET 只读取已记录事件，不更改任务、库存、设备状态，不新增表。参考 [Stone 源码分支](https://github.com/Rookiefreshman/Stone/tree/codex/implement-java-version-of-design-rules-cqmdqu)。本轮 Java 扩展源码未提交到原仓，未编译或连接真实 MySQL。

## 接口契约
`GET /api/wes/warehouses/{warehouseId}/tasks/{taskId}/events`：成功返回 `ApiResponse`，data 为 `warehouseId`, `taskId`, `taskState`, `events`, `truncated`。每条 event 包含 `eventKey`, `targetState`, `reconciled`, `reason`, `completedQty`, `createdAt`。`createdAt` 为 MySQL DATETIME(6) 文本，不宣称 UTC；按 `(created_at,event_key)` 稳定展示，时间相同不表示因果顺序。最多返回 100 条，读取第 101 条识别截断。空数组不是设备未作业的证据。响应不含设备/领取凭据。`reason` 是原始上报文本，仍可能含敏感信息；不发送给模型，不视为系统指令或根因。

```json
{"success":true,"message":"success","data":{"warehouseId":1,"taskId":103,"taskState":"UNKNOWN","events":[{"eventKey":"evt1","targetState":"UNKNOWN","reconciled":false,"reason":"设备上报：等待回执超时","completedQty":4,"createdAt":"2026-01-01T00:00:00.000000"}],"truncated":false}}
```
上述为模拟契约示例，不是生产记录。查询服务 REPEATABLE_READ 内先核对任务仓库与库存作业类型，再按仓库、任务 JOIN 读取事件。401/403/404 明确处理，不将 404 误判为无回执。默认关闭，开发 token 和仓库 allowlist 仅用于隔离测试，不代替企业认证，也不能为原 Stone 其他未鉴权接口提供保护。

## 本地运行（无需 Stone）
```bash
python -m pytest -q -rs
python -m agent.main --db deep-demo.sqlite deep-diagnose '已确认：诊断历史回执' --warehouse 1 --task 103
```
`DEEP_DIAGNOSE_STOCK_WORK` 当前仅结构化 CLI/API；不改变现有 DIAGNOSE/ANALYZE 的步骤，不升级历史任务协议，无新增 Agent schema migration。API 使用未托管的开发 session：`POST /api/v1/sessions/dev/runs`，body:
```json
{"client_request_id":"deep-1","message":"已确认：诊断历史回执","goal":{"kind":"DEEP_DIAGNOSE_STOCK_WORK","entity":{"warehouse_id":1,"task_id":103},"provenance":"CONFIRMED_STRUCTURED_INPUT"}}
```
不得将模型提议直接升级为 confirmed。启动 API/Worker 按 README，运行结束查询 `/api/v1/runs/{run_id}`。真实模型语义提议仍按旧烟测验证；深度诊断当前不经过自然语言目标提取。

## Stone 扩展接入（开发分支）
1. 从上述 Stone 分支创建新的本地开发分支，保持原分支不变。检查 `stone-extension/src` 中七个新增 Java 文件路径均不存在，再将对应文件复制到 Stone 的 `src` 下。不要覆盖其他业务源码。
2. 在 Stone 根目录执行 `mvn -B -ntp clean test`，查看 `DispatchHistoryQueryServiceTest`，并检查 Mapper 注册、Spring 容器、HTTP 映射。此步骤本轮没有执行（当前环境无 Maven）。
3. 在一次性 MySQL 测试库应用 Stone V1–V4，并准备真实测试库存任务及回执；沿用 Stone 的测试说明。验证存在、无回执、不存在、跨仓、状态变化及回执数量。不要向生产设备提交回执。
4. 启动隔离开发 Stone 时设置：
```bash
export STONE_AGENT_EVENT_QUERY_ENABLED=true
export STONE_AGENT_EVENT_QUERY_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
export STONE_AGENT_EVENT_QUERY_WAREHOUSES=1
mvn spring-boot:run
```
开发环境以实际配置为准。Agent 在同一 shell 环境或经安全方式读取同一 token（不写入 Git、日志）：
```bash
export STONE_BASE_URL=http://127.0.0.1:8080
export STONE_LIVE_DOMAIN=1
export STONE_ALLOWED_WAREHOUSES=1
export STONE_API_TOKEN="$STONE_AGENT_EVENT_QUERY_TOKEN"
# 替换为测试库实际 task ID；不要照搬示例 103。
python -m agent.main --db deep-live-dev.sqlite deep-diagnose '已确认：诊断历史回执' --warehouse 1 --task "$TASK_ID"
```
同时验证缺少 token 返回 401、非授权仓库返回 403。现有详情接口本身未由新扩展改造，必须由可信开发环境隔离，正式上线前由业务系统补全统一身份和仓库授权。真实 PostgreSQL 请在备份、停止旧进程后执行已有 `alembic upgrade head`（0003_rule_calls），设置 TEST_POSTGRES_DSN 运行集成测试；不可用时不认定上线验收通过。

## 诊断与恢复
详情不存在直接停止、保留部分结论，不再读取事件。回执请求失败时，保留已验证详情，返回失败代码和部分目标。成功时关联两个 Invocation 的 Hash 与各自观测时间；若来源/任务状态不一致、缺少回执或历史截断，目标 PARTIAL。不认定两次 GET 处于同一快照，不根据 reason、完成数量或 reconciled 字段宣称已核对实物。`SATISFIED` 仅表示指定深度诊断读取及关联完成，不表示物理作业成功或故障根因已确认。只读请求未落账时允许恢复重读，已完成步骤复用证据。

## 验收状态
Python 176 passed / 4 skipped（19 项新增，localhost 模拟 HTTP）。新增 3 项 Java 单测为待执行源码。未连接真实 PostgreSQL、Java/MySQL、LLM，未创建 GitHub 独立仓库，未提交原 Stone。详见 [项目计划清单](PROJECT_CHECKLIST.md) 和 TEST_RESULTS.txt。后续优先完成上述真实联调，再增加库存余额、出库单及容量查询；不开放写操作。
