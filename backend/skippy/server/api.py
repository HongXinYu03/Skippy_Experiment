"""HTTP solver, diagnostic examples, and coalesced WebSocket previews."""
import asyncio
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from .schemas import SolveRequest, RedrawRequest
from . import service

app = FastAPI(title="Skippy Python API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("SKIPPY_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","), allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

@app.get("/api/health")
def health():
    import sys
    return {"status": "ok", "python": sys.version.split()[0], "implementation": "python", "version": "0.1.0"}

@app.get("/api/examples/{kind}")
def get_example(kind: str, width: int = 1000, height: int = 700):
    allowed = {"sphere", "box", "tetrahedron", "two-spheres", "loop", "discontinuity", "stress"}
    if kind not in allowed or not 100 <= width <= 16384 or not 100 <= height <= 16384:
        from fastapi import HTTPException
        raise HTTPException(422, "Invalid example or viewport size")
    return service.example_request(kind, width, height)

@app.post("/api/solve")
def solve_curve(request: SolveRequest):
    return service.solve_curve(request)

@app.post("/api/redraw")
def redraw_curve(request: RedrawRequest):
    return service.redraw_curve(request)

@app.websocket("/api/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()
    pending = asyncio.Queue(maxsize=1)
    async def calculate():
        while True:
            request = await pending.get()
            result = await asyncio.to_thread(service.solve_curve, request)
            if pending.empty():
                await websocket.send_json(result)
    current = asyncio.create_task(calculate())
    try:
        while True:
            payload = await websocket.receive_json()
            try:
                request = SolveRequest.model_validate(payload)
            except ValueError:
                await websocket.send_json({"status": "invalid_input", "message": "输入格式无效。"})
                continue
            # At most one active solver and the newest pending prefix per connection.
            if pending.full():
                pending.get_nowait()
            pending.put_nowait(request)
    except WebSocketDisconnect:
        pass
    finally:
        current.cancel()
