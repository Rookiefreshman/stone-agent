# v0.9.7 自然语言业务目标与类型化确认

## 实际业务范围
历史设备回执、出库分配阻塞、WES 调度容量此前已有结构化目标与确定性业务策略。本版扩展自然语言提议和会话确认，不重写业务查询。三个目标均只读：
- DEEP_DIAGNOSE_STOCK_WORK: StockWorkArgs(warehouse_id,task_id)，详情与历史回执两次读取，不确定实际根因。
- DIAGNOSE_OUTBOUND_ALLOCATION: OutboundArgs(warehouse_id,order_id)，当前快照及分配回执，不能重建历史失败原因或执行分配。
- DIAGNOSE_WES_DISPATCH: WesArgs(warehouse_id,device_code,requested_limit)，请求数1..100，仅分析快照容量，不实际领取任务。

## 执行链路
goal_proposal.py 使用旧兼容别名 propose_stock_work_goal 的扩展候选 schema；仅虚拟非执行提议。真实模型模式发送目标描述、schema、用户文本，不发送业务凭据或审批。新增 business_language.py 有限中文完整匹配语法用于 fixture 测试；真实模型模式对扩展目标进行显式字段角色检查，缺失/不匹配参数需补充，明显复合操作及多实体拒绝。正则是保守词法 guard，不是语义证明，不保证所有自然语言或恶意表达都正确分类。历史兼容目标仍保留原词法机制；所有候选必须由用户明确确认。
session.py 持久化提议与 revision/TTL，扩展类型化确认；同类型补参或明确追问只能从最新有效待补充候选或上次已确认实体继承，仍要重新确认，不能跨域继承。对于 WES 同类型设备追问，沿用已确认的仓库和请求数量，确认页面必须显示所有字段。没有语义记忆检索或自动执行。
确认时校验候选完全一致、目标类型、当前授权、会话归属、有效期，再原子创建 Run；RULE 模式复用现有 Registry、Dispatcher、Invocation 账本。执行阶段不追加模型调用。未改 schema，Agent head 0007_receipt_review，Stone V6。

## 使用示例（受控开发 API）
POST /api/v1/sessions 创建会话，POST /api/v1/sessions/{sid}/goal-proposals 提交 {"client_request_id":"unique-id","message":"分析仓库1出库单201分配失败"}。
等待 NEEDS_CONFIRMATION；由用户查看 goal_kind 和 candidate，确认只读目标：
```json
{"kind":"DIAGNOSE_OUTBOUND_ALLOCATION","entity":{"warehouse_id":1,"order_id":201}}
```
POST /api/v1/sessions/{sid}/goal-proposals/{pid}/confirm 后，沿用 Worker 或开发 advance 接口，GET /api/v1/runs/{rid} 查询结果。
其他样例：“诊断仓库1任务103历史回执”；“诊断仓库1设备station-01请求8个任务的调度容量”。缺少参数：“诊断设备station-01调度容量” -> “仓库1” -> “请求8个”，每轮使用不同 client_request_id，最终确认完整目标。出库确认及执行后可“那出库单202呢？”，再次确认。例子是 fixture 数据，不是实际生产业务。
真实 LLM 需 POC_LIVE_MODEL=1、MODEL_BASE_URL、MODEL_NAME、必要时 MODEL_API_KEY，真实 Stone 需独立开发环境 STONE_LIVE_DOMAIN=1、STONE_BASE_URL 及只读凭据；仍使用当前本地开发身份，不可公开暴露。真实外部 LLM 不在自动化 CI 中。不得把测试指向生产库。

## 验收
首次 Agent CI 45766133202 失败（源代码生成时正则末尾符号误展开，测试收集语法错误），已修复提交 5596372d5af0f96f1de6c91df0538cdd00398361；回归 45766300805。Stone 通过固定该 Agent SHA 的工作流 af0f02e4e34df2be46d542cc7c4b9f4ea66fe753 执行真实 Java/MySQL 只读链路，CI 45766336138。真实跨服务使用 Fixture 语法与明确确认，而非实际外部 LLM。修复后的 Agent CI 实测 346 passed, 1 warning in 52.42s（新增21项，含历史 PostgreSQL 集成）；Stone MySQL 5.7/8.0 各45项，0失败/错误/跳过，9组实际 HTTP 检查均通过；新增组逐一确认三类目标后执行真实 Stone 查询，RULE 模式无执行模型调用，业务 POST=0。生产写入继续关闭；真实模型语义准确率、真实 IAM、发送意图、生产数据库迁移及生产验收未完成。
