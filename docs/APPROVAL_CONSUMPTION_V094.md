# v0.9.4 审批绑定与业务域唯一消费

本版核心功能位于 Stone Java 仓库，而非 Agent 重复实现库存/审批事务。Agent 保留 v0.9.2 的合成演练边界及 v0.9.3 真实只读契约客户端，未新增生产写接口、生产审批状态或实际 SEND_INTENT。Stone 通过受信 CancelAuthorization.requireAccess 与 verifiedApproval 检查真实授权边界；无生产实现默认拒绝。VerifiedCancelApproval 不是签名验证器，实际 IAM/审批服务仍需接入。

Stone MySQL V6 在原操作回执表内保存审批签发方、审批 ID、租户、申请人、独立审批人及有效期。唯一索引 (approval_issuer, approval_grant_id) 防止跨仓重复消费。新操作必须绑定完整 CancelCommand 指纹并在锁仓内校验 READY 与任务维度；数据库 UTC 时间在授权检查及回执 INSERT SQL 再校验。取消、库存释放、回执及消费同事务，任何失败整体回滚。已提交同操作重放需当前访问授权和精确指纹，不再消耗审批。SQL 检查不等于提交瞬间有效，不消除真实权限撤销竞态。

公开只读回执契约与 CANCEL_READY_V1 指纹保持不变，审批审计字段不向 Agent 回执客户端暴露。Agent PostgreSQL head 仍为 0005_rehearsal。Stone 目标 Schema 为 V6。历史 V5 回执审批字段为空，不回填或追认授权；目标环境存量迁移及备份恢复未验收。

## 验收记录
- v0.9.3 最终 Agent CI 305 passed（45762570378），Stone 双库通过（45762564734）。
- Stone 功能提交 707c2498a1929b74beba291457b14c63b33f8529，[CI 45763151336](https://github.com/Rookiefreshman/Stone/actions/runs/45763151336)：MySQL 5.7 与 8.0 各45项（25单元、20集成），无失败、错误、跳过；6组真实 HTTP 契约继续通过。Agent 实际业务 POST=0，选定计数与余额不变。
- 7个新增 Java 测试包含跨仓并发唯一消费及过期 SQL 边界回滚。测试使用 MockBean 审批与独立测试库，过期边界由注入验证，不代表真实 IAM 或真实跨服务故障恢复。
- [Stone 详细设计与迁移说明](https://github.com/Rookiefreshman/Stone/blob/agent/approval-consumption-v0.9.4/docs/agent-approval-consumption-v094.md)。

下一阶段先接入实际受信身份及审批契约，确认旧写接口访问治理；在明确真实信任边界前，不创建可发送的生产操作记录，不接入虚拟审批或演练回执。后续真实操作账本、固定 Workflow、独立回执 Worker、实际网络故障注入与人工处理均保持待办。
