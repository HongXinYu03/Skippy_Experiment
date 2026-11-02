"""End-to-end localhost checks against the running Vite proxy and Python server."""
import asyncio
import json
import httpx
from websockets.asyncio.client import connect

async def main():
    with httpx.Client(base_url="http://127.0.0.1:5173", timeout=15) as client:
        health = client.get("/api/health")
        health.raise_for_status()
        assert health.json()["implementation"] == "python"
        request = client.get("/api/examples/sphere?width=1000&height=700").json()
        request["request_id"], request["revision"] = "http-smoke", 123
        result = client.post("/api/solve", json=request)
        result.raise_for_status()
        assert result.json()["status"] == "ok"
        assert result.json()["revision"] == 123
    async with connect("ws://127.0.0.1:5173/api/stream") as websocket:
        request["request_id"] = "ws-smoke"
        await websocket.send(json.dumps(request))
        result = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
        assert result["status"] == "ok" and result["request_id"] == "ws-smoke"
    print("HTTP proxy + Python solver + WebSocket preview: passed")

asyncio.run(main())
