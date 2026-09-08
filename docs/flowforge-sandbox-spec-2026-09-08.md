# FlowForge Cloud：支撑 EchoMind 的有状态 SaaS 产品沙箱

版本：V2，2026-09-08。本文是待实施设计；金额、客户、域名、时间与配额均为合成规则，不是商业报价。此次只更新文档，未生成运行中的 SaaS 或已导入的数据集。配套：[Agent 方案](./b2b-saas-agent-plan-v2-2026-09-08.md)、[一手调研](./saas-tooling-research-2026-09-08.md)。

## 1. 产品必须有自身成立的业务逻辑

FlowForge Cloud 面向中小企业技术团队，提供 API/Webhook 数据接入、同步任务观察、用量与套餐权益管理、企业成员协作。一个组织可拥有多个项目；项目下有集成及运行记录。技术人员排查失败，企业管理员维护成员，账单管理员查询账单和管理订阅。

EchoMind 是它的自然语言服务入口。用户也能通过普通产品页面查看和操作相同对象，因此可以对照“Agent 说已完成”和“产品实际状态”。这同时支持功能咨询、API 支持、订阅账单和企业账户四类业务。

模拟的是外部支付、邮件投递和真实同步基础设施；本地组织、订阅、邀请、操作记录要实际持久化并有校验规则。不要让 LLM 临时编出余额、权限、账单或“成功结果”。

## 2. 最小产品模块

| 模块 | 沙箱实际实现 | 提供给 Agent 的业务能力 | 首版不展开的部分 |
|---|---|---|---|
| 产品与权益 | 两个自助套餐、版本化功能矩阵、有效订阅的权益计算 | 功能咨询、当前套餐能否使用某能力 | 不做真实销售报价和合同审批 |
| API/同步观察 | 存储集成元数据、脱敏请求记录、运行结果、用量快照；可确定性注入故障 | 查 401/429 原因线索、API 接入指导、比对配额 | 不写通用 ETL 引擎，不访问用户给出的任意 URL |
| 订阅/账单 | 查询、下一周期套餐变更、账单明细；测试时钟推进生效 | 查询状态、预览影响、提交计划变更、核验回执 | 第一阶段没有立即升级按比例收费、退款和真实扣款 |
| 企业账户 | 组织、角色、成员列表、待接受邀请、模拟收件箱 | 查询成员、按允许角色邀请成员、解释权限 | 不重建 SSO/SCIM/企业身份平台 |
| 操作记录 | 幂等请求、变更预览、结果与业务审计 | 展示待确认/已安排/成功/失败，重复查询结果 | 不建事件溯源系统或通用工作流引擎 |

## 3. 合成套餐与业务约束

以下是产品 v1 的人工设计，统一单币种 CNY，金额用整数“分”，不计算税、折扣和退款。套餐权限按 version 定义，避免修改产品规则时悄悄改变历史账单。

| 属性 | Starter v1 | Growth v1 |
|---|---:|---:|
| 月价 | 99,000 分（990 元） | 299,000 分（2,990 元） |
| 企业席位上限 | 5 | 20 |
| 每账期 API 请求配额 | 1,000,000 | 10,000,000 |
| 每分钟请求限制 | 60 | 300 |
| 基础 Webhook/API 集成 | 支持 | 支持 |
| 请求记录保留期 | 7 天 | 30 天 |

套餐能力是服务规则，不可让 Agent 通过“改用户画像”赋予。API 429 同时可能来自分钟级限流或账期配额耗尽，错误记录必须有独立 `reason_code`，不能只看到 429 就推荐付费升级。待下周期升级不会解除当前配额限制，Agent 必须解释生效时间。

首版订阅修改仅支持 `effective_at=period_end`。预览返回当前与目标套餐、下周期价格、权益差异、当前期费用增量 0、生效时间、规则版本及前置条件。预约提交只改变 `scheduled_plan_id`，到期推进才更新 `plan_id`。降级时席位占用超新上限则拒绝；到期再检查占用，若不满足保持原方案并标记变更阻塞，不静默删除成员。取消自动续订、撤销预约等属于后续注册能力，沿用相同预览/执行/回执契约，不暗示 MVP 已覆盖所有订阅生命周期。

扩展实验才支持立即升级：固定报价时刻与账期边界，计算 `round_half_up((新月价-旧月价) × 剩余秒数/账期秒数)`；报价和提交使用相同规则与时刻，仅适用于本沙箱简化规则。金额正负、未付账单、失败回滚必须测试。Stripe 的 preview/proration 思路是参考，不声称税务或计费行为与 Stripe 完全一致。[Stripe 一手资料见 S1–S3](./saas-tooling-research-2026-09-08.md)

## 4. 业务状态库

推荐新增独立 SQLite 文件 `data/flowforge/flowforge.db`，复用 FastAPI 进程；它与 Chroma 自己的 sqlite 文件完全分开。数据库访问置于短事务内并正确处理锁/忙错误，不跨 LLM 请求保持事务。单机演示以有限并发为目标，不能由此宣称支持生产级多实例吞吐。

最小表及关键字段：

| 表 | 关键字段 | 约束 |
|---|---|---|
| organizations | id, name, status | 组织隔离根 |
| users / memberships | user_id, org_id, role, status | membership 唯一 (org_id,user_id)，角色按组织定义 |
| plans | id, version, price_minor, currency, entitlements_json | 被引用版本不原地覆盖 |
| subscriptions | id, org_id, plan_id, period_start/end, scheduled_plan_id, version | 首版每组织一个当前订阅，变更带版本 |
| invoices | id, org_id, subscription_id, period, lines_json, total_minor, status | 账单行加总等于总额；常规周期账单唯一 (subscription_id,period_start)，重复推进时钟不重复开账单 |
| projects / integrations | id, org_id, project_id, type, environment, status | 外键及 org 一致性；测试 key 仅存标识/末位信息 |
| request_logs / usage_snapshots | org_id, integration_id, request_id, ts, status_code, reason_code / metric, period, value | 按组织、项目、时间可查；日志内容脱敏 |
| invitations | id, org_id, email_normalized, role, status, expires_at | pending 唯一 (org_id,email)，重复邀请不多占席位 |
| operations | id, org_id, actor_id, tool, args_hash, target_version, status, expires_at, idempotency_key, receipt_json | 同一租户和操作幂等键唯一；参数变化拒绝复用 |
| audit_events | id, org_id, actor_id, operation_id, resource_id, before/after_version, result | 业务变更与成功审计同事务写入 |

预览与执行状态可共用 operations 表，首版不需要 quote、workflow、event 三套系统。JSON 只用于受控配置和回执，重要检索字段保持列。所有资源 ID 的读取均同时限定 org_id，不能先按 ID 查对象再相信模型提供的租户。邀请的席位检查、pending 插入和审计需在同一事务中完成，避免并发两人各看到一个空位而超卖席位。

### 4.1 状态迁移

- Subscription：`active + scheduled_change` → 测试时钟到期、条件满足 → `active(new_plan)`；条件不满足 → 原套餐续期、变更 `blocked`。测试支付可模拟成功或失败，失败需在 invoice 中明确，不能由语言回复改成 paid。
- Invitation：`pending` → 模拟接受 → `accepted` 并创建 membership；或到期 → `expired`。pending 占用预留席位；接受时释放预留并增加一席，不能重复计数。模拟发送只是本地收件箱记录，不标记真实邮件送达。
- Operation：`prepared` → `confirmed` → 短事务提交 → `succeeded/failed`；过期则 `expired`。未来接外部系统才扩 `pending/unknown/reconciling`；请求超时后先查操作回执，不盲目再产生新操作。
- 当前阶段不支持任意回滚承诺；已成功变更的逆操作也需要新的业务校验。取消尚未执行的预览无需变更业务对象。

## 5. 身份与权限的最小实现

演示账号通过 demo-only 登录入口生成随机服务端会话，服务端从 membership 加载当前 org 和 role。浏览器可选择有成员身份的组织；请求 body 的 user_id、role 和模型 tool 参数都不作为授权依据。演示账号切换要明显标识，只在本地 demo 模式启用，不能部署成开放的“任选管理员”。暂不集成 Keycloak；借鉴其组织与成员概念即可。[身份来源 S5](./saas-tooling-research-2026-09-08.md)

| 操作 | owner | admin | billing_admin | developer |
|---|---|---|---|---|
| 产品公共文档 | 是 | 是 | 是 | 是 |
| 组织权益和用量摘要 | 是 | 是 | 是 | 是 |
| 集成脱敏请求详情 | 是 | 是 | 否 | 是 |
| 查看账单/订阅价格 | 是 | 否 | 是 | 否 |
| 预览和提交套餐变更 | 是 | 否 | 是 | 否 |
| 邀请 developer | 是 | 是 | 否 | 否 |
| 查看成员列表 | 是 | 是 | 是 | 是（不含敏感资料） |

首版邀请仅开放 developer 角色，降低角色提升复杂度；扩展才做 admin/billing_admin 变更与最后 owner 保护。支持人员使用显式 support assignment，首版只有获授权组织的脱敏诊断只读能力，不默认继承企业 owner。

工具可见性按权限裁剪只改善使用体验；执行入口必须重新验权。费用变更需展示具体预览并确认；仅做报价/查账直接执行。邀请在收件人和角色已明确且用户已明确要求时可按产品策略执行；若由模型补全了对象或角色，则先确认具体变更。不存在对所有工具一律增加人工审批的要求。

## 6. 产品 API 与业务工具一一对照

以下是目标接口，尚未实现。API 与 Agent adapter 共用同一 service 和授权校验，同进程不必绕一圈 HTTP。API 是供 UI、测试和未来独立部署使用的契约。

| API | 业务工具 | 读写/阶段 | 成功判断 |
|---|---|---|---|
| GET /saas/plans | get_plan_catalog | 只读，MVP | 返回版本化套餐，不带其他租户信息 |
| GET /saas/me/entitlements | get_entitlements | 只读，MVP | 有效套餐和实际限额 |
| GET /saas/integrations/{id}/requests | get_integration_requests | 只读，MVP | org 绑定、时间范围、脱敏 |
| GET /saas/me/usage | get_usage | 只读，MVP | metric+period+limit+used |
| GET /saas/me/subscription | get_subscription | 只读，MVP | 当前和预约方案分开 |
| GET /saas/me/invoices | list_invoices | 只读，MVP | 明细与状态真实可查 |
| GET /saas/me/members | list_members | 只读，MVP | 组织内成员及邀请摘要 |
| POST /saas/subscription/change-previews | preview_subscription_change | 仅写操作草稿，MVP | 服务端计算具体差异，业务订阅不变 |
| POST /saas/subscription/changes | apply_subscription_change | 业务写，MVP | 预约状态、版本、receipt 可查 |
| POST /saas/invitations | invite_member | 业务写，MVP | 一条 pending invitation 与本地收件箱记录 |
| GET /saas/operations/{id} | get_operation | 只读，MVP | 返回真实执行结果且 org 匹配 |
| POST /saas/integrations/{id}/test-replays | replay_test_request | 沙箱写，扩展 | 新测试运行记录；不触发真实 Webhook |

另有基础设施确认接口 `POST /operations/{id}/confirm`，由已登录 UI 或明确绑定操作的对话确认完成服务端授权记录。它不是可由 LLM 任意填 `confirmed=true` 的业务工具。

`/demo/reset`、`/demo/advance-clock`、`/demo/faults`、`/demo/invitations/{id}/accept` 属测试控制面，仅 demo 管理/评测身份可访问，**不注册到 Agent 可用工具列表**。否则 Agent 可以修改环境让自己通过测评。

### 6.1 工具结果示例

```json
{
  "status": "requires_confirmation",
  "operation_id": "op_plan_001",
  "resource_id": "sub_aurora",
  "resource_version": 3,
  "preview": {
    "current_plan": "starter_v1",
    "target_plan": "growth_v1",
    "effective_at": "2026-10-01T00:00:00Z",
    "current_period_charge_minor": 0,
    "next_period_price_minor": 299000,
    "currency": "CNY"
  },
  "expires_at": "2026-09-08T02:15:00Z"
}
```

这是 schema 示例，不是已产生的回执。提交时只引用 operation_id 与受信任执行上下文，不能再次接受模型提供的金额、目标套餐或“用户已确认”布尔值。状态变更、审计和幂等结果同事务提交；同 key 同 payload 返回原回执，同 key 不同 payload 返回冲突。成功回复须区分“已安排下周期升级”与“当前已升级”。

## 7. 合成数据与文档统一生成

使用版本化 `scenarios/*.json` 作为事实输入，以固定 seed 和业务时钟生成数据库与可导入文档。LLM 可改写产品说明与用户问法，不能决定余额、权限、账单总额和期望最终状态。生成器先校验状态约束，再输出 `dataset_version / policy_version / seed / clock` 清单。

| 客户组织 | 场景状态 | 主要检验 |
|---|---|---|
| Aurora / org_aurora | Starter、4 活跃成员、无 pending、月配额 80%、401 auth_environment_mismatch、订阅 active | API 对接排查、第五席邀请、预约升级 |
| Beacon / org_beacon | Growth、19 活跃+1 pending、月配额 25%、429 rate_limit、存在 open 历史账单 | 容量已满拒绝、限流不是月配额、账单解释 |
| Cedar / org_cedar | Starter、5 活跃、月配额 100%、429 quota_exhausted、近期待到期 | 下一周期变更不能解除当前阻塞、跨组织同名资源 |

起始时间固定 `2026-09-08T02:00:00Z`，账期 `[2026-09-01T00:00:00Z,2026-10-01T00:00:00Z)`；推进由测试时钟控制。所有邮箱使用 `.example`，所有 key 使用无效 fixture 标识，所有回调目标为本地 stub。

首轮目标约 25–35 篇产品材料，而非凭空增加几千条问答：

- 功能与套餐 5–6 篇：计划、限额、保留期、升级生效规则、邀请占席规则。
- API/故障 10–12 篇：认证、环境、分页、幂等、401/403/409/429、Webhook 契约。
- 订阅账单 5–7 篇：账期、账单状态、预约变更、模拟支付限制。
- 企业账户 5–6 篇：组织、成员角色、邀请生命周期、支持人员访问边界。

另留两个过期版本文档检验过滤。每篇包含 source_id、doc_version、valid_from/to、visibility、可选 org/project、对应接口/规则版本；OpenAPI 示例必须与实际 service 返回一致。产品当前状态通过工具查询；导出的业务快照仅作为有截至时间的历史证据，不伪装成实时数据。

生成后必查：组织外键一致、当前订阅唯一、账单行加总、用量单位一致、429 原因可解释、pending 与 active 席位计数一致、权限表与 API 一致、文档案例能在种子数据上重放。

## 8. 与 Agent 五个模块的支撑关系

| Agent 能力 | 产品提供的真实支撑 | 观察/验收 |
|---|---|---|
| 意图识别 | 相同关键词下“问价格/看我的账单/改套餐”三种任务 | domain 与 action 分离，不能把咨询直接转执行 |
| 多 Agent | Support 的故障事实、Billing 的订阅报价、Account 的席位条件 | 读可并行；依赖变更必须串行；无收益不强制多 Agent |
| RAG | API/套餐/账户规则版本与原文 | 规则引用正确，实时状态由工具核验 |
| 分级记忆 | 同一用户多个组织、明确修正、变更前后状态 | 切组织隔离，旧套餐记忆不能覆盖执行后事实 |
| 测评 | 固定种子、可推进时钟、数据库最终状态、操作回执 | 不依赖 Judge 猜“是否真的邀请成功/改了订阅” |

## 9. 产品 UI 最小范围

复用现有 Vue，增加“概览/集成记录/订阅账单/组织成员”四个简单面板，聊天作为侧栏或主工作区。订阅变化卡显示金额、生效时间和确认；成员邀请显示 pending 状态与模拟收件箱；来源抽屉显示文档/工具证据和截至时间。演示账号切换、重置、测试时钟放独立 demo 控制区，普通业务用户不见测试控制接口。

Agent 操作结束后 UI 从业务 API 重新拉取状态，不只把模型文本复制到产品卡片。无需另做一套落地页、营销系统、复杂图表或完整登录注册。

## 10. 首版验收场景

1. developer 问“Starter 支持 Webhook 吗”：依据文档和产品表回答，不变更订阅。
2. Aurora developer 问“这条 401 怎么修”：查本组织记录，确认测试 key/生产环境不匹配，不泄露完整凭证。
3. billing_admin 查账并预约 Growth：预览→确认→唯一 operation→scheduled_plan 更新，当前权益仍 Starter。
4. 重复提交同一 operation：没有第二次业务变更，回执一致；模拟超时后通过 get_operation 找到结果。
5. owner 邀请第五名 developer：一个 pending，占满第五席；重复邀请不增加席位；第六人被阻止。
6. developer 要求改套餐：明确缺权限，数据库无变化，不能靠说“我是老板”越权。
7. 换到 Cedar 查询同名项目：返回 Cedar 数据，不混用 Aurora 的历史与缓存。
8. 测试时钟推进账期：计划变更真实生效；再次询问套餐时取新状态；过期 preview 拒绝提交。

每个场景必须同时验证回复事实、API/工具调用边界与数据库前后状态。支付、邮件和同步为模拟，论文/简历应明确这一范围。
