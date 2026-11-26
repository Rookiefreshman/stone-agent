# v0.9.10 模型 HTTP 协议与评测失败分类

## 范围与动机
v0.9.9 真实模型服务尚未配置；现有 ChatCompatibleProvider 将 HTTP、传输与响应格式错误混为 ModelAttemptError，无法区分鉴权、限流、服务端错误和模型候选问题。本次先完成可验证的协议边界与失败归因，不修改 Prompt、目标语义、权限或 Stone 业务接口。实际外部模型准确率仍待验收。

## 模型边界
固定配置的 /chat/completions、非流式 POST，每次 generate 最多发起一次 HTTP 请求；禁止重定向、环境代理和自动重试。已有 Runtime 的持久化尝试预算策略不变。限制响应 1,000,000 字节；外层严格 JSON（重复键、NaN 拒绝）、恰好一个 choice、合法 finish_reason、function 类型、非空唯一调用 ID 和字符串 arguments。非 TOOL_CALLS（包括截断、过滤）不会向下游提供部分调用。合法多工具响应最多16项，现有单步目标校验仍拒绝多个工具；参数 JSON/schema 仍由实际业务目标或执行工具校验。缺失/null usage 视为未报告，不计0；负值、布尔值拒绝。保持 endpoint revision 兼容，无迁移。
HTTP 分类：MODEL_HTTP_AUTH(401/403)、MODEL_HTTP_RATE_LIMIT(429)、MODEL_HTTP_REDIRECT(3xx)、MODEL_HTTP_CLIENT(其他4xx)、MODEL_HTTP_SERVER(5xx)、MODEL_HTTP_OTHER；MODEL_TIMEOUT、MODEL_TRANSPORT、MODEL_RESPONSE_TOO_LARGE、MODEL_RESPONSE_PROTOCOL、MODEL_PROFILE_MISMATCH。异常只暴露固定代码，不记录上游错误文本或认证值。继承 ModelAttemptError，原业务 API PROPOSAL_FAILED / 旧 Runtime 预算策略保持不变。

## 评测诊断
单轮和多轮 JSON 报告增加 provider_error_code、diagnosis 和聚合 diagnostic_counts/provider_error_counts。分类 MATCHED、PROVIDER_FAILURE、TOOL_PROTOCOL_MISMATCH、CANDIDATE_SCHEMA_INVALID、RAW_CANDIDATE_MISMATCH、POST_VALIDATION_MISMATCH、NONCONFIRMABLE_EXPECTATION_MISMATCH、CONTEXT_OR_RULE_MISMATCH、FIXTURE_OR_CONTEXT_MISMATCH、CONFIRMATION_OR_RUNTIME_FAILED、SKIPPED_AFTER_MISMATCH。分类依据实际返回及预期，不是根因证明；非可确认预期不武断判定模型错误。多轮出错停止依赖回合，禁止确认错误提议。原指标不变。

## 验收及运行
真实 HTTP 合同测试使用本地 ThreadingHTTPServer，不使用真实模型或 Stone，不改数据库或业务写入。覆盖认证、HTTP 状态、重定向、重复 JSON key、非法 envelope、截断、usage、限长和评测故障归因；补充 localhost 读超时与连接拒绝测试。核心 CI [45774491535](https://github.com/Rookiefreshman/stone-agent/actions/runs/45774491535) 实测 393 passed / 1 warning（新增28项），单轮33/44，多轮30/30，错误可确认候选0，模型真实请求0；警告仍是 Starlette/AnyIO TestClient 弃用别名。
```bash
python -m pytest -q tests/test_model_http.py
python -m agent.eval_goals --mode fixture --fail-on-critical
python -m agent.eval_sessions --mode fixture --fail-on-critical
```
真实模型端点由用户在隔离环境自行配置（MODEL_BASE_URL、MODEL_NAME、可选 MODEL_API_KEY），使用原有 --mode live --confirm-isolated-dev。无需提供密钥到聊天或提交仓库。下一步基于实际报告和业务方确认的黄金集优化 Prompt 或校验，不通过放宽确认与授权提高分数。真实 LLM、企业 IAM、生产写入和部署验收仍未完成。
