# v0.9.8 独立业务语义评测

## 目的与范围
同一业务意图可能有不同文本表达，合法 Function Calling JSON 也不等于正确业务目标。新增独立、人工编写的合成黄金集和评测程序，直接复用 v0.9.7 的 propose() 与真实 ChatCompatibleProvider。评测不确认提议，不创建 Run，不实例化 Runtime、不读取 Stone、不发送业务写入。不新增模型工具、API、数据库表或外部基础设施。

evals/business_goals_v1.json（44条）标注预期状态、目标类型与业务实体；包含6类可确认只读目标、语序/表达变化、角色冲突、参数缺失、禁止/复合操作及越权。预期并非由被测解析器生成；仍是开发者合成样例，尚未经业务用户独立验收。此版本只评估单轮目标提议，已有多轮会话测试继续回归，真实多轮评测待后续。

## 指标及失败定义
- exact_match_rate：平台最终 status、kind、candidate 全部相等才通过，分母为全部样例/重复次数。
- confirmable_goal_accuracy / confirmable_entity_accuracy：应可确认的样例中，分别核对最终目标类型与实体；不替代完整匹配率。
- raw_goal_accuracy / raw_entity_accuracy：原始 schema-valid 候选在应可确认样例上的目标与实体匹配；保留原始候选及平台校验后结果，便于区分模型与校验层问题。原始合法候选计数不等于语义准确率。
- unsupported_recall、clarification_exact_rate、authorization_exact_rate 分别以对应预期类别为分母。空分母返回 null；PROPOSAL_FAILED 不能计作正确拒识。
- false_confirmation_count：本应拒识、澄清或禁止却返回可确认候选；wrong_confirmation_count：本应可确认但最终可确认参数/目标错误；critical_proposal_count 为两者之和。它衡量错误提议，不表示实际执行或越权写入。
- 延迟是 propose 的端到端耗时（含本地校验）；P50/P95 使用 nearest-rank，Token 只统计服务实际报告的 total_tokens、同时提供覆盖率；没有报告不按零 Token 计。
- 重复1–3次，unstable_case_ids 只在重复大于1时报告最终提议变化；这是有限采样，不是总体稳定性证明。最多200个样例运行，单次 provider 调用，不自动重试。模型、数据集 Hash、明细和分类指标保留，不输出认证凭据/完整模型请求或原始任意文本。结果可能含评测实体参数，只使用非敏感合成数据。

## 运行 SOP
在开发环境安装 requirements.txt 后：
```bash
python -m agent.eval_goals --mode fixture --fail-on-critical > semantic-fixture.json
```
这只是有限规则基线，不使用真实 LLM。CI 生成 semantic-fixture-baseline artifact；关键错误提议非零则失败，完整匹配率当前作为诊断指标而非业务 SLA。

实际模型评测（需要独立、已授权的测试模型服务，确保只提交合成数据）：
```bash
export MODEL_BASE_URL=http://127.0.0.1:8000/v1
export MODEL_NAME=your-actual-model
# 若服务要求认证，从受控环境注入 MODEL_API_KEY，不提交源码。
python -m agent.eval_goals --mode live --confirm-isolated-dev \
  --repeats 3 --fail-on-critical > semantic-live.json
```
可根据业务方确认的指标显式增加 --min-exact 0..1。未指定时无任意准确率放行阈值；真实模式配置缺失立即退出，无 fixture fallback。44条×3=132次模型请求，费用由模型服务决定。--dataset 可指定经独立审核的自定义非敏感合成集合。
报告中按 case ID 查看 expected/raw/actual：raw 错而 actual 澄清属于模型错误被平台约束；raw 对而 actual 错属于后处理缺陷或评测契约差异；raw/actual 均错需先分析意图边界，再针对证据调整提示词或契约。不能通过把预期标签改成实际输出提高分数。

## 验收边界
Agent 核心提交 ca43db42aa224af786d78b55bb8e52bd75071998，[CI 45767119437](https://github.com/Rookiefreshman/stone-agent/actions/runs/45767119437)。实测 357 passed, 1 warning in 49.18s，新增11项测试；CI 规则基线 33/44 完整匹配（75%），可确认目标及实体匹配均10/20（50%），拒识13/13、缺参澄清7/8、越权3/3；错误可确认候选0、PROPOSAL_FAILED 0、模型请求0，44个原始规则候选符合 schema。P50/P95 为0.013/0.028ms，仅本地规则提议耗时。11个未匹配为10个应可确认及1个应澄清样例，当前规则拒识；不通过修改标签或无限增补正则追求满分。报告 artifact semantic-fixture-baseline 已上传。测试还用模拟 Provider 复现错误读目标或错误实体可能得到待确认候选，评测能够识别这些问题；不代表实际外部模型已出现这些错误。尚未执行外部真实模型评测。Stone v0.9.7 最终 CI 已通过（45766622682），本轮纯评测模块不修改 Stone、不重新建设跨仓流水线。Agent 0007_receipt_review 与 Stone V6 不变。真实企业 IAM、可信审批、实际执行、目标环境迁移、真实用户评测和生产写入仍待验收。
