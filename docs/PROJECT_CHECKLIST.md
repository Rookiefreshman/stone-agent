# Stone Agent 项目实施清单（v0.9.11）

更新日期：2026-11-26。以实际业务价值和验收证据作为勾选依据。[x] 表示该项在注明范围内已实现或已验证；[ ] 表示未实现、未验证或存在外部依赖。代码交付不等于真实环境验收。历史版本的勾选依据为历次交付记录及 v0.9.3 累计回归；不代表每项都完成生产验收。

## v0.1 业务接入（本地完成）
- [x] Stone Java 分支的 Controller、DTO、领域规则核对
- [x] 库存作业详情 GET HTTP 适配、参数和响应校验
- [x] LOCAL/HTTP 工具注册、真实执行器绑定、调用账本
- [x] 仓库权限边界与模拟 HTTP 回归（历史 41 项）
- [x] 隔离 CI 实际 Stone Java/MySQL HTTP 联调（v0.9.3，45762289693）
- [ ] 目标部署环境与实际业务数据验收

## v0.2 自然语言目标提取（本地完成）
- [x] 查询候选目标、参数缺失澄清、明确结构化确认入口
- [x] 禁止候选直接执行；非法目标、伪造参数测试（累计 61 项）
- [ ] 真实模型语义准确率和开放式输入评测

## v0.3 会话上下文（本地完成）
- [x] Session、Proposal 持久化及已确认实体继承
- [x] 过期、替代、重复确认及权限检查；原子创建 Run
- [x] 多轮补全及并发回归（累计 85 项）
- [ ] 真实企业身份与完整消息历史（按业务需要推进）

## v0.4 持久化和 Worker（CI 独立 PG 验收完成）
- [x] PostgreSQL 适配代码、Alembic 迁移和 CI 配置
- [x] Run 队列、Worker 自动领取、Lease/Fencing、SSE 断点续读
- [x] SQLite 并发及故障恢复（累计 96 项）
- [x] PostgreSQL 16 CI 独立库迁移、锁竞争、故障恢复（v0.9.1/0.9.2）
- [ ] 部署目标 PostgreSQL 环境与备份恢复验收
- [ ] 按测量结果完善连接池、资源限制、部署与备份恢复

## v0.5 有界多步查询（本地完成）
- [x] step_no、步骤级接受与 Invocation 唯一性、旧数据迁移代码
- [x] 多作业对比、状态诊断、流水汇总、部分结果与恢复
- [x] SQLite/模拟 HTTP 回归（累计 129 项）
- [x] PostgreSQL 16 CI 独立库多步恢复与全新库迁移
- [ ] 既有部署数据上的在线增量迁移验证

## v0.5.1 真实环境验收（P0，仍阻塞生产放行）
- [x] 真实环境烟测入口、PostgreSQL CI 配置
- [x] 确认独立 stone-agent GitHub 仓库存在，初始化 main 与独立开发分支（当前为 Public）
- [x] 当前 v0.7 模块、升级补丁及测试提交 GitHub，并核对 Blob SHA
- [x] 完整基线源码导入独立 GitHub 开发分支（v0.9.1 c959609）
- [x] GitHub CI 实际验收：v0.9.1 修复后 296 passed（45760593186）；v0.9.2 305 passed（45761308366）
- [ ] 仓库可见性是否私有化由所有者决定
- [x] PostgreSQL 16 CI 全新库迁移至 0005_rehearsal，6 项集成测试全部通过
- [ ] 部署目标 PostgreSQL 数据迁移与运行环境验收
- [x] Stone Java 8 / MySQL 5.7 与 8.0 的 V1–V5、全套 Maven 测试与隔离 HTTP 契约验证（v0.9.3）
- [ ] 真实企业授权仓及目标环境业务数据验证
- [ ] 真实 LLM：自然语言意图、实体、工具协议、误调用与澄清评测
- [ ] 统一提议请求模型账本、精确预算与实际模型用量
- [ ] 企业身份、仓库数据域、Stone 服务端授权及基础日志脱敏
- [ ] 在真实业务数据上建立经业务方确认的黄金集与验收基线

## v0.6 库存作业基础异常助手（本地完成）
- [x] 确定性步骤跳过无必要 LLM 调用；MODEL/RULE 来源分离
- [x] StockWorkPolicy 初步业务解耦，复用 Registry 和执行账本
- [x] 六种任务状态与 NOT_FOUND 的规则解释及缺失证据
- [x] 自然语言诊断目标、目标类型与实体共同确认
- [x] 旧执行协议兼容、异常注入和迁移回归（累计 157 项）
- [ ] 完全通用化不设为独立目标；随新增真实业务需求继续解耦

## v0.6.1 历史回执深度诊断（本地完成，Java 扩展已提交）
- [x] 核对 Stone V2/V4 事件表、回执状态机、现有 Mapper 与应用服务
- [x] 设计仓库隔离、只读、最大 100 条的历史回执查询契约
- [x] 编写独立 Stone Java 扩展源码及 3 项 Java 单测源码；默认关闭、开发 token/仓库范围
- [x] Agent 注册 get_stock_work_events、固定 GET、严格 DTO/归属校验
- [x] 新增结构化 DEEP_DIAGNOSE_STOCK_WORK，复用 RULE/Invocation 账本
- [x] 联合状态、回执、完成数量、人工核对标记及双证据 Hash；保留根因未确定
- [x] 不存在、空回执、状态冲突、权限、HTTP 错误、重定向及第二步恢复测试
- [x] 19 项新增 Python 测试通过，累计 176 passed / 4 skipped
- [x] 独立部署说明、演示输出和计划清单
- [x] Stone 扩展源码及三项 Java 单测提交独立业务开发分支（ba80d46）
- [x] Maven 编译及 Java 单测执行（双 MySQL 版本 CI）
- [x] 隔离 MySQL 测试仓库的 JOIN、只读事务、回执顺序和内容验证
- [x] 隔离 CI 实际 Java HTTP/Agent 适配器：历史回执、作业详情及开发凭据
- [ ] 真实自然语言“为什么/历史回执”意图评测及托管会话深度诊断确认（本轮仅结构化入口）
- [ ] 真实生产身份与原因字段的脱敏/留存策略验收

## v0.7 库存与出库诊断（出库分配诊断开发完成，待联调）
- [x] 核对真实 OutboundAllocationApplicationService、InventoryMapper、V1/V3/V4 表及分配状态机
- [x] Stone 聚合式只读查询：单条 SELECT、仓库和订单范围、精确 AVAILABLE 库存维度、冻结状态、成功分配回执存在标记、200 行上限；不暴露幂等键
- [x] Java DDD 接口、DTO、Service、Repository、Mapper 及 4 项 Java 单测源码、1 项真实 MySQL 集成测试源码
- [x] Agent 新增 OutboundArgs / OutboundResult、DIAGNOSE_OUTBOUND_ALLOCATION、get_outbound_allocation_snapshot；复用 Registry / RULE Invocation / Worker / Evidence
- [x] 同一库存 ID 合并剩余需求，区分库存不存在与零余额、冻结与未配置库位、已审核与其他状态、已分配回执
- [x] 严格响应结构、实体归属、数量一致性、重复明细与库存快照检查；不推断历史失败根因，不开放写入
- [x] CLI 和结构化 API、故障恢复及模拟 HTTP 验证；34 项新增 Python 测试通过，历史 176 项回归通过
- [x] GitHub 两个仓库独立分支提交；Agent 三个核心文件 Blob SHA 与本地一致；v0.6.1 Java 历史回执扩展同步至 Stone 分支
- [x] 完整 Agent 基线远程导入（v0.9.1 c959609）
- [x] 后续完整基线 GitHub CI 回归通过（v0.9.2）
- [x] Stone Java Maven 编译、单测及真实 MySQL 集成测试（v0.9.3）
- [x] 隔离 CI 实际 Stone HTTP/Agent 出库 DTO、开发凭据及快照证据验证
- [ ] 生产权限及实际数据验收
- [ ] 托管会话自然语言出库目标与确认协议、真实 LLM 评测
- [ ] 独立库存余额/库位通用查询、上架/移库可行性预检：仅按真实业务需要另行建设
- [ ] 历史失败事件/日志可追溯性（当前成功回执不足以还原历史失败根因）

## v0.8 WES 调度诊断（本地开发完成，真实联调待验收）
- [x] 复查 Stone claim、activeCount、V2 容量/设备/任务表及 NEW claim 限制规则
- [x] 仓库/设备容量及 READY、IN_FLIGHT、UNKNOWN 聚合查询 Java 源码；单条非锁定 SELECT，不新增表
- [x] Java 默认关闭、开发服务 token、仓库范围、设备代码校验；5 项 Java 单测与 1 项 MySQL 集成测试源码
- [x] Agent DIAGNOSE_WES_DISPATCH、WesArgs/WesResult、HTTP Adapter、Registry/RULE Invocation/Evidence
- [x] 确定性领取上限计算；maxReady 只限制入队，UNKNOWN 占槽，缩容保留负剩余量，多瓶颈并列
- [x] 严格响应归属和计数一致性；缺失设备不视为空队列；不调用 claim/report
- [x] CLI / 结构化 API、模拟 HTTP、权限、重定向、恢复验证；新增 41 项，累计 251 passed / 4 skipped
- [x] Stone 独立分支提交（3f6a76f），保留 v0.7 历史
- [x] 完整本地基线导入脚本、升级补丁检查、联调 SOP、合成演示输出
- [x] Agent v0.8 增量源码、测试、补丁提交 GitHub（d4938bc）并核对 SHA
- [x] 完整 Agent 基线远程导入（v0.9.1 c959609）
- [x] 后续完整基线 GitHub CI 回归通过（v0.9.2）
- [x] Maven 编译、Java 单测、MySQL 集成及 Java/Python 跨服务验证（隔离 CI）
- [ ] 真实业务设备权限、容量聚合 SQL 负载测试及 LLM 语义验收
- [ ] 托管会话自然语言 WES/出库目标与确认协议；真实业务用户验收

## v0.9 受控业务操作（取消预检与审批演练本地完成；真实写入关闭）
- [x] 核对 Stone cancelStockWork / StockWorkService.finish / claim 锁仓、READY 状态复查、预占释放及状态级幂等；首期候选 READY 取消
- [x] 基于已完成 RULE 只读 Run 与 Invocation 证据预检；实体、权限、证据 Hash、工具绑定、120 秒有效期检查
- [x] READY 才能创建不可变操作草案；绑定 operation_id、原因、仓库、任务、观察快照与来源；请求幂等与重复内容冲突
- [x] 独立开发申请人及演练审批人 token、独立仓库范围；禁止自审、拒绝、撤回、过期；真实数据禁止审批
- [x] operation_draft / operation_event 事务账本，Alembic 0004_cancel_drafts，SQLite 本地持久化与重启检查
- [x] REST 预检、草案、审查、撤回；无执行接口，无写工具，无写任务入队，任何状态 write_allowed=false / NEVER_DISPATCHED
- [x] 22 项新增 Python 测试通过，累计 273 passed / 5 skipped；真实 PG 并发草案及审批测试在 v0.9.1 CI 通过
- [x] Stone 领取与取消竞争、重复取消仅一次释放及跨仓隔离的 2 项真实 MySQL 测试源码；真实写入契约放行说明提交 9fc7922
- [x] Agent 操作草案及迁移文件远程提交 6f20f79；本地与远程 Blob SHA 一致
- [x] 完整运行时基线远程导入（v0.9.1 c959609；CI 验收单列）
- [x] GitHub CI 与独立 PostgreSQL 16 并发审批验收（v0.9.1/0.9.2）
- [x] Stone Maven 与 MySQL 实际并发验收（隔离 CI）
- [ ] 部署目标 PostgreSQL 既有数据迁移
- [ ] 业务方确认首个受控操作、真实企业身份授权与审批策略；真实模型语义验收
- [x] Stone 服务端 operation_id 回执、幂等、结果查询及域内事务原子性（隔离 CI、测试授权替身）
- [ ] 真实独立企业授权
- [ ] 固定真实写 Workflow、一次性审批消耗、派发前实时复查、UNKNOWN 对账和故障注入
- [ ] 生产安全写入验收；v0.9 不存在真实派发入口

## v0.9.1 取消操作回执及安全对账（内部事务源码、只读对账组件；真实写入仍关闭）
- [x] 核对 v0.9 两仓库 HEAD、Stone 真实取消、锁仓及库存释放规则
- [x] 恢复 v0.8 源码清单并核对 54 个 Blob；43 个缺失文件导入新分支（含 v0.9 API/Store/PG 补丁还原与 SHA 校验），完整运行时及 CI 入库（c959609）
- [x] Stone V5 回执表、按仓库和 operation_id 查询、同事务复用取消与回执写入源码
- [x] Stone 内部 CancelAuthorization 端口（无生产实现、默认拒绝）、READY 与任务身份复核、幂等重放/不同指纹冲突
- [x] Stone 独立默认关闭的 GET 回执接口、开发只读凭据和仓库范围；不存在新增写 POST 接口
- [x] Java/Python 长度前缀 SHA-256 指纹协议及共享黄金向量；Python 验证通过
- [x] Agent ReceiptReader 只读固定 GET、严格匹配操作/仓/任务/指纹；缺失、异常保持 UNKNOWN；无模型工具注册或真实执行入口
- [x] 可序列化对账检查点：UNKNOWN 仅凭匹配回执转 COMMITTED、终态不被缺失响应覆盖、冲突拒绝；仅测试夹具恢复，尚未接入实际持久写账本
- [x] 本地 v0.9 273 passed / 5 skipped；新增 18 passed，累计 291 项通过（分批执行）
- [x] Stone 5 项 Java 单测及 3 项 MySQL 集成测试源码（回执、并发幂等、插入失败回滚）；已提交 f21e06b / 3d39676
- [x] Agent 新增模块及测试提交 847ea2a，Blob SHA 与本地一致
- [x] Java Maven 编译、共享黄金向量 Java 实测、V5 MySQL 原子性/并发/回滚验收（5.7 与 8.0，CI）
- [x] GitHub CI 独立 PostgreSQL 16 全新库迁移及集成回归（296 passed）
- [x] Stone 实际 HTTP / Agent 回执跨服务契约验证（隔离 CI）
- [ ] 真实 LLM 与目标部署数据库验收
- [ ] 企业服务端身份、租户与仓权限、真实审批一次性消耗与有效期、旧写接口访问治理
- [ ] 固定真实 Workflow、持久 SEND_INTENT 与对账 Worker、可信 NOT_COMMITTED 证明、故障注入及安全写入验收；不得将演练审批升级为生产授权

## v0.9.2 持久化操作故障演练（CI 通过；仅合成环境）
- [x] 检查上一版本失败及修复日志：显式 HTTPX 依赖、固定兼容版本；修复后两次 CI 通过，296 passed
- [x] 独立 rehearsal_execution 表、Alembic 0005_rehearsal、SQLite 自动迁移；无真实发送状态
- [x] 仅合成 FixtureStoneService 和显式启用的内部演练服务可消费审批；真实 HTTP 适配器拒绝
- [x] 重新核对原只读证据、Hash、工具绑定、READY、审批有效期、申请人和审批人分离
- [x] 一次性演练审批消费、稳定 operation_id、请求指纹、事务内模拟发送意图和审计事件；消费后不能撤回或重新创建同一演练
- [x] 独立合成回执写入事务；无实际库存修改、Stone POST、生产写工具、执行 API 或真实派发 Job
- [x] 内部 once 对账步骤，专用开发维护身份和租户/仓库范围；Lease/Fencing、SKIP LOCKED、旧 Worker 结果拒绝
- [x] 回执迟到后持久检查点恢复；缺失保持 REHEARSAL_UNKNOWN；最多三次查询，指数退避，耗尽转人工核对提示，不重派
- [x] 8 项新增 SQLite/离线测试，1 项 PostgreSQL 并发测试；本地分批 299 passed / 6 skipped；GitHub CI 305 passed / 1 warning，6 项 PG 全部通过（45761308366，cd6c23b）
- [x] 核心 7 个改动文件 Git Blob SHA 一致性核对并提交独立开发分支
- [ ] Node.js 20 Actions 弃用警告及全量依赖锁定（非阻塞）
- [ ] 目标部署数据库旧数据增量迁移、实际服务部署及生产运行验收
- [x] Stone Maven/V5 MySQL 原子事务和并发/回滚测试（隔离 CI）
- [ ] 真实企业授权、可信审批及旧写接口访问治理
- [ ] 真实持久 SEND_INTENT、派发前实时复查、固定写 Workflow、独立只读 HTTP 对账 Worker、可信 NOT_COMMITTED 证明、实际故障注入与人工接管工作台
- [ ] 真实安全写入验收；演练记录永远不能转换为生产授权或业务提交

## v0.9.3 实际 Java/MySQL 与跨服务验收（隔离 CI 通过；真实写入关闭）
- [x] 核验 v0.9.2 最终 HEAD 两次 Actions 成功，305 passed，6 项 PostgreSQL 集成无跳过（45761709093）
- [x] Stone 新建 Java 8 / MySQL 5.7 与 8.0 双版本 CI，独立一次性数据库，依次执行 V1–V5，Maven clean verify，Surefire 报告必需套件及零跳过门禁
- [x] 双数据库各 38 项 Java 测试（21 单测、17 MySQL 集成）全通过，零失败、错误、跳过；取消与领取竞争、预占释放一次、回执原子事务、插入异常回滚、同键重放和指纹黄金向量（45762032368）
- [x] Agent 增加只读 scripts/stone_ci_contract.py，固定回环地址、显式 CI 开关、独立诊断与回执凭据，Stone 工作流按不可变 Agent SHA 检出
- [x] 从真实 Java 业务测试产生的记录提取 ID，启动实际 Spring Boot JAR；StockWork/历史回执/出库/WES/取消回执均由真实 HTTP 适配器查询，不使用 Mock HTTP Server
- [x] 双 MySQL 版本各 6 组跨服务检查通过：取消及单次释放流水、UNKNOWN/核对历史、出库精确库存与回执、WES 容量、独立计算指纹及缺失回执保持 UNKNOWN、Agent/Stone 权限边界（45762289693）
- [x] 查询前后选定业务表计数与库存余额一致；Agent 发出的业务 POST 为零。Java 集成测试中内部事务写入只发生在一次性测试库，授权使用测试替身；不等同生产授权
- [x] Agent 99c4357 CI 305 passed / 1 warning（45762213821）；Stone 6bf650d CI 双版本成功，未改变领域事务、生产 API 或数据库 schema
- [ ] 目标环境现有数据迁移、真实授权用户、生产服务权限与旧写接口访问治理；实际部署及恢复验收
- [ ] 企业身份与受信审批策略（待确定真实 IAM/网关/审批平台）；不得使用开发凭据或 MockBean 放行
- [ ] 真实业务执行账本、审批消耗与固定 Workflow、真实 SEND_INTENT、跨服务响应丢失/恢复、只读对账 Worker、人工核对及真实写入门禁；演练与生产隔离
- [ ] 真实 LLM、业务黄金集、性能/容量及用户验收

## v0.9.4 Stone 可信审批绑定与唯一消费（隔离 CI 通过；真实授权待接入）
- [x] 检查 v0.9.3 双仓库最终 CI：Agent 305 项、Stone 双 MySQL 各38项及6组真实 HTTP 契约成功（45762570378、45762564734）
- [x] CancelAuthorization 拆分当前访问授权与受信审批核验端口；无生产实现默认拒绝；重放仅查当前权限与原指纹
- [x] VerifiedCancelApproval 绑定签发方、凭证 ID、租户、申请人、独立审批人、完整请求指纹与有效期；拒绝缺失、错误绑定、自审
- [x] 锁仓内复查当前授权、审批、READY 和作业身份，复用已有领域取消事务；数据库 UTC 毫秒有效期校验
- [x] Stone V6 扩展原回执表7个审计字段与跨仓唯一索引 (approval_issuer, approval_grant_id)，不新增表；旧 V5 回执元数据为 NULL，不追认审批
- [x] 最终回执 INSERT SQL 有效期复查；审批唯一消费、取消、预占释放、回执同事务；唯一键/过期/回执写入异常整体回滚
- [x] Java 新增4单元+3集成，双 MySQL 各45项（25单元、20集成），零失败/错误/跳过；V1–V6 全新库迁移及6组既有实际 HTTP 契约通过（45763151336，707c249）
- [x] 实际跨仓并发重复授权标识仅提交一笔；失败方任务 READY、预占不变、无释放流水/回执；最终 SQL 过期注入回滚；回执重放不重复消费
- [x] 原公开回执 DTO 与指纹协议不变；Agent 现有 ReceiptReader 兼容，无真实 POST、新工具或新增 Python 运行时逻辑
- [ ] 真实企业 IAM/审批平台、可信签发者验证、实际撤权策略、网关对旧写接口访问治理；不以 MockBean 或开发 token 代替
- [ ] 目标 V5 存量数据增量迁移、DDL 锁影响、备份恢复与部署验收
- [ ] 真实发送意图、审批消费与执行工作流跨服务衔接、派发前复查、独立真实对账 Worker、响应丢失及人工接管；NOT_COMMITTED 证明
- [ ] 真实 LLM、生产安全写入及用户验收；当前不开放真实写入

## v0.9.5 实际只读回执核对与持久恢复（隔离 CI 通过）
- [x] 核验 v0.9.4 双仓最终 CI（Agent 45763428509、Stone 45763421852）成功。
- [x] 独立维护命令 register/status/once，显式启用、配置固定服务环境及只读凭据；不注册模型工具、不新增公开 API 或发送入口。
- [x] 独立 receipt_check / receipt_check_attempt 账本，0006_receipt_check 迁移与 SQLite 自动建表；不转换演练审批，不伪造真实 SEND_INTENT。
- [x] 操作参考由维护人员独立提供，绑定租户、维护主体、服务 realm、origin、仓、任务、operation_id 与请求指纹；重复登记幂等，不同参数冲突，跨租户/仓及变更服务绑定拒绝。
- [x] 事务内领取与查询尝试持久化，HTTP GET 在事务外执行，Lease/Fencing、SKIP LOCKED、过期领取后恢复，旧结果不得覆盖。
- [x] 只有严格匹配的实际回执才标记 RECEIPT_MATCHED；该状态不等同 Agent 发起操作或企业授权成功；dispatch_state=NOT_TRACKED、write_allowed=false。
- [x] 缺失或传输异常保留 UNKNOWN，最多三次领取，退避；权限或身份不匹配等非重试类结果停止自动领取并提示人工核对；无自动业务重派。
- [x] Agent 核心提交 9a575f1，CI 317 passed / 1 warning（45763923739），新增12项（含1项真实 PostgreSQL 并发），累计7项 PG 集成通过。
- [x] Stone CI 固定检出新 Agent SHA（ed4331e），MySQL 5.7/8.0 各45项 Java、7组真实 HTTP 检查通过（45763988260）；实际 GET 后注入异常，手动模拟租约到期，新进程恢复并持久化回执；选定业务计数及余额不变，Agent 业务 POST=0。
- [ ] 真实操作发送来源与可信身份绑定、企业 IAM/审批签发者验证、撤权及旧写接口访问治理；当前维护主体仅基于本地配置，Stone GET 仍使用开发凭据。
- [ ] 接入实际可信发送账本、固定取消工作流、真实 SEND_INTENT、生产自动对账调度与独立维护身份；不将维护人员输入当作已授权操作。
- [ ] 真实提交响应丢失/故障恢复、UNKNOWN 人工接管及可信 NOT_COMMITTED 证明；当前仅提示人工核对，不支持自动重置预算或人工判定提交。
- [ ] 目标数据库存量迁移、生产凭据、实际业务用户及负载验收；真实写入关闭。

## v0.9.6 UNKNOWN 人工复核与受控只读恢复
- [x] 核验 v0.9.5 双仓库最终 CI：Agent 45764376634 / 45764324490，Stone 45764369838 / 45764352437 全成功。
- [x] 实现 pending-reviews 按租户、维护主体、服务环境及仓库范围发现待核对任务，分页上限100，排除活跃租约和匹配终态。
- [x] 实现 review-retry，原始参考、服务绑定及状态保持不可变；显式请求 ID、预期 fence、非空原因；重复请求不再次 GET，不同内容冲突。
- [x] 独立 receipt_check_review 审计表，与查询尝试通过复合外键绑定；0007_receipt_review 增量迁移和 SQLite 建表。历史记录不回填虚假人工审批。
- [x] 人工查询与原自动预算分离，最多追加两次单次 GET，累计最多五次领取；不重置 attempts/fence，不重新开放自动查询，不允许人工设置提交终态。
- [x] 租约内拒绝复核、过期任务可接管、旧 fence 结果拒绝；崩溃消耗预算，重复请求只返回当前状态；人工查询失败仍 UNKNOWN。
- [x] Agent 核心代码与8项新增测试源码提交 69695dc；Stone 验收工作流固定检出该 SHA，提交 5b7917e，新增实际 HTTP 401 后凭据修复、显式复核、回执匹配及审计验证。
- [x] Agent 核心 CI 325 passed / 1 warning（45765456480，49.69s），新增8项（含1项 PostgreSQL 并发），累计8项 PG 集成通过。
- [x] Stone 45765488016：MySQL 5.7/8.0 各45项 Java 测试，零失败/错误/跳过；8组实际 HTTP 检查通过，真实401后修复凭据、人工复核和幂等审计；业务 POST=0，选定数据不变。
- [ ] 真实 IAM、按实际人员记录身份、生产审批及可信发送记录、旧写接口治理、固定写 Workflow；实际提交响应丢失及 NOT_COMMITTED 证明。
- [ ] 生产自动对账调度、人工升级处理、目标环境存量迁移与备份恢复；当前仅受控本地维护 CLI、生产写入关闭。

## v0.9.7 自然语言业务目标与类型化确认
- [x] 核验 v0.9.6 双仓库最终 CI（Agent 45765724848、Stone 45765721663 及各自另一运行）成功。
- [x] 复用已有三类只读 GoalContract 和执行器：历史回执、出库分配、WES 调度；不新增 Stone 业务 API、工具注册项或数据库表。
- [x] 扩展模型非执行提议 schema 与受限 Fixture 语法，分别校验 task_id、order_id、device_code、requested_limit 的字段角色；不猜测设备或请求数量。
- [x] 扩展 managed session 确认契约；明确目标类型及完整类型化实体，确认前不创建 Run；沿用原事务、幂等、过期、权限和会话边界。
- [x] 同类型的显式短句补参、已确认同类型实体追问，重新确认；禁止跨类型继承，拒绝明显复合操作；模型输出不是授权。
- [x] 确认后复用 RULE 模式执行现有业务工具（历史回执两步、出库和 WES 各一步），不追加执行模型调用。
- [x] 新增参数化测试和模拟模型协议案例；首次 CI 45766133202 在收集阶段失败，定位并修复生成正则的文本替换错误；修复提交 5596372，Stone 固定客户端 af0f02e。
- [x] Agent 修复提交全量 CI 346 passed, 1 warning in 52.42s（45766300805），新增21项测试，历史8项 PostgreSQL 集成未跳过。
- [x] Stone 双 MySQL 各45项 Java 测试、9组实际 HTTP 检查全部通过（45766336138），新增组覆盖三类自然语言确认及真实只读执行，业务 POST=0。
- [ ] 真实外部 LLM Function Calling、独立业务语义黄金集/准确率及真实用户表达覆盖；当前有限语法与模拟模型协议不等于泛化能力。
- [ ] 生产企业身份、真实审批、可信发送账本及旧写接口治理仍为独立专项；真实业务写入关闭。

## v0.9.8 独立业务语义评测与真实模型评测入口
- [x] 核验 v0.9.7 双仓最终 CI（Agent 45766629055 / 45766574348，Stone 45766622682 / 45766603683）成功；Agent 346 passed，1 warning。默认 main 仅 README，完整工程位于版本化开发分支，未擅自合并。
- [x] 44条人工编写合成业务黄金样例（evals/business_goals_v1.json），覆盖六类只读目标、同义表达、字段角色冲突、缺参、拒识及数据域；不从被测解析器反推预期答案，尚未由真实仓储用户审核。
- [x] 独立 agent.eval_goals：复用实际 propose 与 ChatCompatibleProvider；分别记录原始模型候选及最终校验后结果，计算 exact、目标/实体准确率、拒识、澄清、越权、误确认、失败及延迟、Token 覆盖、重复运行稳定性。
- [x] 单条一次模型请求、最多200次/批、重复1–3次；真实模式显式确认隔离环境和配置，无 fixture fallback；评测不创建 Run、不调用 Stone、不使用业务凭据或执行器。
- [x] 新增评测工具测试、CI synthetic fixture baseline 工件及 critical proposal gate；真实模型结果不伪造，不把 fixture 分数当模型准确率。
- [x] v0.9.8 核心 CI 357 passed, 1 warning in 49.18s（45767119437），新增11项；PostgreSQL 回归、依赖检查及迁移通过。
- [x] 44条合成规则基线实测33条完整匹配（75%），可确认目标/实体10/20（50%），拒识13/13，澄清7/8，越权3/3，错误可确认候选0，模型请求0；CI 评测报告已上传。仅规则覆盖率，不是真实模型准确率。
- [ ] 真实模型端点配置、独立重复实测、真实用户语料评审、指标门槛业务方确认、基于实际错误分布优化。当前未提供实际模型端点及凭据，不宣称完成。
- [ ] 生产写入专项、企业身份、实际部署及业务验收依旧独立待办；本轮无 Stone 代码、工具、运行时业务逻辑或数据库 schema 变化。

## v0.9.9 多轮会话语义评测与上下文安全回归
- [x] 复核 v0.9.8 最新 CI：Agent 357 passed，核心评测基线正常；识别 Python TestClient 的上游 AnyIO 弃用警告。
- [x] 新增 14 个真实业务流程形态的合成会话场景、30 个用户回合：确认后继承、缺参澄清、未确认不继承、同业务类型追问、跨类型隔离、跨 Session 隔离、权限撤销、待澄清过期、活跃 Run 阻止并发新提议。
- [x] 新增 agent.eval_sessions，直接调用现有 SessionService；确认后仅使用本地 SQLite + FixtureStoneService 完成合成 Run；评测不访问 Stone HTTP、不执行真实业务写入。
- [x] 模型模式复用 ChatCompatibleProvider；只对未被受限规则处理的自然语言轮次请求模型；模型候选与平台最终结果分开记录；错误/不匹配时中止场景，不确认错误目标。
- [x] 统计 turn/scenario exact、错误确认、澄清与上下文隔离、真实模型请求与 Token 覆盖、P50/P95、重复稳定性；从持久 invocation ledger 统计 Fixture 调用，避免漏计历史回执步骤。
- [x] CI Actions 更新至 checkout@v6 / setup-python@v6 / upload-artifact@v6（Node 24），并上传多轮会话 fixture 基线；Node 20 弃用警告已消除。
- [x] v0.9.9 核心代码最终 CI 365 passed，1 warning，54.48s（45770395968）；SQLite/PG 回归及双评测 artifact 上传成功。文档提交后的 CI 另行复核。
- [x] Node.js 20 Actions 弃用告警已通过 checkout@v6 / setup-python@v6 / upload-artifact@v6（Node 24）消除。
- [ ] Python TestClient DeprecationWarning：当前 FastAPI 0.128.2 将 Starlette 限制为 <0.51，Starlette 0.50.0 调用 AnyIO 4.15 已弃用别名；上游修复位于 Starlette 1.x，需单独进行 FastAPI/Starlette/httpx2 兼容升级与完整 HTTP 契约回归，不通过屏蔽 warning 或降级 AnyIO 掩盖。
- [x] 多轮 Fixture 基线：14/14 场景、30/30 回合完整匹配；16 次本地确认、15 个 Fixture Run 完成、17 条业务查询 invocation，错误确认0；模型请求0，Stone HTTP/写入0（45770395968）。
- [ ] 真实模型端点、真实多轮 LLM 评测及失败样例归因；当前未配置模型服务，不把 Fixture 结果称为 LLM 准确率。
- [ ] 仓储业务人员审核会话黄金集，确认澄清策略、同类型继承与撤权行为是否符合实际岗位工作流。
- [ ] 生产身份、企业授权、真实审批、可信发送账本、真实写入仍为独立专项；本轮没有 Stone 业务代码、API 或数据库 schema 变化。

## v0.9.10 模型 HTTP 协议与评测失败分类
- [x] 复核 v0.9.9 Push/PR CI 45771306705 / 45771314046 均成功（365 passed，1 warning）。
- [x] 固定模型适配器严格响应 envelope、调用 ID、arguments 类型、finish_reason、响应上限及 usage 校验；保留既有业务确认、授权和执行预算。
- [x] HTTP、超时、传输、响应格式错误固定诊断码；单轮、多轮评测增加失败分类及汇总，不暴露上游错误文本、认证值或错误请求内容。
- [x] 本地真实 HTTP 合同测试源码，包含拒绝重定向与无自动重试、错误候选不执行；未使用真实外部模型或 Stone。
- [x] 核心 CI 45774491535：393 passed，1 warning，71.70s；PostgreSQL 迁移与全量回归、双评测基线通过。单轮33/44，多轮30/30，错误可确认候选0。
- [x] 补充真实 loopback 读超时和连接拒绝后，代码提交 c1b78ee 的 Push CI 45774817602 成功：395 passed / 1 warning，67.56s；双评测基线不变。
- [ ] 实际模型服务单轮/多轮评测、真实业务人员评审、依照真实失败分布优化目标识别。
- [ ] Starlette/AnyIO 弃用警告的协同依赖升级；本轮保留现有依赖组合。
- [ ] 企业 IAM、可信审批、生产发送账本及写入授权；本轮无 Stone/数据库 schema 修改。

## v0.9.11 人工回执复核预检与单次请求状态
- [x] 核验最新开发分支为 v0.9.10（32cfafc），Push/PR CI 45775116592 / 45775121473 均成功；人工恢复 v0.9.6 已存在，不重复从旧交付包开发。
- [x] 增加受控维护 review-preview，只读原账本；展示当前租约、自动等待、人工可查询、人工预算耗尽和已匹配五种状态，不执行 GET、不消耗预算、不预留资格。
- [x] 保留 manual_review 语义，新增 manual_retry_allowed；待核对列表显示预算耗尽状态，实际请求仍在行锁内复核 expected_fence、权限、服务绑定、参考及预算。
- [x] review-retry 按原 request_id 返回单次尝试状态、诊断码与证据 Hash；区分 CLAIMED、派生 LEASE_EXPIRED、ABANDONED 和 OBSERVED，重放不追加 GET。
- [x] 新增12项测试源码（含1项独立 PG 并发），覆盖迟到回执、四字段身份不符、过时预检、崩溃/重启/重放、权限与证据、CLI 零网络及预算耗尽。
- [x] 代码提交 e9ed601 的 Push CI 45785967903：407 passed，1 warning，75.56s，零跳过；独立 PostgreSQL 迁移与9项 PG 集成、单轮33/44和多轮14/14场景30/30回合合成基线全部通过。5个提交文件远程内容与提交原文完全一致。
- [ ] 本地环境准备被自动审批检查的使用额度限制拦截，未执行本地回归；当前验收依据为独立 GitHub CI。Starlette/AnyIO 既有弃用警告继续保留，依赖协同升级另列待办。
- [ ] 真实企业 IAM、逐人审计、可信发送记录、业务写入、目标部署环境及生产验收继续独立推进；本轮不修改 Stone、schema、审批授权或写派发。

## v1.0 业务交付与生产验收（计划）
- [ ] 面向仓库/运维岗位的工作台：会话、查询、证据、运行记录、审批（按需）
- [ ] 真实业务任务完成率、延迟、成本、权限与无依据结论率验收
- [ ] 容量测量、备份恢复演练、告警、版本发布与回滚、数据安全
- [ ] 由业务方确认 SLO/RPO/RTO；按实际可用性需求建设 HA
- [ ] 用户验收及上线审批

## 暂缓范围（不计入阶段进度）
多 Agent、向量工具检索、独立反思 Agent、通用 DAG、MCP 工具市场、大规模向量库和微服务拆分。只有新增具体需求或可测量瓶颈时才启动。生产写入、设备回执和调度控制不开放给模型。
