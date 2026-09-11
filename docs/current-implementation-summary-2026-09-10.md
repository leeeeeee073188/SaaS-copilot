# 当前项目实现说明

更新：2026-09-11。当前独立分支仅包含面向 SaaS 企业用户的产品支持链路，旧内部分析模式已移除。

## 1. 业务定位

EchoMind 为 FlowForge 模拟 API/Webhook 产品提供功能咨询、集成支持、订阅账单和企业账户管理。
三个演示组织、四类成员角色，实际修改本地业务状态；无真实扣款、邮件或外部 Webhook 执行。

## 2. 意图识别

`core/business_intent.py` 使用关键词、问句/否定与明确操作模式识别领域和动作。
领域为 product/integration/billing/account；动作为 read/preview/change。
指代问题可参考上一条用户消息。无 LLM 意图分类、向量模板融合或经校准的置信度。
识别结果只决定分工和候选工具，不授予业务权限。

## 3. 多 Agent 编排

`agents/agent_orchestrator.py` 仅包含 Triage、Support、Success 三类 Agent。
Triage 对应 product，Support 对应 integration，Success 对应 billing/account。
只读跨领域任务采用主辅路由与并行核验，再汇总；变更任务只交给主领域。
同一角色实例串行执行以隔离工具轨迹，不同角色可并行。
模型调用共享 Anthropic 协议，支持按角色配置模型、温度与输出长度。

`saas/specialization.py` 分配领域任务，裁剪工具和证据。
并行结果包含 summary/evidence_ids/missing/next_steps，验证结构和引用存在性；不等同语义正确性证明。
角色失败明确提示，不扩权回退；Composer 失败则保留已有领域结果。
旧 Delivery/Renewal/Escalation、Skills 加载与性能路由已删除。

## 4. 记忆

`saas/memory.py` 按组织、用户、会话隔离工作历史，默认 SQLite，可选 Redis TTL 24 小时。
历史超过阈值后先归档再裁剪；Chroma 保存有界历史片段与显式偏好。
历史片段不是模型摘要，画像也不是授权依据。请求读取记忆后识别意图，路由后再分配领域证据。
多个 Agent 共享对话背景以理解指代，不宣称每个 Agent 有独立长期记忆。

## 5. RAG

`saas/knowledge.py` 导入 30 条当前公共规则、3 条组织快照和 1 条过期负例。
检索先过滤组织、有效版本与 active，再获取 Top-K；各 Agent 对已有证据按领域进一步过滤。
默认 lexical 为 512 维字符 n-gram 哈希向量，另有可选 MiniLM 集合；未实现混合检索或 reranker。
实时订阅、账单、成员与用量必须通过业务工具核验，不能以知识文档或记忆代替。

## 6. 工具与操作

`saas/agent_tools.py` 注册业务工具；请求权限、动作和角色职责共同收窄可用范围。
`saas/service.py` 再次校验登录身份与权限，以事务写入业务状态、回执与审计。
套餐变更先预览、由 UI 确认后提交，下周期生效；邀请固定 developer 并保留 pending 状态。
确认和模拟时钟不注册为 Agent 工具。

## 7. 可观测性

`monitor/business_monitor.py` 持久化 request_id、阶段耗时、工具结果、模型用量与操作 ID。
包括各专业 Agent 的执行阶段；请求记录按组织及用户隔离。
区分业务拒绝、技术失败、超时和降级；指标不使用用户、组织或请求 ID 作为标签。
`/metrics` 需独立凭证；`/ready` 检查 SQLite、Chroma 和可选 Redis。
尚无分布式追踪、外部告警投递或 token 价格核算。

## 8. 测评

`evaluation/business_evaluator.py` 经由同一 ChatService 执行 5 模板 × 3 组织 × 4 角色共 60 个业务状态用例。
检查权限、隔离、回执、幂等、席位、金额和生效时间；离线执行不代表真实模型质量。
`tests/test_specialization.py` 使用 fake provider 和真实业务读工具验证专业化执行。
本分支不再提供旧 `/eval`、知识上传、Skills 或内部分析入口。

## 9. 运行入口

`api.main:app` 始终提供当前工作台，前端仅渲染企业服务页面。
`ECHOMIND_DEMO_LLM=0` 为确定性演示，设为 1 并配置模型后运行 Agent 编排。
默认本地数据位于 `data/flowforge`，不再启动或访问旧分析数据库。

详见 [启动指南](../README.md)、[产品说明](flowforge-product-introduction.md)、[清理记录](standalone-cleanup.md)。
