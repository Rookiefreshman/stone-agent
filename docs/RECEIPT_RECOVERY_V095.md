# v0.9.5 实际只读回执核对及恢复

## 业务用途
仓库维护人员已掌握某个取消操作 ID、仓库、任务及独立取得的原始请求指纹，但需要核对 Stone 是否保存匹配的实际回执。复用 ReceiptReader 和 ReconcileCheckpoint；新增 ReceiptCheckService 与持久查询记录。不是实际执行账本，未授权的参考不转为 SEND_INTENT，不接受演练回执，也不新增取消 POST。

## 本地维护入口（隔离开发环境）
停止旧 API/Worker、备份数据库；PostgreSQL 执行 alembic upgrade head（0006_receipt_check），SQLite 自动建表。配置 STONE_RECEIPT_CHECK_ENABLED=1、STONE_RECEIPT_TENANT、STONE_RECEIPT_WAREHOUSES、STONE_RECEIPT_REALM（稳定环境标识）、STONE_RECEIPT_ORIGIN（HTTPS；仅字面回环允许 HTTP）、STONE_RECEIPT_TOKEN（至少24字符的独立只读凭据，从受控环境注入，不提交）；AGENT_DATABASE_URL 或 POC_DB 明确指定数据库。当前主体 local-receipt-operator 来自本地受控配置，不是企业 IAM。不得对不受信用户开放命令或数据库写权限；生产凭据与数据域治理待验收。服务 realm 与 origin 绑定不能由每次请求任意改变；token 可轮换，来源信任与证书仍由部署维护方保证。

```bash
python -m agent.receipt_check register --warehouse "$WAREHOUSE" --task "$TASK" --operation-id "$OPERATION_ID" --fingerprint "$EXPECTED_FINGERPRINT"
python -m agent.receipt_check once
python -m agent.receipt_check status "$CHECK_ID"
```

register 返回 check_id；预期指纹应从独立原始请求记录获得，不从待核对回执复制。once 领取一个到期核对任务；没有则返回 null。没有自动生产循环。UNKNOWN 保留未确认含义，RECEIPT_MATCHED 只表示查询来源返回的回执匹配输入参考，不证明发起者或审批真实性。输出 dispatch_state=NOT_TRACKED、write_allowed=false。

## 持久化与恢复
receipt_check 绑定租户、主体、realm、origin Hash、参考 Hash、checkpoint；receipt_check_attempt 记录 fence、领取/完成时间、观察结果码与证据 Hash。相同范围内同操作幂等登记，参数冲突拒绝。领取时按仓库范围、到期时间、预算和服务绑定过滤；PG SKIP LOCKED；HTTP GET 在事务外。旧租约或 fence 拒绝结果；重新领取将未完成旧尝试记 ABANDONED。三次领取预算包含崩溃消耗；缺失/传输异常/429/5xx 退避 2^attempts 秒，权限/指纹等异常停止自动领取。最后一次领取崩溃后状态仍 UNKNOWN、manual_review=true；其尝试历史可能仍为 CLAIMED（租约已过期）。无自动重置或人工终态认定功能。socket timeout 不是绝对 HTTP 总耗时上限，迟到结果由租约保护。Hash 只是受信数据库内部一致性校验，不防数据库管理员篡改。

## 实测
Agent 9a575f192e41d6ee91072339c4eee709a373ce08：CI 45763923739，317 passed / 1 warning in 45.04s，新增12项（11项 SQLite/契约测试与1项 PG 集成），7项 PG 通过。Stone ed4331eda619b09569ae3881e64e0cc805e61c01：CI 45763988260，双 MySQL 各45项、7组实际 HTTP 检查通过。真实 Java GET 读取独立测试库回执，注入 GET 后异常、将租约设置到期，启动新 Python 进程重新 GET，确认两次尝试 ABANDONED/OBSERVED、RECEIPT_MATCHED、NOT_TRACKED。选定业务计数与库存余额不变，Agent 业务 POST=0。是只读结果丢失演练，不是实际业务提交后响应丢失。SQLite 跨服务恢复与 PostgreSQL 独立并发验证分别执行，不是生产端到端 PG 测试。

## 剩余门禁
实际企业 IAM、可信审批及旧写接口治理；真实授权执行账本与固定发送工作流、独立生产维护身份、实际网络故障注入、人工接管及 NOT_COMMITTED 证明；目标环境存量迁移、备份恢复、真实 LLM 与生产验收。Stone V6 不变，当前不允许真实 Agent 写入。
