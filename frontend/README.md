# EchoMind SaaS Frontend

Vue/Vite 前端，用于连接 EchoMind Python/FastAPI 服务，面向 SaaS 企业内部的客户交付、技术支持、客户成功和续费运营分析。

## 功能

- 客户运营分析对话与多 Agent 路由信息展示
- 请求 ID、主/辅助 Agent、RAG 与工具调用摘要
- 最近请求工具轨迹查看
- 健康检查、监控摘要与知识库统计
- 知识库检索、文档导入和文件上传
- Docker + Nginx 部署

## 本地开发

先启动根目录的 EchoMind 后端，再执行：

```bash
npm ci
npm run dev
```

访问 `http://localhost:5173`。Vite 会将 `/api/python` 代理到 `http://localhost:8000`。

## Docker 全栈部署

在项目根目录执行：

```bash
docker compose up -d --build
```

访问：

- 前端：`http://localhost:5174`
- API：`http://localhost:8000`
- Swagger：`http://localhost:8000/docs`

前端容器通过 `/api/python` 访问当前 Python/FastAPI 分析后端。
