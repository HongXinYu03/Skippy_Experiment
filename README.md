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
cd "C:\Workspace\Git program\Skippy\backend"
python -m pip install -r requirements.txt
python -m uvicorn skippy.server.api:app --host 127.0.0.1 --port 8000 --reload
```

终端二：

```powershell
cd "C:\Workspace\Git program\Skippy\frontend"
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

## 测试与离线复现

在 `backend` 目录中运行：

```powershell
python -m pytest -q
python -m skippy --example sphere --output result.json --export-input input.json
python -m skippy input.json --output replay.json
python -m skippy.algorithm.benchmark
```

固定案例包含球体、长方体、四面体棱边、双球体、闭合曲线、深度不连续桥接；基准另含 10 球体、200 个采样点。基准写入 `backend/benchmark.json`。当前安装版本另保存于 `backend/requirements.lock.txt`，可通过 `python -m pip install -r requirements.lock.txt` 重建相同依赖版本。

前端检查：在 `frontend` 目录运行 `npm run build`，包含 TypeScript 检查和生产构建。保留现有 Vue 脚手架的版本与构建方式，新增 Three.js。

## 代码位置

- `backend/skippy/algorithm/geometry.py`：几何体、距离、梯度、射线与偏移等值面交点。
- `backend/skippy/algorithm/stroke.py`：二维重采样、on/off、深度不连续、height 和候选。
- `backend/skippy/algorithm/graph.py`：on 段、off 插值、桥接、曲率变化和 DAG 最优路径。
- `backend/skippy/algorithm/smoothing.py`：三维重采样、active-contour 能量、解析梯度、受约束下降与 Catmull–Rom。
- `backend/skippy/algorithm/pipeline.py`：缓存、skip、局部重画、诊断与质量检查。
- `backend/skippy/algorithm/models.py`：独立算法输入 `SolveInput` / `RedrawInput`、几何及参数校验。
- `backend/skippy/algorithm/cli.py`：离线求解；同目录的 `fixtures.py`、`benchmark.py`、`diagnose.py` 用于案例、性能和数值诊断。

- `backend/skippy/server/api.py`：FastAPI 路由、CORS、WebSocket 待处理队列。
- `backend/skippy/server/schemas.py`：前端请求结构，包含 `request_id` 和 `revision`。
- `backend/skippy/server/service.py`：请求转换、算法调用、错误转换和响应元数据回填。
- `backend/skippy/server/smoke.py`：运行中服务的 HTTP / WebSocket 检查。

- `frontend/src/components/Viewport.vue`：三维视口、二维叠加层、相机与物体操作。
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

