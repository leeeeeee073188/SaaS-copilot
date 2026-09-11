# 企业服务工作台

Vue 3 + Vite，为 FlowForge 企业用户提供组织身份切换、产品咨询、集成诊断、订阅账单、成员管理及服务记录。

运行 `npm ci`、`npm run dev`，默认 http://127.0.0.1:5173。
`/api/python` 转发至本地 8000 端口。`npm run build` 生成静态站点。
生产部署使用根目录的 Docker Compose，Nginx 转发到 `echomind:8000`。
