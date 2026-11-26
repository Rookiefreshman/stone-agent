# PostgreSQL 持久化、Worker 与 SSE（v0.5）

## 1. 架构决策与边界

沿用 V3.1 只读协议，v0.5 扩展为有界多步骤；本阶段不引入 Redis、Celery 或独立消息中间件。以 `run` 表作为 P1 持久任务队列，T1/确认事务中原子写 Run 和 QUEUED 事件，不再引入一个与 Run 可能发生双写不一致的 Job 表。P3 审批等待、独立对账及工作流步骤需要单独 Job 模型时再扩展。`event` 表是同事务运行事件账本，支持 SSE 续读，不是具备外部消息投递/确认语义的完整 Transactional Outbox。

| 组件 | 职责 |
|---|---|
| `agent/store.py` | SQLite 后端及共用 repository 操作，事务、run queue、模型/调用账本 |
| `agent/postgres.py` | psycopg 实现，短事务、数据库时钟、行锁、SKIP LOCKED；仅内部固定 SQL 占位符转换 |
| `migrations/versions/0001_agent.py` | 初始 PostgreSQL DDL；独立 Alembic 部署，不自动建表 |
| `agent/worker.py` | 独立进程，自动领取、续租、恢复，限定开发主体 |
| `agent/api.py` | 提交、查询、取消及 SSE，保留手动 advance 作为兼容调试入口 |

```mermaid
flowchart TD
 A[API / confirmed proposal] --> B[Transaction: Run + QUEUED event]
 B --> C[(PostgreSQL)]
 C --> D[Worker claim with SKIP LOCKED]
 D --> E[Runtime with fence]
 E --> F[Model or Stone read]
 F --> G[Persist checkpoint and events]
 G --> C
 C --> H[SSE by event cursor]
```

## 2. PostgreSQL 16 开发环境部署

先安装 Python 3.11+、Docker（或者使用独立空的 PG16 开发库）。以下命令仅用于开发机；生产禁止直接复用开发凭证及单机拓扑。

```bash
cd stone-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 本地开发，随机密码；不要提交到 Git。
export PG_DEV_PASSWORD="$(python -c 'import secrets; print(secrets.token_hex(24))')"
docker volume create stone-agent-pgdata
docker run -d --name stone-agent-pg \
  -e POSTGRES_USER=stone_agent \
  -e POSTGRES_PASSWORD="$PG_DEV_PASSWORD" \
  -e POSTGRES_DB=stone_agent \
  -p 127.0.0.1:15432:5432 \
  -v stone-agent-pgdata:/var/lib/postgresql/data \
  postgres:16
docker exec stone-agent-pg pg_isready -U stone_agent -d stone_agent
# 等 pg_isready 确认 accepting connections 后再迁移。
export AGENT_DATABASE_URL="postgresql://stone_agent:${PG_DEV_PASSWORD}@127.0.0.1:15432/stone_agent"
python -m alembic upgrade head
python -m alembic current
```

预期 revision `0002_steps`。上面是待执行部署步骤，不是已经实测的 PostgreSQL 部署记录。已有 PostgreSQL 业务数据库不得直接执行该迁移；使用独立 Agent 数据库。迁移用户、运行用户在生产应分离，DDL 由运维专用角色执行。运行时校验 Alembic 版本，缺失时启动失败，不回退到 SQLite。SQLite v0.3/v0.4 开发数据可经启动迁移继续使用，当前没有历史数据搬迁工具；需要迁移旧数据时先停写并设计校验/回滚流程，不能直接复制 SQLite 文件。

## 3. 启动 API 与 Worker

API 与所有 Worker 必须配置一致的 `AGENT_DATABASE_URL`、模型、Stone 端点和开发身份权限。在同一已配置 shell 或受保护的环境配置下启动进程。默认只使用 fixture 数据与模型。

```bash
export POC_DEV_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(24))')"
export STONE_ALLOWED_WAREHOUSES=1
export POC_LIVE_MODEL=0
export STONE_LIVE_DOMAIN=0
uvicorn agent.api:create_app --factory --host 127.0.0.1 --port 18080
```

另一终端加载相同配置（不要重新生成 API token/数据库密码），执行：

```bash
python -m agent.worker --poll 1 --lease 60
# 可在第三终端启动另一个同配置 worker。
# 单次领取并执行（CI 或调试）：python -m agent.worker --once
```

SQLite 回归路径：不设置 AGENT_DATABASE_URL，API 和 Worker 使用同一个绝对 `POC_DB` 文件路径。SQLite BEGIN IMMEDIATE 串行事务仅适合开发验证。

## 4. 提交后自动执行

按照 README v0.3 会话流程创建 session、proposal、confirm。confirm 返回 HTTP 202 和 run_id。无需调用 advance，Worker 会自动领取；查询 `GET /api/v1/runs/{run_id}`。示例（使用实际返回的 run_id）：

```bash
curl -N -H "Authorization: Bearer $POC_DEV_TOKEN" \
  "http://127.0.0.1:18080/api/v1/runs/$RUN_ID/events"
# 断开后续读：
curl -N -H "Authorization: Bearer $POC_DEV_TOKEN" \
  -H "Last-Event-ID: $LAST_SEQ" \
  "http://127.0.0.1:18080/api/v1/runs/$RUN_ID/events"
```

SSE 仅发送事件元数据（无完整业务 payload），通过 run_id + seq 续读，授权检查每轮重做；每次连接最多 30 秒，客户端负责重连，已终态且事件读完则关闭。浏览器需使用支持 Authorization header 的 fetch streaming 或受控同源代理，不能把 token 放在 URL。客户端读取最终结果仍需 GET run。

## 5. 并发、恢复及边界

- `SELECT ... FOR UPDATE SKIP LOCKED` 领取同主体的 QUEUED / 过期 RUNNING；短事务写 fence 和 lease、回收未发送预留与未知发送；租约使用 PostgreSQL `clock_timestamp()`，不使用 Worker 本地时钟。
- Session 确认、Run 领取、guard/取消、模型及调用账本推进使用相应行锁，外部网络调用不持数据库事务。幂等提交用唯一键 + ON CONFLICT DO NOTHING。
- 续租每 lease/3 秒，120 秒后停止续租，过期可被接管；该上限不等于强制中断网络 I/O。旧 fence 无法提交新状态，已发送安全读调用可能重复执行。模型调用与读调用各有尝试上限。
- Worker 只执行配置的 demo/local-dev 主体，不从数据库行凭空获得用户身份；仓库范围取自开发环境配置。真实 IdP、每次执行的动态授权及多租户 Worker 调度尚待建设。
- 目前连接按事务创建，没有连接池；高负载、长期 poison run、队列公平性、死信/外部 Outbox、RLS、真实时钟漂移、SSE 规模压测、PG HA、真实进程故障及灾难恢复尚未验证或实现。提议网络请求预算仍未纳入统一模型账本。完整请求内容留存策略亦未生产化。

## 6. 验收命令

```bash
# 本地回归（未配置 TEST_POSTGRES_DSN 时 PostgreSQL 用例明确跳过）
python -m pytest -q -rs
# 专用测试数据库，不使用生产库：先执行迁移，再运行 PG 用例
export TEST_POSTGRES_DSN="$AGENT_DATABASE_URL"
python -m pytest -q tests/test_postgres.py
```

CI 使用 PostgreSQL 16 service，先升级 Alembic，再执行全部用例。CI 配置已编写但尚未在 GitHub 运行。离线 DDL 编译不证明 PostgreSQL 在线事务、行锁及并发正确性。需实际运行后才能将 PG 标为验收通过。
