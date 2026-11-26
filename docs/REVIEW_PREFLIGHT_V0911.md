# v0.9.11 人工回执复核预检与单次请求状态

## 目的与边界
人工恢复已由 v0.9.6 实现。本轮基于 v0.9.10 的完整开发分支补齐操作前预检及崩溃重放状态，保留每次 GET 的预算、行锁、fence、原始参考和回执证据。真实业务写入继续关闭；不新增 Stone 接口、模型工具、公开 API 或数据库迁移。main 不合并。

## 操作 SOP
沿用 v0.9.5/0.9.6 的显式启用、独立只读凭据、固定服务 realm/origin 及租户/仓库范围。升级前停止相关维护进程，备份并确认 Agent 数据库已迁移至 0007_receipt_review；本轮 schema 不变。先查询 pending-reviews，再对指定 check_id 执行：

```bash
python -m agent.receipt_check review-preview "$CHECK_ID"
python -m agent.receipt_check review-retry "$CHECK_ID" --request-id "$REVIEW_REQUEST_ID" --expected-fence "$CURRENT_FENCE" --reason "已核对原始参考并恢复只读访问"
python -m agent.receipt_check status "$CHECK_ID"
```

review-preview 只读已有账本，不执行 GET、不消耗预算、不延长租约、不预留复核机会。返回的 fence 是快照；实际 review-retry 仍在行锁内重新校验当前授权、服务绑定、参考完整性、租约和预算。并发导致 fence 改变时先重新预检，不以旧快照重试。

| review_readiness | 含义与后续动作 |
| --- | --- |
| AUTOMATIC_RETRY_PENDING | 自动查询尚有机会，由现有 once 路径及退避控制继续；不提前追加人工查询 |
| LEASE_ACTIVE | 当前租约仍有效，等待结束；重复原 request_id 只查看状态 |
| MANUAL_RETRY_AVAILABLE | 可显式申请一次 GET；需核对独立原始参考、当前 fence 及原因 |
| MANUAL_BUDGET_EXHAUSTED | 人工预算已耗尽，升级独立业务核查；不得重置预算或重新登记来绕过 |
| RECEIPT_ALREADY_MATCHED | 已有严格匹配回执，无需再次查询；不构成新的业务授权 |

manual_review 仍表示“需人工核对”。新增 manual_retry_allowed 表示当前是否还有单次查询机会；两者不可混用。pending-reviews 保留预算耗尽条目，并显示这两个新字段，避免让维护人员误以为列表中每条都能继续查询。

## 单次请求与整体核对状态
review-retry 的 state 仍为整个 check 的当前状态。新增 review_attempt_status、review_code、review_evidence_hash 绑定本次 request_id 所对应的查询尝试；replayed=true 永远不追加 GET。后续复核已经成功时，重放早先崩溃请求可以同时返回当前 RECEIPT_MATCHED 和该旧请求的 ABANDONED，不能把后续成功归因于早先请求。

| review_attempt_status | 含义 |
| --- | --- |
| CLAIMED | 该查询已领取且租约仍有效；尚无持久化观测 |
| LEASE_EXPIRED | 派生展示：原尝试仍存为 CLAIMED，但租约已过期；不宣称 GET 从未发生 |
| ABANDONED | 新领取已将旧 CLAIMED 尝试标为放弃；旧 Worker 不能落库 |
| OBSERVED | 已持久化观测；结合 review_code 和证据 Hash 判断结果 |

LEASE_EXPIRED 不修改历史状态，不消费新预算。当前仍无法区分同一受控部署内的真实员工；actor=local-receipt-operator 属于维护配置，不是企业 IAM。维护人员提供的参考不是可信发送账本，RECEIPT_MATCHED 只表示读取到了与独立参考一致的回执。404、超时、预算耗尽和人工意见均不能证明 NOT_COMMITTED。

## 验证与放行
新增回归覆盖真实 loopback HTTP 的迟到回执、四个身份字段不符、重启恢复、租约内/过期/后续接管的幂等重放、过时预检、只读 CLI、权限/服务绑定/证据校验及预算耗尽后禁止继续 GET；另有独立 PostgreSQL 并发不同 request_id 竞争测试。实际 Stone Java/MySQL 未修改；本轮不宣称新做真实 Stone、LLM、企业授权或目标部署环境验收。

基线 32cfafc 的 Push/PR CI 45775116592 / 45775121473 均成功。当前本地测试环境准备因自动审批检查的使用额度限制受阻；新增代码测试结果以本轮实际 CI 为准，未执行项保持未验收。

## 本轮实际验收证据
代码提交 e9ed60196c8b5b7d445d0dd9305a33cba565bcad 的 Push CI [45785967903](https://github.com/Rookiefreshman/stone-agent/actions/runs/45785967903) 成功：407 passed，1 warning in 75.56s，零跳过，包含12项新增用例及累计9项 PostgreSQL 集成。依赖一致性检查、0007_receipt_review 全新库迁移、单轮33/44、多轮14/14场景30/30回合合成基线与工件上传全部通过。合成基线不是真实模型准确率。5个提交文件已按不可变 commit 逐一读取，与提交原文完全一致。既有 Starlette/AnyIO DeprecationWarning 未屏蔽；本地回归未执行。

代码和文档位于独立分支 agent/review-preflight-v0.9.11，[草稿 PR #14](https://github.com/Rookiefreshman/stone-agent/pull/14) 基于 v0.9.10；未合并 main，真实企业身份、审批、发送账本和业务写入继续关闭。
