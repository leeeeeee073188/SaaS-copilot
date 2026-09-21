# SaaS Copilot × FlowForge Cloud

项目展示名统一为 **SaaS Copilot**，技术标识使用 `saas-copilot`，配置前缀使用 `SAAS_COPILOT_`。
升级旧部署时需同步修改环境变量键名；工作目录、Git 远程地址和已有数据目录无需迁移。
Compose 后端服务名现为 `saas-copilot`，前端代理和监控目标已同步更新。

面向使用 SaaS 产品的企业用户，提供产品咨询、API 对接支持、订阅账单和企业账户管理。
FlowForge 是有状态的模拟 SaaS 产品：所有业务操作仅修改本地数据，不产生真实扣款或邮件。

本分支 `feat/b2b-saas-agent-v2` 独立维护当前产品。旧内部分析模式已删除；历史版本见提交 `df3cbd1`。

## 当前实现

- 三类 Agent：Triage（product）、Support（integration）、Success（billing/account）。
- 规则识别领域与动作；只读请求主辅并行，变更仅由主领域处理。
- 每个 Agent 具有专属任务、工具范围和领域证据；结构化并行结果经过引用校验后汇总。
- 服务端组织身份、工具鉴权、订阅预览与确认、幂等回执、事务审计。
- Chroma 产品规则与组织快照；Redis 工作记忆（24 小时 TTL）、Chroma 历史片段与显式偏好。
- 持久请求轨迹、阶段耗时、工具结果、模型 token 用量、Prometheus 指标和服务记录页面。

## 本地启动

Python 3.12，Node.js 22。Windows 示例：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
docker compose up -d redis
.venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

另开终端：

```powershell
cd frontend
npm ci
npm run dev
```

打开 http://127.0.0.1:5173，选择演示组织和角色。首次启动自动导入 34 条 Chroma 模拟文档。
默认 `SAAS_COPILOT_DEMO_LLM=0` 为确定性演示；配置模型凭证并设为 `1` 后启用真实 Agent。
不再需要 `SAAS_COPILOT_DEMO` 模式开关；保留的 `SAAS_COPILOT_DEMO_*` 配置用于模拟产品和模型选择。

## 验证

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m evaluation.business_evaluator --engine offline --output data/eval/business-report.json
.venv/Scripts/python.exe -m saas.seed --help
```

60 个参数化业务状态用例使用真实 SQLite/Chroma 和共享 ChatService，不是模型质量评分。
专业化编排测试使用 fake provider 验证工具循环、越权拒绝、引用及部分失败。

## Docker

复制 `.env.example` 为 `.env` 后运行 `docker compose up --build -d`，前端 http://127.0.0.1:5174。
包含后端、前端与 Redis。业务和 Chroma 数据持久化在 `flowforge-data` 卷，Redis 使用独立卷和 AOF。
工作记忆必须使用 Redis，连接失败不会降级到 SQLite；默认地址 `redis://127.0.0.1:6380/0`。
外部 Prometheus 配置示例在 `config/prometheus.yml`，需单独挂载与后端一致的监控 token 文件。

## 代码与文档

| 目录 | 职责 |
|---|---|
| api | 当前应用入口、HTTP 接口 |
| agents | 三类业务 Agent、工具调用与结果汇总 |
| core | 业务意图识别与模型响应文本处理 |
| saas | 模拟产品、权限、知识、记忆、工具与共享聊天链路 |
| monitor | 持久轨迹与指标 |
| evaluation | 业务状态测评 |
| frontend | 企业服务工作台 |

- [当前实现说明](docs/current-implementation-summary-2026-09-10.md)
- [完整产品介绍](docs/flowforge-product-introduction.md)
- [专业化分工](docs/specialization-v2.md)
- [监控与可观测性](docs/observability-v2.md)
- [分支清理记录](docs/standalone-cleanup.md)
- [业务规范](docs/spec-b2b-saas-v2.md)

`docs` 中早期研究与实施报告保留为历史依据，不代表当前代码仍包含旧模式。

工作记忆切换及高并发方案见 [Redis 工作记忆与并发设计](docs/redis-working-memory.md)。
已有 SQLite 会话可运行 `.venv/Scripts/python.exe -m saas.migrate_memory` 复制到 Redis，已有 Redis 会话不会被覆盖，旧表不删除。
