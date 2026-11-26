# v0.9.3 Java/MySQL 与 Agent 跨服务验收

## 目的与范围
解决历史 Java 源码未编译、真实 MySQL 原子事务未验证、Python HTTP Adapter 只用 Mock HTTP 的工程缺口。保持现有业务规则与 schema；不新增生产写接口、不实现假企业身份、不将演练审批转换为真实授权。

## 流水线
Stone 分支 agent/business-validation-v0.9.3 的 .github/workflows/business-verification.yml 使用 Java 8、MySQL 5.7/8.0 独立容器与一次性 stone_ci 数据库，应用 V1–V5 后执行 mvn -B -ntp clean verify。Surefire 报告检查所需套件实际存在、tests>0、无失败错误跳过。保留报告和测试服务器日志。
随后按固定 Agent commit 99c43574fbbeec31c53313a9e160ff09f317ac32 检出客户端，安装依赖。基于已执行的 Java 业务测试查找 99101 取消操作回执及 403 历史任务的 ID（不读取回执指纹作为预期值），真实启动 Spring Boot JAR 在 127.0.0.1:18081，通过独立 CI 查询凭据调用 Python 适配器。
脚本 scripts/stone_ci_contract.py 要求 STONE_CI_CONTRACT=1、两个不同且至少24字符的 CI 查询 token，仅支持固定本地地址与测试仓数据。由 Stone 工作流执行，不是生产烟测命令。不要将测试指向生产数据库。

## 验收结果
- Agent v0.9.2 最终 CI：305 passed, 1 warning，run 45761709093；v0.9.3 客户端提交 99c4357：305 passed, 1 warning in 52.44s，run 45762213821；6 项 PostgreSQL 测试均执行。
- Stone 初次 Java/MySQL CI 01ed03e：run 45762032368，MySQL 5.7/8.0 每个版本 38 tests, 0 failures, 0 errors, 0 skipped（21 单测、17 集成）。
- Stone 跨服务提交 6bf650dfeda56f17564d0741a686d696d9faaf9b：run 45762289693，两种数据库各 38 项 Maven 测试及 6 组实际 HTTP 契约检查通过；日志明确显示 DISPOSABLE_STONE_CI、write_allowed=false、business_posts=0。
- 六组检查：取消状态与一次 MOVE_RELEASE；UNKNOWN/FAILED 核对历史；出库精确维度、冻结、库存缺失与已有回执；WES UNKNOWN 在途与领取上限；独立 Java/Python 请求指纹、缺失回执及终态保护；本地数据域与 Stone 只读凭据。
- 选定业务计数（取消回执、领取批次、设备回执、库存流水）及指定库存可用/预占余额查询前后相同。这是有限的不变性检查，不是完整数据库 diff。

## 放行边界
真实数据库引擎及 Java HTTP 已验证，但业务数据为固定合成夹具，内部写事务测试使用测试授权替身。未接入真实 LLM、生产 IAM/审批、现有旧写接口访问控制、真实发送意图及对账 Worker；未测目标环境存量迁移、性能、全链路网络故障或真实生产数据。Agent 0005_rehearsal 与 Stone V5 不变。生产写入关闭。
