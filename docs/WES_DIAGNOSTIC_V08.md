# v0.8 WES 调度诊断

业务依据 Stone 实际 claim 服务、activeCount 与 V2 表。查询接口 GET `/api/wes/warehouses/{warehouseId}/dispatch-diagnostic?deviceCode=station-01`，在 REPEATABLE_READ 只读事务内单条聚合 SELECT 读取配置与 READY/IN_FLIGHT/UNKNOWN 数量。不新增表、不调用 claim/report、不占仓容量行锁、不自动释放 UNKNOWN。不存在仓库设备组合返回 404 DISPATCH_SCOPE_NOT_FOUND，不能视为空队列。

新领取数量上限 max(0,min(requested_limit,maxBatch,maxActive-inFlight-unknown,deviceMaxActive-deviceInFlight-deviceUnknown,ready))。requested_limit 范围1–100；maxReady 仅限制入队。保留缩容时负剩余量，并列因素全部返回。仅诊断快照，不包含历史领取键重放，不保证未来领取结果，不推断硬件状态或故障根因。

Agent DIAGNOSE_WES_DISPATCH，注册 get_wes_dispatch_snapshot，复用 RULE、Invocation、Worker、证据 Hash，正常执行零 LLM 一次 GET；严格校验归属及计数。当前仅结构化 CLI/API，生产写入关闭。默认 fixture，仓库1/station-01 示例 READY25、仓占13/16、设备占4/4，领取上限0。命令：`python -m agent.main --db wes-demo.sqlite wes-diagnose '诊断调度容量' --warehouse 1 --device station-01 --limit 8`。

真实联调先在 Stone v0.8 分支运行 Maven 测试，并在一次性 V1–V4 MySQL 测试库运行集成测试。隔离开发环境配置 stone.agent.dispatch-query.enabled=true、stone.agent.dispatch-query.token（至少24字符）、stone.agent.dispatch-query.warehouses；Agent 配置 STONE_LIVE_DOMAIN=1、STONE_BASE_URL、STONE_API_TOKEN、STONE_ALLOWED_WAREHOUSES。不要提交真实凭据。完整本地测试 251 passed / 4 skipped，真实 PG/MySQL/LLM、生产身份及负载验收未完成。

GitHub 目前仅增量；完整 ZIP 中包含 scripts/stage_agent_baseline.py。克隆本开发分支后执行脚本，传入正确 checkout 和审阅过的 expected-head；脚本仅复制白名单文件、暂存到 Git，拒绝非预期 origin/branch/HEAD 或不干净目录，不自动提交、推送、删除或强制覆盖。检查 staged diff、运行测试后正常提交 push。完整源码入库及 CI 不提前勾选。
