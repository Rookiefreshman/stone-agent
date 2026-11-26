# v0.9.2 持久化操作故障演练与对账恢复

## 业务目的
针对 READY 库存作业取消场景，验证审批消费、模拟发送意图和回执丢失/迟到后的恢复逻辑。真实 Stone 取消、库存预占释放、V5 事务均不由本模块执行。复用 v0.9 草案及 v0.9.1 回执校验；不修改 Stone Java/MySQL。只有明确启用的内部 RehearsalJournal 且绑定精确 FixtureStoneService 才可使用；无 HTTP execute 路由、无 CLI 写入、无模型工具、无生产 dispatcher。开发身份 local-fixture/local-reconciler 不属于企业 IAM。

## 执行协议
1. 使用已有合成 READY 查询生成草案，独立 reviewer 完成演练审批。begin 在草案锁内校验当前证据、来源、Hash、绑定、有效期和审批人，插入唯一 operation_id 的 rehearsal_execution 与 REHEARSAL_SEND_INTENT 审计事件。该操作仅为模拟，不发送请求。重复 begin 返回已有记录，消费后 withdraw 被拒绝。
2. fixture_commit 仅在另一个本地事务生成合成回执，存于同一演练表。它不是独立 Stone 数据库，不验证实际库存原子性。
3. claim 按租户、仓库和到期时间领取；PostgreSQL 使用 FOR UPDATE SKIP LOCKED，更新 attempts/fence/lease。observe 只读本地合成回执；finish 校验租约、fence、回执与原指纹及持久合成回执一致。重启后重新领取过期租约，旧 Worker 结果拒绝。
4. 无回执保持 REHEARSAL_UNKNOWN；最多三次领取，退避为 2^attempts 秒。达到预算且租约释放/到期时 manual_review=true，不自动重新派发。预算耗尽后的人工继续处理尚未实现。匹配回执只标记 REHEARSAL_COMMITTED；所有结果 source=SYNTHETIC_FIXTURE、write_allowed=false、dispatch_state=NEVER_DISPATCHED。
5. 独立内部 once 方法供测试和受信演练调用，尚未接入生产 Worker 循环。生产审批消耗、真实发送意图、真实回执对账及操作授权依旧未实现。不得将演练回执用于真实业务。

## 升级与验证
停止旧 API/Worker，备份后在独立测试库执行 alembic upgrade head，目标 0005_rehearsal。SQLite 自动建表；PG 全新库链路已在 CI 验证，既有部署数据升级需另行验收。旧离线 SQL 保留追溯，0005_agent_head_offline.sql 仅供新库初始化。Stone V5 不变。
运行 python -m pytest -q tests/test_rehearsal.py -rs 或 python -m pytest -q -rs；PG 集成需 TEST_POSTGRES_DSN 指向独立已迁移测试库。不要将测试指向生产库。

## 验收证据
v0.9.1 修复后 CI 296 passed（45760593186）；v0.9.2 代码提交 cd6c23be64476a1163b5f9559b393731a9fccf72 的 CI 305 passed, 1 warning in 40.23s（45761308366）。本地分批 299 passed / 6 skipped（无 DSN）。6 项 PG CI 集成测试全部通过。Node.js 20 Actions 弃用告警仍存在，非阻塞。真实 Java/MySQL、LLM、企业授权和实际写入未验收。
