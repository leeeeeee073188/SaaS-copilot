# V2 监控与可观测性

更新：2026-09-10。适用于 `ECHOMIND_DEMO=1` 的客户企业用户支持模式，离线和模型执行共用监控链路。

## 1. 能回答什么问题

- 一次请求慢在哪里：排队、鉴权、记忆读取、意图识别、知识检索、Agent／模型处理、记忆写入。
- 请求是否发生技术失败，还是因权限、席位、预览有效期等业务规则被拒绝。
- 操作之后响应失败时，如何凭请求编号找到关联操作回执。
- 服务重启后能否查到之前的处理记录，以及其他组织或用户能否读取这些记录。
- 模型调用实际报告了多少 token，当前依赖是否可访问。

实现：[monitor/business_monitor.py](../monitor/business_monitor.py)。采用一个轻量监控模块、独立 SQLite 轨迹库和 Prometheus 指标，不依赖额外追踪服务。

## 2. 请求编号和执行阶段

HTTP 入口为每次请求生成完整 UUID，放入 `X-Request-ID` 响应头。不会信任外部传入的编号。

认证后的 `/chat` 把同一编号传入 ChatService、Agent Request 和持久轨迹。成功响应的 `request_id` 与响应头一致；错误也返回编号，前端会显示它。认证和参数校验失败只有 HTTP 指标，不会以未经验证的身份保存租户轨迹。

ChatService 内部使用 ContextVar 关联单次请求，支持异步并发和多 Agent 子任务。记录阶段偏移、持续时间与状态：

| 阶段 | 内容 |
|---|---|
| queue | 等待 ChatService 并发配额 |
| authorization | 再次验证当前组织成员身份 |
| memory_read | 查询工作记忆、情景记忆和偏好 |
| intent | 业务领域与动作分类 |
| retrieval | 初始知识召回，记录来源 ID、数量和 embedding 类型 |
| agent | 确定性执行或模型编排全过程 |
| llm | 每轮 Agent 模型请求 |
| composer | 多 Agent 结果合成，发生协作时记录 |
| memory_write | 保存当前轮对话及可能的归档 |

模型阶段包含在 Agent 阶段中，并行子任务也会重叠，不能把所有 span 耗时相加当作总耗时。额外的按需知识检索体现在工具轨迹，初始 retrieval 字段不是所有召回的累计值。

总 ChatService 耗时包含排队，到监控收尾采样前为止；不含最终轨迹落库开销。HTTP 耗时另外采样，覆盖业务处理与监控收尾，到响应构造完成为止，不等于浏览器端到端耗时。

## 3. 状态与成功率口径

| 请求状态 | 含义 |
|---|---|
| ok | 完成处理，未观察到工具拒绝或技术错误；不等于客户问题已解决 |
| business_rejected | 工具因权限、席位等规则拒绝；正常的业务防线，不算技术故障 |
| degraded | 模型／Agent 未成功完成，或工具发生技术失败但处理链路仍返回了结果 |
| error | 请求发生未处理的技术异常，例如记忆库读取失败 |
| timeout | 包括排队在内的 ChatService 总处理时限触发 |
| cancelled | 应用任务接收到取消信号；不保证所有客户端断连都转换成任务取消 |

工具同时记录 `status` 和归一化 `outcome=ok/rejected/error`，离线与模型模式使用同一适配器埋点，返回耗时、工具名称和效果类型。

只请求了无权限工具但工具未被注册时，离线分支也记录 denied；模型发出未知工具名时统一记为 unregistered，避免不受控名称进入指标标签。

模型多次尝试中的失败工具会保留在轨迹中，即使最终给出回答，也可能标记 degraded。这样不会把“最终有文字”直接视为无故障。

## 4. 轨迹存储与内容最小化

默认数据库位于 `data/flowforge/flowforge.db.traces.db`，与业务事实库分开。表按请求编号存储 JSON，索引覆盖组织、用户和时间。

- 默认写入时清理超过 7 天的记录，并保留全局最新最多 10,000 条。
- 容量限制是整个实例共享，不是每个租户各 10,000 条；繁忙实例可能保留不足 7 天。
- 清理随新轨迹写入触发，不是定时到期删除任务。
- 查询始终限定当前组织和当前用户；同一组织的 owner 也不能通过此入口读取其他用户的轨迹。
- 保存组织／操作者 ID、哈希会话引用、阶段、状态、来源 ID、token 数和业务操作 ID。
- 新持久轨迹不保存原始问题、回复正文、工具参数、完整邮箱、认证凭证或异常消息。
- 新结构化完成日志仅包含请求编号、执行引擎、领域、状态和耗时。原业务模块已有日志不属于这项内容最小化保证。

轨迹落库失败时记录存储错误计数和不含异常详情的错误日志，避免因为观测数据库故障把已成功业务操作改成失败。此时相应轨迹可能缺失，不能声称永久不丢失。

业务变更仍以原操作回执和审计为准。监控与业务状态不是一个事务；进程崩溃、强制终止、外部操作超时或监控存储失败，均可能导致轨迹不完整。

如果操作已成功，但随后的记忆写入失败，轨迹仍包含之前获得的 `operation_ids`，可通过业务回执接口核验，避免盲目重复执行。

## 5. 查询接口和前端

| 接口 | 权限 | 返回内容 |
|---|---|---|
| GET /health | 无需登录 | 应用存活及配置，不检测模型可用性 |
| GET /ready | 无需登录 | 业务 SQLite、轨迹 SQLite、Chroma 和可选 Redis 的访问检查；失败为 503 |
| GET /monitor | 当前演示会话 | 最近 24 小时保留样本的摘要、分位数、工具结果与告警 |
| GET /trace/tools?limit=20 | 当前演示会话 | 默认 20 条、最多 100 条，最新在前 |
| GET /trace/tools/{request_id} | 当前演示会话 | 单条记录；无权限与不存在均为 404 |
| GET /metrics | 独立运维凭证 | 实例级 Prometheus 指标，不接受客户登录 token 作为该凭证 |

工作台增加“服务记录”页面：查看当前企业及本人的样本量、技术失败比例、P50/P95、告警、请求阶段、工具状态和关联操作编号。切换身份会清空当前展示。

`/ready` 检查依赖连接／读取是否可用，不证明写入空间充足、模型凭证有效、业务数据语义正确或外部服务可用。不会为探活触发付费模型调用。

`/monitor` 是对保留记录的快照聚合。小于 5 个样本不产生阈值告警；达到样本数后，技术失败比例超过 20% 或处理 P95 超过 5 秒时返回告警。技术失败统计 error、timeout、degraded，不含业务拒绝和任务取消。

告警当前在查询时计算并展示，不是外部通知，也不是持续运行的后台巡检。空样本的失败率和分位数为 null。摘要会给出 engine 样本分布；混合引擎的整体分位数不宜直接用于模型性能比较。

## 6. Prometheus 指标

每个应用实例使用独立 CollectorRegistry，避免测试和重复创建应用时指标重复注册。计数器与直方图为进程内状态，重启归零；SQLite 轨迹重启保留。

| 指标 | 标签 | 口径 |
|---|---|---|
| flowforge_http_requests_total | route、status | HTTP 响应计数，使用路由模板，不使用完整资源 URL |
| flowforge_http_duration_seconds | route | HTTP 响应构造耗时 |
| flowforge_chat_requests_total | engine、domain、status | 完成、失败、超时或取消的 ChatService 尝试 |
| flowforge_chat_duration_seconds | engine | 每次 ChatService 请求采样一次 |
| flowforge_chat_stage_seconds | stage | 每次阶段执行采样一次 |
| flowforge_tool_calls_total | tool、outcome | 包含业务拒绝的工具调用结果 |
| flowforge_tool_duration_seconds | tool | 每次工具调用耗时 |
| flowforge_llm_tokens_total | kind | 提供商实际返回的输入、输出、缓存读取和缓存创建 token |
| flowforge_trace_storage_errors_total | 无 | 轨迹写入失败次数 |

标签不使用组织、用户、请求 ID、会话 ID、邮箱或完整查询，避免泄露和无界标签增长。HTTP 指标包含监控接口自身的请求；计算业务 SLA 时应选择 `/chat` 等具体路由。

Token 来自 Agent 和 Composer 响应的 usage。提供商未返回某个字段时保留 null，不猜测为零；离线引擎不产生模型 token 数据。缓存 token 与普通输入 token 是否重叠取决于提供商，不能直接求和当作费用。当前未提供金额估算，也未覆盖原分析模式的所有独立模型调用。

### 启用采集

在本地 `.env` 中为 `ECHOMIND_METRICS_TOKEN` 设置独立值，重启后通过 `Authorization: Bearer <该值>` 抓取 `/metrics`。未配置时该入口返回 403，不把凭证放入前端。

同机 Prometheus 配置示例（独立部署的 Prometheus 需使用实际可达地址）：

```yaml
scrape_configs:
  - job_name: flowforge
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /path/to/flowforge-metrics-token
    static_configs:
      - targets: ["127.0.0.1:8000"]
```

以下 PromQL 用于接入后核验，不代表本次已部署 Prometheus／Grafana：

```promql
# ChatService 每秒完成的尝试数
sum(rate(flowforge_chat_requests_total[5m]))

# 仅模型引擎的处理 P95（秒）
histogram_quantile(0.95,
  sum by (le) (rate(flowforge_chat_duration_seconds_bucket{engine="llm"}[5m])))

# 技术失败占比；无流量时不应把无结果解读成 100% 成功
sum(rate(flowforge_chat_requests_total{status=~"error|timeout|degraded"}[5m]))
/
sum(rate(flowforge_chat_requests_total[5m]))
```

## 7. 验证与后续边界

新增测试：[tests/test_business_monitor.py](../tests/test_business_monitor.py)。覆盖轨迹持久化、跨组织及同组织跨用户隔离、参数内容不入轨迹、指标标签、权限拒绝与技术失败区分、排队超时、并发 ContextVar 隔离、保留容量、告警阈值、写入故障降级、真实工具循环中的模拟提供商 token，以及业务写成功后记忆失败时查回执。

2026-09-10 本地验证：新增 6 项监控测试通过，全量 40 项回归测试通过，原 60/60 业务状态用例通过，前端生产构建通过。浏览器实际发送 API 查询后，“服务记录”展示了对应请求、7 个处理阶段和 2 个工具结果。模型用量验证使用模拟提供商，没有调用付费模型。

本次业务复测输出为 `data/eval/observability-business-report.json`，测试日志为 `data/flowforge/monitor-tests.log`。报告记录的 base_revision 为执行时 HEAD，本次监控代码当时位于工作区，不能只凭该 HEAD 重建此次修改。

```powershell
.venv/Scripts/python -m unittest tests.test_business_monitor -v
.venv/Scripts/python -m unittest discover -s tests -q
.venv/Scripts/python -m evaluation.business_evaluator --output data/eval/observability-business-report.json
```

当前仍是单进程轻量观测方案：没有 OpenTelemetry 导出、分布式父子调用传播、外部告警投递、故障恢复工作流或完整 token 成本结算。监控接口也不能证明回答事实正确，回答质量和业务任务完成仍需独立评测。
