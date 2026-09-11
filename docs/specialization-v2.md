# V2 专业化分工

面向使用 FlowForge 的企业用户，沿用主辅路由与只读并行，不引入新框架或新的模型实例类型。

| Agent | 子任务 | 专属业务工具 |
|---|---|---|
| Triage | 产品功能、配置说明、适用边界 | 公共只读工具 |
| Support | API/集成排查，区分认证、分钟限流、账期配额 | get_usage、list_integrations、get_integration_requests |
| Success（billing） | 订阅、账单、套餐变更与生效时间 | get_subscription、list_invoices、preview_subscription_change、apply_subscription_change |
| Success（account） | 成员、席位、角色与邀请 | list_members、invite_member |

Success 同时收到 billing/account 只读请求时一次处理两个子任务，不为每个领域新增类。
原有 Delivery/Renewal/Escalation 及内部分析模式已于 2026-09-11 删除，运行时只保留以上三类 Agent。

公共工具为 search_product_knowledge、get_plan_catalog、get_entitlements、get_operation。
最终工具是“用户权限及请求动作允许的工具”与“角色任务范围”的交集；角色配置不授予权限。
角色外工具即使被模型请求也不会执行。change/preview 只分配主领域，直接调用并行入口也会拒绝写请求。

## 执行顺序

会话记忆读取 → 意图识别 → 初始 RAG → 主辅路由 → 各专业领域并行核验 → 结构化结果校验 → 用户答复汇总 → 记忆写回。

例如“API 429 是不是套餐不够，升级后能解决吗”：

- Support 读取脱敏请求和用量，判断错误子类型；不读取私有账单或操作订阅。
- Success 读取订阅及套餐规则，核验升级时间；不读取集成请求日志。
- Composer 连接两类结论，说明下周期升级是否能解决当下问题；事实不足或冲突时保留待核实项。

每个调用复制任务对象，独立累积证据和操作回执，完成、失败或取消时归并回父请求。
同一 Agent 实例串行执行，防止原有实例级工具轨迹字段串扰；不同角色仍可并行。
监控阶段含 specialist_support、specialist_success 等固定角色名称，不记录完整提示词。

## 输入与输出契约

领域证据按现有 `ff-领域-编号` source_id 筛选，并保留产品通用规则及组织历史快照。
工具补充检索也应用同样筛选；当前是在召回后过滤，可能少于 Top-K，尚非按领域预过滤召回。
会话记忆仍共享以保持指代与用户偏好，不宣称有 Agent 独立长期记忆。
历史快照不作为当前订阅、用量等实时事实；仍须调用业务工具核验。

单领域请求直接生成用户答复；并行子任务只输出以下 JSON：

```json
{
  "summary": "本领域结论",
  "evidence_ids": ["get_subscription"],
  "missing": ["尚需确认错误子类型"],
  "next_steps": ["结合请求记录确认当前限制"]
}
```

程序校验字段集合、类型、非空结论及引用是否在该子任务证据中。
这保证格式和来源可追溯，不等于证明模型结论受证据蕴含；语义正确性仍需真实模型评测。
非法结果视作该领域失败；不自动重试或扩权回退。
Composer 接收结构化结果，面向企业用户答复；部分失败追加明确提示，汇总失败则确定性展示已有结果。
最终自然语言汇总的引用和语义仍依赖模型遵循提示，未实现逐句归因校验。

## 验证

运行 `.venv/Scripts/python.exe -m unittest discover -s tests -v`。
`test_specialization.py` 使用 fake provider 验证路由后的真实工具循环、工具隔离、引用校验与降级。
原有业务 Agent 测试继续验证预览回执归并和当期订阅不变。
离线 60 个参数化业务状态用例用于回归，不经过真实模型，不能作为多 Agent 效果提升指标。
