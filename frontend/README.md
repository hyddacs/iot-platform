# Frontend

这是 `iot-platform` 的 Vue 前端目录，使用 `Vue 3 + Vite`。

## 开发

```bash
cd frontend
npm install
npm run dev
```

默认开发地址：

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8000`

`vite.config.js` 已代理 `/api` 到后端，因此前端里直接请求 `/api/...` 即可。
