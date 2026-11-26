# v0.9.9 多轮会话语义评测与上下文安全回归

## 1. 为什么需要这一阶段

v0.9.8 的业务黄金集只评估单轮自然语言目标提议。企业用户实际操作是连续对话：先给出仓库或任务，再补充缺失字段；确认一个目标后继续查询同类型对象；在未确认、跨 Session、权限变化或前一 Run 尚未结束时，平台必须阻止不安全的上下文继承。

本阶段不新增 Agent 能力、不改 SessionService 业务规则，而是将现有多轮会话契约变成可重复评测的业务场景。这样可以判断当前行为是否符合已实现规则，并为真实 LLM 测试建立多轮回归入口。

## 2. 范围与安全边界

- 数据集：`evals/business_sessions_v1.json`，14个开发者编写的合成会话场景，共30个用户回合。并非客户数据，也未经过仓储业务人员验收。
- 被测对象：实际 `SessionService.propose()`、`SessionService.confirm()` 和现有 RULE Runtime。
- 评测数据库：每个场景一个临时 SQLite 数据库，场景结束自动清理。
- 业务适配器：只注册 `FixtureStoneService`，不实例化 `StoneHttpService`，不连接生产或测试 Stone。
- 确认规则：只有当前回合的最终提议与独立预期完全一致，才允许评测器调用 confirm；若不一致，立即停止该场景后续回合，避免错误实体被继续确认。
- 确认后的合成 Run 仅由 RULE Runtime 调用 FixtureStoneService；不调用真实模型执行器、不发起 HTTP、不写 Stone 业务表。
- Live 模式只把未被确定性会话语法处理的自然语言回合发送到配置的模型服务；确认后的业务 Run 仍然只在本地 Fixture 环境完成。Live 模式不代表真实业务联调。

## 3. 14个业务场景

| ID | 场景 | 验证的业务约束 |
|---|---|---|
| M01 | 确认库存作业后追问另一个任务 | 仅从已确认的 READ 目标继承仓库 |
| M02 | 先提供任务，再补仓库 | NEEDS_INPUT 仅继承待补充字段 |
| M03 | 先提供仓库，再补任务 | 不猜测任务 ID |
| M04 | 尚未确认就追问另一个任务 | 不继承未确认候选 |
| M05 | 出库单确认后追问同类出库单 | 只继承同类业务上下文并重新确认 |
| M06 | 出库诊断后追问库存任务 | 跨业务类型不得继承仓库或实体 |
| M07 | 历史回执诊断后追问另一个任务 | 同类深度诊断重新确认 |
| M08 | WES 设备诊断分步补充仓库和数量 | 多字段逐步澄清 |
| M09 | WES 确认后追问另一台设备 | 同类型字段继承并重新确认 |
| M10 | 新 Session 追问旧 Session 的任务 | Session 之间上下文隔离 |
| M11 | 已确认目标后出现未确认的新目标 | 新的未确认提议阻断旧上下文回退 |
| M12 | 已确认后撤销仓库权限 | 每轮重新校验当前数据域 |
| M13 | NEEDS_INPUT 超过 TTL 后补参 | 过期上下文不能继续补参 |
| M14 | 前一个 Run 尚处于活动状态 | ACTIVE_RUN 阻止新提议 |

每个场景在 fixture 模式使用精确受限语法，在 live 模式优先使用人工改写的自然语言。Live 表达是测试样例，不代表已由真实用户提供。

## 4. 评测链路

```text
business_sessions_v1.json
        |
        v
Evaluation Runner
        |
        +--> 临时 SQLite / SessionService
        |          |
        |          v
        |    propose -> compare Expected
        |          |
        |          +-- mismatch --> stop scenario / skip remaining turns
        |          |
        |          +-- exact + explicit confirm
        |                       |
        |                       v
        |                 confirm -> local Run
        |                       |
        |                       v
        |              RULE Runtime + FixtureStoneService
        |                       |
        |                       v
        |               inspect invocation ledger
        |
        v
turn/scenario metrics + JSON report
        |
        v
CI artifact / regression gate
```

Live 模式下，只有需要模型处理的提议步骤才调用 ChatCompatibleProvider；受限 follow-up grammar 命中的回合不会虚构模型请求。报告会记录实际请求数及服务实际返回的 Token 使用量。

## 5. 指标定义

- `scenario_exact_rate`：一个场景所有预期回合均匹配、无跳过，且所有显式确认后的 Fixture Run 均完成，才算场景通过。
- `turn_exact_rate`：实际执行回合中，status、goal kind、candidate 或预期错误码完全匹配的比例；跳过回合单独统计，不混入分母。
- `confirmable_goal_entity_exact_rate`：应进入 NEEDS_CONFIRMATION 的回合，目标类型与完整实体同时正确的比例。
- `false_confirmation_count`：预期应澄清、拒识、越权或冲突，却返回可确认提议的数量。
- `wrong_confirmation_count`：预期可确认，但返回了错误的可确认目标或实体的数量。
- `critical_proposal_count`：上述两种错误之和。它是提议质量风险，不代表实际业务执行；评测器会在任何不匹配时停止确认。
- `confirmation_count`：通过独立预期检查后创建的本地合成 Run 数。
- `fixture_completed_runs`：本地 RULE Runtime 完成的合成 Run 数。
- `fixture_service_invocations`：从持久 invocation ledger 中按固定业务查询 alias 统计，覆盖历史回执步骤；不依赖 FixtureStoneService 内部计数器。
- `local_rule_invocations`：本地 Run 持久化的全部工具调用记录。
- `stone_http_calls` / `stone_business_writes`：固定为0；该评测器不构造真实 Stone HTTP 适配器。
- `model_requests`、`reported_token_sum`、`reported_token_coverage`：仅统计真实模型服务请求与服务实际返回的 Token；fixture 模式为0/空值。
- `latency_ms_p50/p95`：每回合 SessionService.propose 的本地端到端延迟，不等于真实模型推理延迟。
- `unstable_scenarios`：重复1–3次时，比较相同场景最终提议结果是否变化；有限重复不构成总体稳定性证明。

## 6. 运行 SOP

### 6.1 确定性 Fixture 基线

```bash
python -m agent.eval_sessions --mode fixture --fail-on-critical > session-fixture.json
```

CI 同时执行全量 Pytest、PostgreSQL 迁移/集成测试、单轮语义基线与多轮会话基线，并上传 `multi-turn-session-fixture-baseline` artifact。

### 6.2 真实模型提议评测

需要一个独立、已授权、只接收合成数据的 OpenAI-Compatible 模型端点：

```bash
export MODEL_BASE_URL=http://127.0.0.1:8000/v1
export MODEL_NAME=your-actual-model
# 需要鉴权时，从受控密钥管理系统注入 MODEL_API_KEY。
python -m agent.eval_sessions --mode live \
  --confirm-isolated-dev --repeats 3 --fail-on-critical \
  > session-live.json
```

14个场景、30个回合，三次重复最多90个回合，低于200回合预算。模型提议与预期不匹配时，场景停止；后续依赖上下文的回合会被标记 skipped，不会用错误实体继续确认。真实模型服务的费用、响应时延及 Token 用量按实际记录统计。

## 7. CI 与依赖警告治理

GitHub Actions 更新至 `actions/checkout@v6`、`actions/setup-python@v6`、`actions/upload-artifact@v6`，三者使用 Node 24，消除旧 Node 20 runtime 弃用警告。

当前保留一条 Python 上游依赖警告：FastAPI 0.128.2 约束 Starlette `<0.51.0`，仓库使用 Starlette 0.50.0；AnyIO 4.15 将 `anyio.abc.BlockingPortal` 标记为弃用，而该 Starlette TestClient 版本仍使用该别名。Starlette 上游已在后续 1.x 版本修复（#3498），但这需要 FastAPI、Starlette 和 TestClient 底层 HTTP 客户端的协同升级。当前不通过过滤 warning、任意降级 AnyIO 或直接引入 Alpha 版本掩盖问题。单独建立依赖升级任务，完成全部 API/HTTP 契约测试后再合并。

## 8. 验收边界及下一步

本阶段只新增评测数据、评测 Runner、自动化测试、CI artifact 与文档；不修改 SessionService、业务工具注册、Stone API、Stone 数据库 schema、Agent Alembic 版本或生产写入路径。合成 Fixture 全通过仅说明当前代码符合这14个手工定义的会话契约，不代表真实 LLM 具备相同准确率，也不代表业务人员认可所有上下文继承策略。

下一阶段优先级：
1. 配置真实模型端点，在隔离环境执行 live 模式，收集原始候选与最终提议差异。
2. 由仓储/调度业务人员审阅合成语料和 expected，补充真实表达及边界案例。
3. 按错误分布优化目标识别 Prompt、参数 Schema 或平台校验；不为提高分数放宽权限、澄清或确认门禁。
4. 单独完成 FastAPI/Starlette/httpx2 兼容升级。
5. 只有在模型质量和会话工作流通过业务验收后，再进入面向用户的会话工作台。
