# 独立分支清理（2026-09-11）

按用户要求，`feat/b2b-saas-agent-v2` 后续仅开发面向 SaaS 企业用户的当前产品。
旧模式可从 Git 历史 `df3cbd1` 找回，不再保留运行时切换入口。

## 清理范围

- 删除内部分析 API、CLI、Delivery/Renewal/Escalation、旧意图识别与性能路由。
- 删除旧记忆管理、MCP RAG、Skills 加载、原监控评测及仅针对它们的测试。
- 删除旧前端分析页面、知识上传入口、多后端适配及 Java 代理。
- 删除旧 wiki、架构图、简历材料和重复部署脚本，重写当前 README 与前端说明。
- Docker 默认仅启动后端和前端，使用本地持久 Chroma，不再启动未被当前链路使用的 Chroma 服务。
- 旧数据库从 Git 索引移除，但保留本地文件；未清空当前 FlowForge 业务、记忆和 Chroma 数据。

## 保留与兼容

三个业务 Agent 的工具循环、授权、结构化汇总、记忆、检索、监控及业务状态测评保留。
`api.main:app` 启动路径不变，始终创建当前工作台。`SAAS_COPILOT_DEMO_LLM` 仍选择离线或模型执行，
其他 `SAAS_COPILOT_DEMO_*` 配置名称保留，`SAAS_COPILOT_DEMO` 不再选择旧模式。
演示身份和模拟产品仍非生产认证、真实支付或邮件实现。

早期研究、ADR 和验收记录作为历史保留；当前功能以 README、当前实现说明与代码为准。

## 验证结果

- 27 项当前链路测试通过；原 47 项中删除 21 项旧模式测试，新增 1 项实际启动入口测试。
- 60/60 离线业务状态用例通过，验证真实 SQLite/Chroma、权限、回执和状态变更。
- `npm run build` 通过；`docker compose config --quiet` 通过，未执行 Docker 镜像构建或容器部署。
- 启动入口测试验证 `/ready`、登录、聊天、监控、组织隔离检索，以及旧接口返回 404。
- 专业化编排测试继续通过；没有调用真实模型，不能据此声称模型效果提升。

本地报告：`data/flowforge/standalone-tests.log`、`data/eval/standalone-business-report.json`。
报告 base_revision 为清理前提交，验证发生在未提交工作区。
