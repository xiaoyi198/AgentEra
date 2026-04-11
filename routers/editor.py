"""Agent 编辑器 — 官员 / 地点 / 人格 / Prompt 管理."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from . import _state as state

router = APIRouter(prefix="/api", tags=["editor"])

OFFICIALS_PATH = "daming_court/config/officials.json"
PROMPTS_DIR = "daming_court/prompts"
PERSONAS_PATH = "daming_court/config/personas.json"


# ── 内部工具 ──

def _read_officials_json() -> dict:
    with open(OFFICIALS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _write_officials_json(data: dict) -> None:
    Path(OFFICIALS_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(OFFICIALS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_personas() -> dict[str, str]:
    if not Path(PERSONAS_PATH).exists():
        return {
            "贪腐": "你是一个贪腐官员，倾向于隐瞒灾情以避免追责，偶尔索取好处。上报时会淡化问题严重性，优先考虑自己的仕途和钱袋。",
            "忠直": "你是一个忠直官员，如实上报，不夸大不隐瞒，为百姓着想。哪怕可能得罪上级，也要把真实情况传递出去。",
            "圆滑": "你是一个圆滑官员，善于揣摩上意，措辞委婉，优先保护自己。你会根据接收者的喜好调整措辞，既不完全说谎也不完全说实话。",
        }
    with open(PERSONAS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _write_personas(data: dict[str, str]) -> None:
    Path(PERSONAS_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(PERSONAS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 官员 CRUD ──


class OfficialUpsertRequest(BaseModel):
    id: str
    name: str
    rank: int = 1
    location_id: str = ""
    role: str = "official"
    agent_type: str = "official"
    superior_id: str | None = None
    personality_seed: str = "忠直"
    enabled: bool = True
    extra_prompt: str = ""


@router.get("/editor/officials")
async def editor_get_officials():
    """返回完整的 officials + locations 配置."""
    if not Path(OFFICIALS_PATH).exists():
        return {"officials": [], "locations": []}
    return _read_officials_json()


@router.put("/editor/officials/{official_id}")
async def editor_update_official(official_id: str, req: OfficialUpsertRequest):
    """更新或新增一个官员."""
    data = _read_officials_json()
    officials = data.get("officials", [])
    found = False
    for i, o in enumerate(officials):
        if o["id"] == official_id:
            officials[i] = req.model_dump()
            found = True
            break
    if not found:
        officials.append(req.model_dump())
    data["officials"] = officials
    _write_officials_json(data)
    return {"status": "ok", "created": not found}


@router.delete("/editor/officials/{official_id}")
async def editor_delete_official(official_id: str):
    """删除一个官员."""
    data = _read_officials_json()
    before = len(data.get("officials", []))
    data["officials"] = [o for o in data.get("officials", []) if o["id"] != official_id]
    _write_officials_json(data)
    return {"status": "ok", "deleted": len(data["officials"]) < before}


@router.post("/editor/officials/batch")
async def editor_batch_save(officials: list[OfficialUpsertRequest]):
    """批量保存（覆盖全部官员列表，保留 locations）."""
    data = _read_officials_json()
    data["officials"] = [o.model_dump() for o in officials]
    _write_officials_json(data)
    return {"status": "ok", "count": len(officials)}


# ── 地点坐标（读写 WorldState entity metadata）──


class LocationUpsertRequest(BaseModel):
    id: str
    name: str
    region: str = ""
    x: float = 0.0
    y: float = 0.0


@router.get("/editor/locations/{location_id}")
async def editor_get_location(location_id: str):
    """获取单个地点坐标（从 WorldState metadata 读取）."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    ws = getattr(state.orchestrator, "world_state", None)
    if not ws:
        return {"error": "world_state not initialized"}
    entity = ws.get_entity(location_id)
    if not entity:
        return {"error": "not found", "location_id": location_id}
    meta = entity.metadata
    return {
        "id": entity.id,
        "name": entity.name,
        "region": meta.get("region", ""),
        "x": float(meta.get("x", 0)),
        "y": float(meta.get("y", 0)),
    }


@router.put("/editor/locations/{location_id}")
async def editor_update_location(location_id: str, req: LocationUpsertRequest):
    """更新单个地点坐标（写入 WorldState metadata）."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    ws = getattr(state.orchestrator, "world_state", None)
    if not ws:
        return {"error": "world_state not initialized"}
    entity = ws.get_entity(location_id)
    if not entity:
        return {"error": "not found", "location_id": location_id}
    entity.metadata["x"] = req.x
    entity.metadata["y"] = req.y
    entity.metadata["region"] = req.region
    entity.name = req.name
    ws.upsert_entity(entity)
    return {"status": "ok", "location_id": location_id}


# ── Prompt 预览 ──


class PromptPreviewRequest(BaseModel):
    id: str
    name: str
    rank: int = 1
    location_id: str = ""
    superior_id: str | None = None
    personality_seed: str = "忠直"
    extra_prompt: str = ""


@router.post("/editor/prompt-preview")
async def editor_prompt_preview(req: PromptPreviewRequest):
    """预览某官员的 system prompt 渲染结果."""
    from daming_court.loader import load_personas, _build_system_prompt
    from multiagent_framework.court_graph import Official

    official = Official(
        id=req.id, name=req.name, rank=req.rank,
        location_id=req.location_id, role="official",
        agent_type="official", superior_id=req.superior_id,
        personality_seed=req.personality_seed,
        extra_prompt=req.extra_prompt,
    )
    personas = load_personas()
    prompt = _build_system_prompt(official, personas, PROMPTS_DIR)
    return {"prompt": prompt}


# ── 人格种子 CRUD ──


class PersonaUpsertRequest(BaseModel):
    name: str
    description: str


@router.get("/editor/personas")
async def editor_get_personas():
    """返回所有人格种子."""
    return _read_personas()


@router.put("/editor/personas/{persona_name}")
async def editor_upsert_persona(persona_name: str, req: PersonaUpsertRequest):
    """新增或更新一个人格种子，保存后立即热重载."""
    personas = _read_personas()
    if persona_name != req.name and persona_name in personas:
        del personas[persona_name]
    personas[req.name] = req.description
    _write_personas(personas)
    return {"status": "ok"}


@router.delete("/editor/personas/{persona_name}")
async def editor_delete_persona(persona_name: str):
    """删除一个人格种子."""
    personas = _read_personas()
    deleted = persona_name in personas
    personas.pop(persona_name, None)
    _write_personas(personas)
    return {"status": "ok", "deleted": deleted}


@router.post("/editor/personas/batch")
async def editor_batch_save_personas(personas: dict[str, str]):
    """批量保存所有人格种子（覆盖），保存后热重载."""
    _write_personas(personas)
    return {"status": "ok", "count": len(personas)}


# ── Prompt 文件编辑 ──


@router.get("/prompts")
async def get_prompts():
    """返回所有可编辑 prompt 文件."""
    result = {}
    for name, path in [
        ("official_template", "daming_court/prompts/official_template.txt"),
        ("router", "daming_court/prompts/router.txt"),
        ("action_agent", "daming_court/prompts/action_agent.txt"),
        ("tianji", "daming_court/prompts/tianji.txt"),
        ("personas", "daming_court/config/personas.json"),
    ]:
        p = Path(path)
        if p.exists():
            result[name] = p.read_text(encoding="utf-8")
        else:
            result[name] = ""
    return result


class PromptSaveRequest(BaseModel):
    name: str  # official_template / router / personas
    content: str


@router.post("/prompts")
async def save_prompt(req: PromptSaveRequest):
    """保存 prompt 文件."""
    paths = {
        "official_template": "daming_court/prompts/official_template.txt",
        "router": "daming_court/prompts/router.txt",
        "action_agent": "daming_court/prompts/action_agent.txt",
        "tianji": "daming_court/prompts/tianji.txt",
        "personas": "daming_court/config/personas.json",
    }
    path = paths.get(req.name)
    if not path:
        return {"error": f"unknown prompt: {req.name}"}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(req.content, encoding="utf-8")
    if req.name == "router" and state.orchestrator:
        state.orchestrator.router._system_prompt = req.content
    return {"status": "ok"}
