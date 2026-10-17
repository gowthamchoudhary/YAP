"""Scene detection + in-memory scene store.

POST /api/scene reads a multipart image, asks the vision model for the
physical objects in frame, validates them with pydantic, stores the image in
memory under a scene_id so the SAME photo can be replayed in a different mode,
and returns {scene_id, objects}.
"""
import time
import uuid
from typing import Any, Literal

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

import apilog
import llm

router = APIRouter(prefix="/api")

MAX_OBJECTS = 12
SCENE_PROMPT = """You are looking at a photo taken by a user (a room, desk, garden, bed, car, street...).
List every DISTINCT physical object you can clearly see that could be made into a cartoon character.
Everyday furniture, plants, tools, electronics, food, containers, textiles and building parts are all eligible.
Do NOT list the room itself, walls, floors or vague areas; list concrete individually visible objects.
Do NOT list people or animals.
For each object give:
 id: short stable machine id that IS "obj1", "obj2", ... in this exact order
 label: 1-3 common words naming it (e.g. "desk lamp")
 bbox: normalized bounding box [x, y, w, h], each between 0.0 and 1.0, covering the whole object
 hint: one short line about what personality would suit this object in a cartoon
Return at most 12 objects, the most visually prominent first.
Reply ONLY with JSON of the exact shape:
{"objects": [{"id": "obj1", "label": "...", "bbox": [0.1, 0.2, 0.3, 0.4], "hint": "..."}]}"""


class SceneObject(BaseModel):
    id: str = Field(min_length=1, max_length=24)
    label: str = Field(min_length=1, max_length=48)
    bbox: list[float] = Field(min_length=4, max_length=4)
    hint: str = Field(default="", max_length=200)

    @field_validator("bbox")
    @classmethod
    def _clamp_bbox(cls, v: list[float]) -> list[float]:
        out = []
        for x in v:
            out.append(min(1.0, max(0.0, float(x))))
        # safety: keep box inside the frame
        x, y, w, h = out
        w = min(w, 1.0 - x) or 0.05
        h = min(h, 1.0 - y) or 0.05
        return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


class Scene(BaseModel):
    scene_id: str
    image_mime: str
    objects: list[SceneObject]
    created_at: float


# In-memory store: scene_id -> Scene (with raw image bytes kept beside the model)
_STORE: dict[str, dict[str, Any]] = {}
_SCENE_HARD_LIMIT = 32


def _store_scene(scene: Scene, image: bytes) -> None:
    if len(_STORE) >= _SCENE_HARD_LIMIT:
        oldest = min(_STORE.values(), key=lambda s: s["scene"].created_at).scene_id
        _STORE.pop(oldest, None)
    _STORE[scene.scene_id] = {"scene": scene, "image": image}


def get_scene(scene_id: str) -> dict[str, Any] | None:
    return _STORE.get(scene_id)


def get_scene_image(scene_id: str) -> bytes | None:
    entry = _STORE.get(scene_id)
    return entry["image"] if entry else None


class _VisionObjects(BaseModel):
    objects: list[SceneObject]


@router.post("/scene")
async def create_scene(file: UploadFile = File(...)) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        image = await file.read()
    finally:
        pass
    if not image:
        raise HTTPException(status_code=400, detail="Empty upload: no image bytes received.")
    if len(image) > 15_000_000:
        raise HTTPException(status_code=413, detail="Image too large (>15 MB). Retake or compress it.")
    image_mime = file.content_type if file.content_type and "/" in file.content_type else "image/jpeg"
    if image_mime in ("image/jpg",):
        image_mime = "image/jpeg"

    try:
        raw = await llm.vision(image, SCENE_PROMPT, mime=image_mime)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Vision call failed: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Vision model did not return parseable JSON: {exc}",
        ) from exc
    except RuntimeError as exc:
        # from llm.py: provider errors / missing keys with explicit variable names
        code = 400 if "environment variable" in str(exc) else 502
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    # Tolerate {"objects": [...]} or a bare list [...]
    if isinstance(raw, list):
        raw = {"objects": raw}
    try:
        parsed = _VisionObjects.model_validate(raw).objects
    except Exception as exc:  # noqa: BLE001 - invalid schema from model
        raise HTTPException(
            status_code=502, detail=f"Vision output failed validation: {exc}"
        ) from exc

    if len(parsed) > MAX_OBJECTS:
        parsed = parsed[:MAX_OBJECTS]
    if not parsed:
        raise HTTPException(
            status_code=422,
            detail="No objects detected in this photo. Try a busier scene or better lighting.",
        )

    # Guard against duplicate ids
    seen: set[str] = set()
    uniq: list[SceneObject] = []
    for i, obj in enumerate(parsed):
        oid = obj.id or f"obj{i + 1}"
        if oid in seen:
            oid = f"{oid}_{i}"
        seen.add(oid)
        uniq.append(obj.model_copy(update={"id": oid}))

    scene = Scene(
        scene_id=uuid.uuid4().hex[:16],
        image_mime=image_mime,
        objects=uniq,
        created_at=time.time(),
    )
    _store_scene(scene, image)
    apilog.log_call(
        service="scene-store",
        endpoint="/api/scene",
        status="ok",
        latency_ms=(time.perf_counter() - t0) * 1000,
        request_bytes=len(image),
        response_bytes=None,
    )
    return {
        "scene_id": scene.scene_id,
        "objects": [o.model_dump() for o in scene.objects],
        "latency_ms": round((time.perf_counter() - t0) * 1000),
    }
