# v0.9.6 UNKNOWN 人工复核与受控只读恢复

## 业务目的
v0.9.5 的真实回执核对可能因 401/403、身份冲突或自动领取预算耗尽而进入 UNKNOWN/manual_review。维护人员修复读取权限或核对原始记录后，需要再次 GET，但不得重新派发业务请求，也不能人工将 UNKNOWN 改为 COMMITTED。v0.9.6 复用 ReceiptCheckService 和 ReceiptReader，提供有界人工核对入口，不建立新审批平台。

## 运行 SOP
继承 v0.9.5 的本地受控维护环境及独立 Stone 只读凭据，参考 RECEIPT_RECOVERY_V095.md。停止旧版 API/Worker/维护命令、备份，独立测试库执行 alembic upgrade head 至 0007_receipt_review；SQLite 自动建表。Stone V6 不变。当前只支持配置主体 local-receipt-operator，无法区分不同真实员工，不能作为生产 IAM 审计。

```bash
python -m agent.receipt_check pending-reviews --limit 50
python -m agent.receipt_check status "$CHECK_ID"
# 使用待核对列表或 status 返回的当前 fence，修复读取权限后显式执行一次查询。
python -m agent.receipt_check review-retry "$CHECK_ID" --request-id "$REVIEW_REQUEST_ID" --expected-fence "$CURRENT_FENCE" --reason "已核对原始请求并恢复只读访问"
python -m agent.receipt_check status "$CHECK_ID"
```

pending-reviews 支持 --after 上一页 next_after（按 ID 的 keyset 分页；并非固定快照）；列表只包含当前范围内 UNKNOWN、租约到期且 held 或自动预算耗尽的记录，包括人工预算已耗尽的记录。维护人员应从独立原始记录校验 operation_id、仓、任务及请求指纹；不可从待核对回执复制预期指纹。不要在 reason 填 token 或敏感数据。固定 realm/origin 与原参考不可修改；凭据可以由部署方轮换。原参考错误时不提供改写或重新登记绕过冲突；升级由业务方独立核实。

## 事务与审计
在原核对记录行锁内检查当前权限、服务绑定、参考与 checkpoint 完整性、expected_fence、状态、租约和人工预算。事务内创建查询尝试及 receipt_check_review（check_id/request_id 唯一、check_id/fence 唯一、复合外键关联 attempt），递增 attempts/fence 并保持 held=1。提交后只执行一次 GET，结果由原 _finish 核验有效 fence/lease 及回执。重复 request_id 与原参数相同返回当前状态和原 review_fence、replayed=true，不重复查询；同键不同参数拒绝。过期租约下新的复核请求将旧 CLAIMED 置 ABANDONED；最后一次领取崩溃而无后续领取时该尝试仍可能显示 CLAIMED/过期租约。手工请求与自动请求共享单调 fence，不能回退或清空历史。

自动最多3次领取，人工最多2次领取（总计不超过5次，崩溃也消耗）。人工失败仍 UNKNOWN/held，不恢复自动查询。匹配真实回执才为 RECEIPT_MATCHED；不提供人工宣告成功、修改预期参考、解除终态、重置预算、业务 POST 或模型工具。manual_review 表示需人工核对，不表示还有可用预算。人工预算耗尽后升级独立业务核查，不自动继续。

## 验收与限制
新增8项测试源码（含1项 PostgreSQL 并发）。核心 Agent 69695dc 对应 CI 45765456480；Stone 5b7917e 对应 CI 45765488016。实际跨服务测试新增真实 Stone 401、轮换正确查询凭据、明确一次复核并核验审计与幂等重放。实测：Agent CI 325 passed, 1 warning in 49.69s，8项 PG 集成执行；Stone 双数据库各45项 Java 测试，0失败/错误/跳过，8组实际 HTTP 全通过；Agent 业务 POST=0、选定计数及库存余额不变。此为实际读取权限恢复，不是生产写入响应丢失测试。维护 CLI 信任边界仅为本地受控部署，真实企业 IAM、人员身份、可信审批与发送账本、旧接口访问治理、真实业务提交后响应丢失、生产自动调度、目标数据库存量迁移及性能验收仍待完成。真实写入保持关闭。
