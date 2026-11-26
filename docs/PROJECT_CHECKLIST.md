# Stone Agent 项目实施清单（v0.9.2）

更新日期：2026-11-26。以实际业务价值和验收证据作为勾选依据。[x] 表示该项在注明范围内已实现或已验证；[ ] 表示未实现、未验证或存在外部依赖。代码交付不等于真实环境验收。历史版本的勾选依据为历次交付记录及 v0.9.2 累计回归；不代表每项都完成生产验收。

## v0.1 业务接入（本地完成）
- [x] Stone Java 分支的 Controller、DTO、领域规则核对
- [x] 库存作业详情 GET HTTP 适配、参数和响应校验
- [x] LOCAL/HTTP 工具注册、真实执行器绑定、调用账本
- [x] 仓库权限边界与模拟 HTTP 回归（历史 41 项）
- [ ] 真实 Stone Java/MySQL 联调（统一由 v0.5.1 验收）

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
- [ ] Stone Java/MySQL V1–V4：真实授权仓、已知库存任务及流水查询
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
- [ ] Maven 编译及 Java 单测执行
- [ ] 真实 MySQL 测试仓库验证 JOIN 隔离、同一事务快照、顺序与回执内容
- [ ] 真实跨服务烟测（新接口启用、授权、两次 GET、真实证据）
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
- [ ] Stone Java Maven 编译与 7 项新增单测、真实 MySQL 集成测试执行
- [ ] 真实 Stone 与 Agent 跨服务授权及快照证据验收
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
- [ ] Maven 编译、5 项 Java 单测、真实 MySQL 集成及 Java/Python 跨服务验证
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
- [ ] Stone Maven 与 MySQL 实际并发验收；部署目标 PostgreSQL 既有数据迁移
- [ ] 业务方确认首个受控操作、真实企业身份授权与审批策略；真实模型语义验收
- [ ] Stone 服务端 operation_id 可信回执、业务幂等与结果查询；域内写事务原子性及独立授权
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
- [ ] Java Maven 编译、共享黄金向量 Java 实测、真实 V5 MySQL 原子性/并发/回滚验收
- [x] GitHub CI 独立 PostgreSQL 16 全新库迁移及集成回归（296 passed）
- [ ] 真实 LLM / Stone 跨服务联调及部署目标数据库验收
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
- [ ] Stone Maven/V5 MySQL 原子事务和并发/回滚测试；真实企业授权、可信审批及旧写接口访问治理
- [ ] 真实持久 SEND_INTENT、派发前实时复查、固定写 Workflow、独立只读 HTTP 对账 Worker、可信 NOT_COMMITTED 证明、实际故障注入与人工接管工作台
- [ ] 真实安全写入验收；演练记录永远不能转换为生产授权或业务提交

## v1.0 业务交付与生产验收（计划）
- [ ] 面向仓库/运维岗位的工作台：会话、查询、证据、运行记录、审批（按需）
- [ ] 真实业务任务完成率、延迟、成本、权限与无依据结论率验收
- [ ] 容量测量、备份恢复演练、告警、版本发布与回滚、数据安全
- [ ] 由业务方确认 SLO/RPO/RTO；按实际可用性需求建设 HA
- [ ] 用户验收及上线审批

## 暂缓范围（不计入阶段进度）
多 Agent、向量工具检索、独立反思 Agent、通用 DAG、MCP 工具市场、大规模向量库和微服务拆分。只有新增具体需求或可测量瓶颈时才启动。生产写入、设备回执和调度控制不开放给模型。
