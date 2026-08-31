# EchoMind Skills 文档

EchoMind 启动时会从 `ECHOMIND_SKILLS_DIR` 读取 Skills，并在匹配用户请求时注入到对应 Agent 的 system prompt。Skills 用于维护 SaaS 客户交付、技术支持、客户成功和续费运营的分析规范。

当前内置五类 Skills：

```text
skills/triage/SKILL.md   # 客户运营问题分诊与结果汇总
skills/delivery/SKILL.md # 客户交付与实施分析
skills/support/SKILL.md  # 技术支持与可靠性分析
skills/success/SKILL.md  # 客户成功与功能采用分析
skills/renewal/SKILL.md  # 续费准备度与运营信号分析
```

## Skill 文件格式

推荐每个 Skill 使用独立目录，并将主文件命名为 `SKILL.md`：

```text
skills/<skill_name>/SKILL.md
```

文件顶部使用简单 front matter：

```markdown
---
name: 技术支持处理规范
description: 适用于 SupportAgent 的集成、故障和可靠性分析规范
keywords: 报错,错误,接口,API,部署,超时,500,401,日志
agents: support
enabled: true
---
```

字段说明：

- `name`：Skill 展示名称，会出现在注入给模型的 prompt 中。
- `description`：简短说明，方便 `/skills` 接口排查。
- `keywords`：触发关键词，用户消息命中后才注入；多个关键词用英文逗号或中文逗号分隔均可。
- `agents`：适用 Agent，可填 `triage`、`delivery`、`support`、`success`、`renewal`，多个值用逗号分隔。
- `enabled`：是否启用，支持 `true/false`。

## 编写要求

- 重要规则放在文档前半部分，因为过长内容会按 prompt 预算截断。
- 一类 Skill 只描述一类职责，不要把交付、支持、客户成功和续费规则混在一个文件里。
- 建议包含“角色定位”“处理流程”“风险边界”“禁止事项”等稳定章节。
- 对客户数据、API Key、Token、生产配置等敏感信息必须写明禁止收集或禁止公开。
- 对无法保证的事项使用保守措辞，例如“通常”“预计”“需要核验后确认”。
- 对需要补充数据、专家判断或人工决策的场景要明确写出升级条件。

## 热加载

修改 Skill 文件后，不需要重启服务，调用：

```bash
curl -X POST http://localhost:8000/skills/reload
```

查看加载结果和解析错误：

```bash
curl http://localhost:8000/skills
```
