# 本地验收结果

日期：2026-10-31。Python：3.12.16，环境为用户创建的 `skippy`。

- `python -m pytest -q`：23 passed。依赖 Starlette/httpx 有一条弃用提示，不影响测试结果。
- `npm run build`：TypeScript 检查与 Vite 生产构建通过。Three.js 所在主包约 636 kB、gzip 约 171 kB，Vite 给出常规分包体积提示。
- `python -m skippy.server.smoke`：实际 Vite `/api` 代理、HTTP 求解和 WebSocket 求解均通过。
- 浏览器：固定球体案例、前后候选切换、旋转检查、双长方体桥接、重新求解、真实拖动新建第二条曲线均成功。
- 三维曲线截图：`docs/skippy-preview.png`。
- 基准原始数据：`backend/benchmark.json`。共七个案例，每个五次热缓存测量；10 球体 / 200 点案例首次约 152 ms，热缓存 P95 约 41 ms。复杂双盒桥接首次约 311 ms，热缓存 P95 约 19 ms。
- 数值与原版差异：详见 `docs/reproduction.md`。特别记录解析距离查询、投影式修正、约束平滑、高度带和有限桥接窗口；不声称与作者原程序逐点数值相同。

本次只交付本地项目；线上 Sites 发布及 Python API 托管未执行。

## 后端目录拆分（2026-11-01）

- `python -m pytest -q`：29 passed，保留原依赖的一条 Starlette/httpx 弃用提示。
- 原有 23 项回归检查全部通过；另新增 6 项边界检查，覆盖独立离线重放成功/失败、示例接口兼容、求解错误回传、局部重画成功/失败。
- 独立 Python 子进程可重放带前端元数据的输入，确认不加载 FastAPI 或 `skippy.server`；输入文件保持原样，算法输入及结果不携带请求编号和场景版本。
- HTTP / WebSocket 使用 FastAPI TestClient 验证，保留接口地址、输入校验和请求版本回传。
- 修复新增局部重画案例暴露的边界：深度桥接同时裁剪两端时跳过空候选段，避免索引错误。
- 后端启动模块改为 `skippy.server.api:app`，离线入口 `python -m skippy` 保留。算法基准、数值诊断和服务检查分别位于 `skippy.algorithm.benchmark`、`skippy.algorithm.diagnose`、`skippy.server.smoke`。
- 重构前的源码、测试和文档备份在 `.skippy-backup/backend-layout-20261101`。
