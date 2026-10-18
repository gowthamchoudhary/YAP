"""YAP — backend app.

Run:  python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

Routes:
  POST /api/scene          multipart image -> {scene_id, objects}
  GET  /api/modes          list of mode definitions from backend/modes/*.json
  GET  /api/audio/{line}   cached TTS audio for a spoken line
  GET  /api/health         liveness
  WS   /ws/session         character conversation (start|user_*|played|...)
  WS   /ws/stt             browser PCM 16k mono -> AssemblyAI streaming v3 relay
"""
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

import apilog
import engine
import scene
import tts
from config import get

CORS_ORIGINS = (get("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173") or "").split(",")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await tts.warm_up()  # fail-safe: logs what is missing instead of crashing
    apilog.log_call(service="app", endpoint="startup", status="ok", latency_ms=0.0)
    yield
    apilog.log_call(service="app", endpoint="shutdown", status="ok", latency_ms=0.0)


app = FastAPI(title="YAP", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scene.router)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "tts_configured": tts.is_configured(),
        "sessions": len(engine.SESSIONS),
    }


@app.get("/api/modes")
async def modes() -> list[dict[str, Any]]:
    return engine.load_modes()


@app.get("/api/voices")
async def voices() -> dict[str, Any]:
    """Voice list for the Setup screen's per-character voice selector."""
    import characters as characters_mod

    return {"voices": characters_mod.load_voices()}


@app.get("/api/tts_capabilities")
async def tts_capabilities() -> dict[str, Any]:
    return dict(tts.load_capabilities())


@app.get("/api/audio/{line_id}")
async def audio(line_id: str) -> Response:
    item = engine.get_audio(line_id)
    if item is None:
        detail = "Unknown line id (audio was cut off, expired, or TTS is not configured for this line)."
        raise HTTPException(status_code=404, detail=detail)
    audio_bytes, mime = item
    return Response(content=audio_bytes, media_type=mime, headers={"Cache-Control": "public, max-age=300"})


@app.websocket("/ws/session")
async def ws_session(websocket: WebSocket) -> None:
    await engine.handle_ws(websocket)


@app.websocket("/ws/stt")
async def ws_stt(websocket: WebSocket) -> None:
    import stt

    await stt.handle_websocket(websocket)


if __name__ == "__main__":
    import uvicorn

    port = int(get("PORT", "8000") or 8000)
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
    print(f"backend on 0.0.0.0:{port}", file=sys.stderr)
