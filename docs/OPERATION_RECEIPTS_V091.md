# v0.9.1 Operation receipt protocol

## 本轮范围
复用 Stone READY 取消事务，新增 V5 回执表、内部 CancelOperationService、默认拒绝授权端口和只读 GET。没有新的对外取消 POST；没有真实审批消耗、业务发送、自动重试或实际对账 Worker。旧 v0.9 演练草案不会转换为业务提交。Agent receipt 模块不是模型工具，也不接收任意 URL。业务提交状态和作业状态独立。

## 契约
操作键为 (warehouse_id, operation_id)，固定请求指纹绑定仓库、任务、租户、申请人、任务键、类型、SKU、批次、源/目标库位、数量、原因及审批 Hash。按文档所列字段顺序编码 UTF-8、每字段 4 字节大端长度前缀后 SHA-256。共享黄金向量 f384e17fa1a042bfb31974d2e40c8ade060258660a4bd2102da9d70b20fd68fe；Python 已验证，Java 测试源码待执行。它不是签名或生产授权凭据。

GET /api/wms/warehouses/{warehouseId}/cancel-operation-receipts/{operationId}。Stone 回执必须包含 operationId、warehouseId、taskId、requestFingerprint、businessState=CANCELLED、recordedAt（UTC）。recordedAt 是事务内记录时间，不是精确 commit timestamp。只有所有标识及指纹匹配才接受 COMMITTED。404 OPERATION_RECEIPT_NOT_FOUND、拒绝、超时、异常或不匹配均保持 UNKNOWN；没有 NOT_COMMITTED 判定；不自动重派。已确认回执不可被后续缺失覆盖，冲突回执需人工核验。检查点可序列化，仅在合成故障用例中验证恢复；尚无可信实际发送账本或自动对账工作流。

## 运行
完整运行时已导入 GitHub agent/operation-receipts-v0.9.1，不再需要手工导入 v0.8 ZIP。Python: pip install -r requirements.txt; python -m pytest -q -rs。历史 273 passed, 5 skipped；本轮 18 passed，累计 291（分批）。Agent schema 仍为 0004_cancel_drafts，SQLite/PG 无新增表。Stone 独立一次性测试库需先按原 README 应用 V1–V4，再执行 V5__create_cancel_operation_receipt.sql；用 STONE_TEST_MYSQL_URL/USER/PASSWORD 运行 CancelOperationServiceTest 与 CancelReceiptMySqlTest。Maven、MySQL 和 PG 在线测试未执行。GET 默认关闭，仅隔离开发环境配置 stone.agent.cancel-receipt-query.enabled=true、token（至少24字符）、warehouses；不要提交凭据。无需且禁止为测试开启生产写入。实际请求发送前必须补企业授权、可信审批和持久发送意图。

## 放行门禁
真实身份/数据域及旧接口访问治理；V5 编译与真实事务验证；实际授权绑定审批、派发前实时复查；独立维护身份与可信 SEND_INTENT、对账任务持久化、UNKNOWN 不重派；实际数据库、故障注入和业务验收。所有真实写入继续关闭。
