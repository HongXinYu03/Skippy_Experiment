# 本地验收结果

以下按日期记录；当前算法的最新验证见末尾“软高度平滑”。

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

## 软高度平滑（2026-11-02）

- `python -m pytest -q`：36 passed，保留原依赖的一条 Starlette/httpx 弃用提示。
- 移除平滑阶段的高度带、深度区间缓存和高度 `np.clip` 硬裁剪；高度误差仍进入能量和梯度。候选点生成与首尾 off 外推代码保持原样。
- 普通开放曲线的首尾点参与优化；局部重画通过显式 `fixed_endpoints` 参数保留原连接端点。现有跨视角重画、HTTP 重画和区间外坐标保持检查通过。
- 新增 7 项回归：三种 U 形笔触分别在 0 / 80 次迭代下检查控制折线与最终样条不出现首尾折返；零次迭代无高度跳变，正常优化允许普通端点移动；改变目标高度会改变能量和梯度。
- 同类 U 形诊断案例的首尾控制折线转角由旧硬投影后的约 149° 降至约 1.8°。这不是用户截图对应输入的逐点重放。
- 原有“高度 P95 < 0.01”断言依赖旧硬约束，已移除；保留投影误差、无穿入、闭合及能量下降检查，并新增高度软能量有效性测试。
- 七个基准案例全部求解成功：投影 P95 最大约 0.1204 px，高度 P95 最大约 0.1255 归一化单位。高度已成为软目标，因此实际误差大于旧硬高度带版本。`backend/benchmark.json` 已更新。
- 本次通过几何及接口自动化验证，未重新进行浏览器手动验收。创作射线投影和数值穿入检查仍保留，具体工程差异见 `reproduction.md`。
- 更新前文件和基准报告备份在 `.skippy-backup/soft-height-20261102`。已有曲线需要重新求解后才能显示新结果。
