"""DLC 数据加载器 — 从 JSON 配置加载官员和 Agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from multiagent_framework.types import EventObject
from multiagent_framework.agent_runtime import (
    AgentConfig,
    BaseAgent,
    InstitutionAgent,
    OfficialAgent,
)
from multiagent_framework.court_graph import Official, Location
from multiagent_framework.llm_client import LLMClient
from multiagent_framework.world_state import Entity, InventoryItem, WorldState


def load_officials(config_path: str) -> list[Official]:
    """从 JSON 加载官员列表."""
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    officials = []
    for item in data.get("officials", []):
        officials.append(Official(
            id=item["id"],
            name=item["name"],
            rank=item.get("rank", 1),
            location_id=item.get("location_id", ""),
            role=item.get("role", "official"),
            agent_type=item.get("agent_type", "official"),
            superior_id=item.get("superior_id"),
            personality_seed=item.get("personality_seed", "忠直"),
            enabled=item.get("enabled", True),
            extra_prompt=item.get("extra_prompt", ""),
        ))
    return officials


def load_locations(config_path: str) -> list[Location]:
    """从 JSON 加载地理位置."""
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    locations = []
    for item in data.get("locations", []):
        locations.append(Location(
            id=item["id"],
            name=item["name"],
            region=item.get("region", ""),
            x=float(item.get("x", 0)),
            y=float(item.get("y", 0)),
        ))
    return locations


def load_personas(config_path: str = "daming_court/config/personas.json") -> dict[str, str]:
    """加载人格种子配置."""
    path = Path(config_path)
    if not path.exists():
        return {
            "贪腐": "你是一个贪腐官员，倾向于隐瞒灾情以避免追责，偶尔索取好处。",
            "忠直": "你是一个忠直官员，如实上报，不夸大不隐瞒，为百姓着想。",
            "圆滑": "你是一个圆滑官员，善于揣摩上意，措辞委婉，优先保护自己。",
        }
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _build_system_prompt(
    official: Official,
    personas: dict[str, str],
    prompts_dir: str,
) -> str:
    """为官员构建 system prompt."""
    # 先尝试加载专用 prompt 文件
    specific_path = Path(prompts_dir) / f"{official.id}.txt"
    if specific_path.exists():
        return specific_path.read_text(encoding="utf-8")

    # 通用 prompt 模板
    template_path = Path(prompts_dir) / "official_template.txt"
    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")
    else:
        template = (
            "你是{name}，大明朝{location}的{rank_title}。\n"
            "{personality}\n"
            "你的直属上级是{superior}。\n"
            "你需要根据收到的信息做出决策，通过 submit_action 表达你的行动。\n"
            "记住：你只能表达意图，世界运行法则会处理实际的消息传递和政策执行。"
        )

    rank_titles = {
        1: "知县", 5: "知府", 8: "布政使", 10: "内阁大学士",
        12: "六部尚书", 15: "皇帝",
    }
    rank_title = rank_titles.get(official.rank, f"品级{official.rank}官员")

    persona_text = personas.get(official.personality_seed, "")

    prompt = template.format(
        name=official.name,
        location=official.location_id,
        rank_title=rank_title,
        personality=persona_text,
        superior=official.superior_id or "无",
        id=official.id,
        rank=official.rank,
    )

    # 附加个性化提示词（有则追加，不论 enabled 状态）
    if official.extra_prompt:
        prompt += "\n\n## 个人特质\n\n" + official.extra_prompt

    return prompt


def load_world_state(
    config_path: str,
    world_state: WorldState,
) -> tuple[int, int]:
    """从 JSON 加载初始世界状态（实体 + 库存）到 WorldState.

    返回 (entity_count, inventory_count)。
    文件不存在时返回 (0, 0) —— 允许 DLC 不配置初始世界状态。
    """
    path = Path(config_path)
    if not path.exists():
        return (0, 0)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    entities: list[Entity] = []
    for item in data.get("entities", []):
        entities.append(Entity(
            id=item["id"],
            type=item.get("type", "location"),
            name=item.get("name", item["id"]),
            description=item.get("description", ""),
            current_status=item.get("current_status", ""),
            updated_at=item.get("updated_at", 0.0),
            metadata=item.get("metadata", {}),
        ))

    inventory: list[InventoryItem] = []
    for item in data.get("inventory", []):
        inventory.append(InventoryItem(
            entity_id=item["entity_id"],
            category=item["category"],
            item_name=item["item_name"],
            quantity=float(item.get("quantity", 0)),
            description=item.get("description", ""),
        ))

    return world_state.bulk_load(entities, inventory)


def load_events(config_path: str = "daming_court/config/events.json") -> list[EventObject]:
    """从 JSON 加载初始事件."""
    path = Path(config_path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    events = []
    for item in data.get("initial_events", []):
        events.append(EventObject(
            event_id=item.get("event_id", ""),
            event_type=item.get("event_type", ""),
            severity=item.get("severity", 5),
            target_agents=item.get("target_agents", []),
            initial_payload=item.get("initial_payload", {}),
        ))
    return events


def create_agents(
    officials: list[Official],
    prompts_dir: str,
    llm_client: LLMClient,
) -> list[BaseAgent]:
    """为官员创建 Agent 实例."""
    personas = load_personas()
    agents: list[BaseAgent] = []

    for official in officials:
        if official.role == "player":
            continue  # 皇帝不创建 Agent
        if not official.enabled:
            continue  # 未启用的官员不创建 Agent

        system_prompt = _build_system_prompt(official, personas, prompts_dir)

        config = AgentConfig(
            agent_id=official.id,
            agent_type=official.agent_type,
            display_name=official.name,
            system_prompt=system_prompt,
            personality_seed=official.personality_seed,
        )

        if official.agent_type == "institution":
            agent = InstitutionAgent(config, llm_client)
        else:
            agent = OfficialAgent(config, llm_client)

        agents.append(agent)

    return agents
