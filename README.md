# Skippy Studio


## 本地运行

如果本地没有创建conda环境需要创建python虚拟环境

```powershell
conda create -n skippy python=3.12
conda activate skippy
``` 

终端一：

```powershell
conda activate skippy
cd "D:\Git_Project\Skippy_Experiment\backend"
python -m pip install -r requirements.txt
python -m uvicorn skippy.server.api:app --host 127.0.0.1 --port 8000 --reload
```

终端二：

```powershell
cd "D:\Git_Project\Skippy_Experiment\frontend"
npm install
npm run dev
```

浏览器打开 `http://127.0.0.1:5173/`。API 文档为 `http://127.0.0.1:8000/docs`。前端开发服务将 `/api` 代理到本地 Python 服务。已安装好依赖时，之后启动只需各终端最后一条运行命令。无需再创建其他 Python 虚拟环境。


## 使用

1. 在下方“固定案例”选择单球体，点击“载入并求解”。
2. 在右侧选择不同深度候选，再切到“观察”拖动旋转，检查曲线实际位于物体前方或后方。
3. 切回“绘制”，在几何体投影内外画一笔。松笔后得到完整曲线；绘制中可进行限频预览。
4. 添加球体、长方体或四面体。用“编辑物体”的控制轴移动、旋转、缩放，或修改左侧数值。场景变化后，旧曲线保留结果，使用“基于当前场景重新求解”刷新。
5. “引导物”模式点击表面放置临时引导球；在左侧移除后，已经生成的曲线仍保留。
6. “局部重画”中选择起止进度，再从当前视角绘制替换段；后端固定两端点，验证连接和穿入情况，区间外曲线保持原坐标。
7. 保存场景 JSON 以恢复对象、曲线和相机；导出输入可离线重放，导出诊断包含候选、段图、误差与迭代记录。

参数变化后请重新求解，再选择该次结果中的深度候选。全 off、空场景、不可行锁定或超过图规模预算时会明确返回状态。

平滑阶段通过高度误差能量保持离物距离，不再使用高度带硬裁剪。普通绘制的首尾点可随优化调整；局部重画的两个连接端点保持固定。`iterations=0` 仍进行三维重采样，但不会强制调整高度。软约束允许高度与平滑目标权衡，诊断面板显示实际高度误差。更新后需点击“重新求解”或重新绘制，已保存的曲线坐标不会自动更新。

## 导入 PLY / GLB 模型

1. 点击左侧“导入 PLY / GLB 模型”，选择本地文件。默认替换当前场景；取消勾选“替换当前场景”可将模型加入现有场景。导入操作可撤销。
2. 模型自动居中、等比缩放到最长边为 3 的工作尺寸，并适配视角。进入“绘制”后直接画一笔，松开生成三维曲线；笔触需要经过至少一部分模型投影。
3. 原有的深度候选切换、引导物、局部重画、观察和物体变换继续可用。模型支持三轴缩放；移动、旋转或缩放后，已有曲线可“基于当前场景重新求解”。“适配”按钮可重新将整个场景放入视野。
4. 顶部“保存”将网格、曲线和相机一起保存为 JSON。“载入场景”可以恢复，无需再次选择原始模型文件；旧版基础几何场景仍兼容。

格式范围：支持 ASCII / 二进制 PLY 的面网格，以及 GLB 中多个网格、节点层级、实例和静态姿态；保留顶点颜色和基础材质颜色。GLB 纹理、动画、Draco / Meshopt / KTX2 压缩扩展暂不保留或解码，请导出不含压缩扩展、资源内嵌的 GLB。纯点云 PLY 需要先重建为三角网格。

单个模型最大 64 MiB、10 万个顶点及 10 万个三角面；场景文件最大 128 MiB，最多 32 个物体。复杂网格求解会比基础几何慢，可关闭绘制中的预览、降低平滑迭代次数或先简化模型。封闭流形部件作为实体参与内外与穿入检查，多部件按并集处理；开放或非流形部分按双面表面处理，不定义实体内部。自交网格和用独立嵌套壳体表示的空腔请先修复为合适的实体网格。

网格仍进入原有 Skippy 推断、深度候选和软高度平滑流程。几何查询使用三角网格 BVH、最近点、射线交点及三角面的欧氏偏移，未用包围盒代替模型表面，也未修改现有平滑能量。

## 后端控制台进度

HTTP 求解、局部重画和 WebSocket 预览默认使用 `tqdm` 显示原位更新的进度栏。首次更新依赖时，在 `skippy` 环境执行 `python -m pip install -r requirements.txt`，之后按原命令启动后端即可；已经使用 `--reload` 启动时，代码保存后会自动重载：

```powershell
python -m uvicorn skippy.server.api:app --host 127.0.0.1 --port 8000 --reload
```

在前端绘制一笔或重新求解，运行后端的终端会在同一行显示请求编号、当前阶段、进度条、实际完成数 / 总数、阶段百分比和耗时。进入下一个阶段时复用这一行，不为开始、中间状态或嵌套阶段新增记录；整个请求结束后，只保留一条完成结果及总耗时，成功时为 100%，失败时保留失败步骤与实际进度。阶段包括：输入转换与缓存、场景准备与网格 BVH、二维重采样、射线 on/off 分类、深度不连续检测、off 距离与 on 高度、候选顶点、穿入细化、候选段与段图、DAG 最短路径、三维重采样、能量初始化、平滑迭代和回溯、最终碰撞验证、误差与诊断整理。局部重画还显示端点准备和曲线拼接验证。

长步骤按实际处理的射线、采样点、三角面或节点更新，默认最多每 0.5 秒原位刷新一次中间状态。总工作量未知的路径提取显示 `--%` 和已探索 / 待探索数量，不推算百分比；快速步骤可能直接完成。平滑提前停止时保留有效迭代数和停止原因，回溯次数、能量等附加信息在终端宽度允许时显示。最终摘要注明缓存命中或有效平滑迭代数。进度栏中的百分比表示当前阶段的进度，进入下一阶段会重新开始，不代表对整个请求耗时的估算。

所有并发请求共用一条活动进度栏，优先显示最新请求；旧请求完成时只追加一条最终结果。Uvicorn 控制台日志通过 `tqdm` 协调输出，不会把进度条挤成多行；长中文标题会按终端宽度截断以避免自动换行。输出到文件、管道或非交互式窗口时，只写每个请求的最终摘要，不写中间刷新内容。前端“预览”开启时，每次前缀求解仍有独立请求编号；取消勾选“预览”可只观察松笔后的完整求解。`python -m skippy ...` 离线命令也显示进度，标准输出仍保持原有 JSON 摘要。直接通过 Python 导入调用算法时默认静默，可用 `skippy.algorithm.progress.console_progress()` 包裹调用以显示进度。

可选环境变量：`SKIPPY_PROGRESS=0` 关闭进度；`SKIPPY_PROGRESS_INTERVAL=0.2` 将中间刷新间隔设为 0.2 秒；`SKIPPY_PROGRESS_MODE=auto` 自动识别终端（默认），`inline` 强制原位刷新，`plain` 仅打印最终摘要。设置后重新启动后端。在 CMD 中例如使用 `set SKIPPY_PROGRESS_MODE=inline`；PowerShell 中使用 `$env:SKIPPY_PROGRESS_MODE="inline"`。如果 IDE 的输出窗口只显示摘要，可使用支持回车覆盖的集成终端，或将模式设为 `inline`。

## 测试与离线复现

在 `backend` 目录中运行：

```powershell
python -m pytest -q
python -m skippy --example sphere --output result.json --export-input input.json
python -m skippy input.json --output replay.json
python -m skippy.algorithm.benchmark
```

固定案例包含球体、长方体、四面体棱边、双球体、闭合曲线、深度不连续桥接；基准另含 10 球体、200 个采样点。基准写入 `backend/benchmark.json`。当前安装版本另保存于 `backend/requirements.lock.txt`，可通过 `python -m pip install -r requirements.lock.txt` 重建相同依赖版本。

前端检查：在 `frontend` 目录运行 `npm test` 检查模型解析与校验，运行 `npm run build` 进行 TypeScript 检查和生产构建。启动前后端并执行过 `npm test` 后，可运行 `npm run test:models-live` 验证实际 PLY / GLB 文件经前端解析、Vite 代理和 Python API 生成曲线。PowerShell 禁止执行 `npm.ps1` 时，可使用等价的 `npm.cmd` 命令。

## 代码位置

- `backend/skippy/algorithm/geometry.py`：几何体、距离、梯度、射线与偏移等值面交点。
- `backend/skippy/algorithm/mesh.py`：网格 BVH、实体部件并集、最近距离和多段偏移射线交点。
- `backend/skippy/algorithm/stroke.py`：二维重采样、on/off、深度不连续、height 和候选。
- `backend/skippy/algorithm/graph.py`：on 段、off 插值、桥接、曲率变化和 DAG 最优路径。
- `backend/skippy/algorithm/smoothing.py`：三维重采样、active-contour 能量、解析梯度、受约束下降与 Catmull–Rom。
- `backend/skippy/algorithm/pipeline.py`：缓存、skip、局部重画、诊断与质量检查。
- `backend/skippy/algorithm/progress.py`：按请求隔离的控制台进度条、节流、阶段耗时和失败报告。
- `backend/skippy/algorithm/terminal_progress.py`：tqdm 单行渲染、并发请求显示协调和 Uvicorn 日志兼容。
- `backend/skippy/algorithm/models.py`：独立算法输入 `SolveInput` / `RedrawInput`、几何及参数校验。
- `backend/skippy/algorithm/cli.py`：离线求解；同目录的 `fixtures.py`、`benchmark.py`、`diagnose.py` 用于案例、性能和数值诊断。

- `backend/skippy/server/api.py`：FastAPI 路由、CORS、WebSocket 待处理队列。
- `backend/skippy/server/schemas.py`：前端请求结构，包含 `request_id` 和 `revision`。
- `backend/skippy/server/service.py`：请求转换、算法调用、错误转换和响应元数据回填。
- `backend/skippy/server/smoke.py`：运行中服务的 HTTP / WebSocket 检查。

- `frontend/src/components/Viewport.vue`：三维视口、二维叠加层、相机与物体操作。
- `frontend/src/modelImport.ts`：PLY / GLB 解析、静态几何合并、变换烘焙、归一化及网格校验。
- `frontend/src/App.vue`：交互工作流、候选选择、撤销重做、JSON 保存、调试面板。
- `docs/reproduction.md`：论文章节对应、数值约定、工程差异和验收结果。


## 独立调试算法

调用方向为 `server → algorithm`。算法目录不导入 FastAPI 或 `server`，在 `backend` 目录可直接调用：

```python
from skippy.algorithm import SolveInput, solve
from skippy.algorithm.fixtures import example

data = example("sphere")  # 也可用 SolveInput.model_validate(payload)
result = solve(data)      # 算法失败时抛出 SolveError
print(result["metrics"])
```

`python -m skippy` 仍可离线重放前端导出的输入 JSON，会去掉 `request_id` / `revision`；这两个字段由服务层在 HTTP / WebSocket 响应中回传。接口 URL 和前端数据格式保持兼容。

数值诊断使用 `python -m skippy.algorithm.diagnose`。启动前后端后，可用 `python -m skippy.server.smoke` 检查联通性。目录调整后，正在运行的旧后端需停止并使用上方新命令重新启动。

