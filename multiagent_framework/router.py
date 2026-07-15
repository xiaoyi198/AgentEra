"""混合路由器：确定性规则优先，仅把歧义意图交给 LLM。"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import deque
from typing import Any, Iterable

from .court_graph import CourtGraph, Official
from .fc_tools import LIST_AGENTS_TOOL, ROUTER_TOOLS
from .capability_contract import CapabilityCatalog, CapabilityContractError, CapabilityDefinition
from .llm_client import LLMClient, LLMResponse
from .runtime_bindings import ROUTER_TASK, RuntimeBindingsHub, RuntimeTaskBinding
from .types import AgentAction, RouterCommand, RouterCommandType

logger = logging.getLogger(__name__)

MAX_HOPS = 10
MAX_AMBIGUOUS_BATCH = 8
MAX_CANDIDATES_PER_ACTION = 12
RECENT_IDEMPOTENCY_LIMIT = 4096
CHAIN_EDGE_LIMIT = 2048
DEAD_LETTER_LIMIT = 1000
ROUTER_SCHEMA_VERSION = "2.0"


class RouterAgent:
    """把 AgentAction 转换成可验证、可追踪、可幂等执行的 RouterCommand。"""

    def __init__(self, llm_client: LLMClient, court_graph: CourtGraph, system_prompt: str = "", seconds_per_day: float = 600.0) -> None:
        self.llm = llm_client
        self.court = court_graph
        self.seconds_per_day = seconds_per_day
        self._system_prompt = system_prompt or self._default_prompt()
        self._runtime_bindings: RuntimeBindingsHub | None = None
        self.last_response_text = ""
        self.last_stats: dict[str, Any] = {}
        self.dead_letters: deque[dict[str, Any]] = deque(maxlen=DEAD_LETTER_LIMIT)
        self._recent_keys: set[str] = set()
        self._recent_key_order: deque[str] = deque()
        self._chain_edges: dict[str, set[tuple[str, str]]] = {}
        self._chain_order: deque[str] = deque()
        self._capability_catalog: CapabilityCatalog | None = None

    def configure_capability_catalog(self, catalog: CapabilityCatalog | None) -> None:
        self._capability_catalog = catalog

    def bind_runtime_bindings(self, hub: RuntimeBindingsHub) -> None:
        self._runtime_bindings = hub

    def _capture_runtime_binding(self) -> RuntimeTaskBinding:
        if self._runtime_bindings is not None:
            return self._runtime_bindings.capture(ROUTER_TASK)
        return RuntimeTaskBinding(
            task_id=ROUTER_TASK, generation=0, prompt=self._system_prompt, llm=self.llm
        )

    def _default_prompt(self) -> str:
        return """\u4f60\u662f\u4e16\u754c\u6a21\u62df\u5668\u7684\u8def\u7531\u5668\uff0c\u8d1f\u8d23\u5728\u4e0d\u66f2\u89e3\u539f\u59cb\u610f\u56fe\u7684\u524d\u63d0\u4e0b\u9009\u62e9\u786e\u5b9a\u7684\u6267\u884c\u76ee\u6807\u3002
\u80fd\u529b\u6a21\u5f0f\u4e0b\uff0c\u53ea\u80fd\u4f7f\u7528 DLC \u6ce8\u518c\u7684\u80fd\u529b ID \u548c\u5019\u9009 Agent ID\uff1b\u4e0d\u5f97\u66ff\u6362\u6216\u865a\u6784\u80fd\u529b\u3002\u5982\u9700\u6d88\u6b67\uff0c\u8c03\u7528 route_capability \u5e76\u7ed9\u51fa\u7b80\u6d01\u7406\u7531\u3002"""

    async def process_batch(self, actions: list[AgentAction], context: dict[str, Any] | None = None) -> list[RouterCommand]:
        if not actions:
            self.last_stats = {"input_count": 0, "deterministic_count": 0, "ambiguous_count": 0, "llm_calls": 0, "command_count": 0, "dead_letter_count": 0}
            return []
        self.last_response_text = ""
        dead_before = len(self.dead_letters)
        commands: list[RouterCommand] = []
        ambiguous: list[AgentAction] = []
        deterministic_count = 0
        for action in actions:
            self._normalize_action_metadata(action)
            command, needs_llm = self._route_deterministic(action)
            if command is not None:
                accepted = self._accept_command(command)
                if accepted is not None:
                    commands.append(accepted)
                    deterministic_count += 1
            elif needs_llm:
                ambiguous.append(action)
        llm_calls = 0
        response_texts: list[str] = []
        for chunk in self._chunks(ambiguous, MAX_AMBIGUOUS_BATCH):
            llm_calls += 1
            llm_commands, response_text = await self._route_ambiguous_chunk(chunk, context)
            if response_text:
                response_texts.append(response_text)
            for command in llm_commands:
                accepted = self._accept_command(command)
                if accepted is not None:
                    commands.append(accepted)
        self.last_response_text = "\n".join(response_texts)
        self.last_stats = {
            "input_count": len(actions), "deterministic_count": deterministic_count,
            "ambiguous_count": len(ambiguous), "llm_calls": llm_calls,
            "command_count": len(commands), "dead_letter_count": max(0, len(self.dead_letters) - dead_before),
            "candidate_limit": MAX_CANDIDATES_PER_ACTION, "batch_limit": MAX_AMBIGUOUS_BATCH,
        }
        return commands

    def _normalize_action_metadata(self, action: AgentAction) -> None:
        action.metadata = dict(action.metadata or {})
        action.metadata.setdefault("intent_id", f"intent_{uuid.uuid4().hex[:10]}")
        action.metadata.setdefault("chain_id", f"chain_{uuid.uuid4().hex[:8]}")
        action.metadata.setdefault("schema_version", ROUTER_SCHEMA_VERSION)
        try:
            action.metadata["hop_count"] = int(action.metadata.get("hop_count", 0) or 0)
        except (TypeError, ValueError):
            action.metadata["hop_count"] = 0

    def _route_deterministic(self, action: AgentAction) -> tuple[RouterCommand | None, bool]:
        catalog = self._capability_catalog
        if catalog is not None and catalog.configured:
            requested = str(action.metadata.get("capability_id") or action.action or "").strip()
            definition = catalog.resolve(requested, audience="agent")
            if definition is None:
                known = catalog.resolve(requested)
                status = "capability_forbidden" if known is not None else "unknown_capability"
                self._dead_letter(action, status, f"Agent capability {requested!r} is not available")
                return None, False
            return self._route_capability(action, definition)

        action_type = (action.action or "").strip().lower()
        if action_type in {"", "do_nothing", "no_op"}:
            return None, False
        if action_type == "report_message":
            return self._route_report(action)
        if action_type in {"do_something", "execute_local_policy", "execute_policy"}:
            return self._route_policy(action)
        if action_type in {"issue_decree", "relay_decree"}:
            return self._route_decree(action)
        return None, True

    def _route_capability(
        self,
        action: AgentAction,
        definition: CapabilityDefinition,
    ) -> tuple[RouterCommand | None, bool]:
        """Translate a DLC capability into the framework-private transport command."""
        source = self.court.get_official(action.agent_id)
        if source is None or not source.enabled:
            self._dead_letter(action, "invalid_source", "source Agent does not exist or is disabled")
            return None, False
        if definition.handler == "noop":
            return None, False

        metadata = dict(action.metadata or {})
        semantic_targets = self._raw_capability_targets(metadata)
        route_targets: list[str] = []
        route_reason = f"capability:{definition.handler}"

        if definition.handler in {"message", "broadcast"}:
            if semantic_targets:
                resolved, ambiguous = self._resolve_agent_targets(semantic_targets)
                if ambiguous:
                    return None, True
                if resolved is None:
                    self._dead_letter(action, "invalid_target", "capability target did not match an enabled Agent")
                    return None, False
                semantic_targets = resolved
                route_reason += ":explicit_target"
            elif definition.handler == "message":
                superior = self.court.get_superior(action.agent_id)
                if superior is None or not superior.enabled:
                    if definition.target.mode == "required":
                        return None, True
                    self._dead_letter(action, "invalid_target", "message capability has no enabled superior")
                    return None, False
                semantic_targets = [superior.id]
                route_reason += ":direct_superior"
            else:
                semantic_targets = [
                    item.id for item in self.court.get_subordinates(action.agent_id)
                    if item.enabled and item.id != action.agent_id
                ]
                route_reason += ":subordinates"
            route_targets = list(dict.fromkeys(semantic_targets))

        elif definition.handler == "world_action":
            executor_hint = str(metadata.get("executor") or metadata.get("executor_hint") or "").strip()
            if executor_hint:
                resolved, ambiguous = self._resolve_agent_targets([executor_hint])
                if ambiguous:
                    return None, True
                if not resolved:
                    self._dead_letter(action, "invalid_target", f"executor hint {executor_hint!r} did not match an enabled Agent")
                    return None, False
                executor_id = resolved[0]
                route_reason += ":explicit_executor"
            else:
                executor_id = action.agent_id
                route_reason += ":source_executor"
            route_targets = [executor_id]
            metadata["executor_id"] = executor_id
            location = str(
                metadata.get("target_location")
                or metadata.get("location")
                or (metadata.get("parameters") or {}).get("target_location", "")
                or (metadata.get("parameters") or {}).get("location", "")
                or source.location_id
                or ""
            ).strip()
            if location:
                metadata["location"] = location

        raw_invocation = {
            "expected_generation": metadata.get("capability_generation"),
            "invocation_id": metadata.get("invocation_id", ""),
            "content": action.content,
            "target_id": semantic_targets[0] if semantic_targets else "",
            "target_ids": semantic_targets,
            "parameters": metadata.get("parameters", {}),
            "urgency": action.urgency,
            "original_intent": action.original_intent,
            "is_distorting": action.is_distorting,
        }
        try:
            invocation = self._capability_catalog.prepare_invocation(
                definition.capability_id,
                raw_invocation,
                audience="agent",
                source_id=action.agent_id,
            )
        except CapabilityContractError as exc:
            self._dead_letter(action, exc.code, str(exc))
            return None, False
        if not invocation["invocation_id"]:
            invocation["invocation_id"] = str(metadata.get("intent_id", "") or f"inv_{uuid.uuid4().hex[:16]}")
        if definition.handler == "world_action":
            invocation["executor_id"] = metadata.get("executor_id", action.agent_id)
            invocation["location"] = metadata.get("location", "")

        command_target = route_targets[0] if len(route_targets) == 1 else None
        command_targets = route_targets if len(route_targets) > 1 else []
        return self._base_command(
            action,
            RouterCommandType.INVOKE_CAPABILITY,
            target=command_target,
            targets=command_targets,
            payload=invocation,
            metadata={
                "route_strategy": "deterministic",
                "route_reason": route_reason,
                "route_target_ids": route_targets,
                "capability_id": definition.capability_id,
            },
        ), False

    @staticmethod
    def _raw_capability_targets(metadata: dict[str, Any]) -> list[str]:
        values = metadata.get("target_ids")
        if isinstance(values, list):
            targets = [str(item).strip() for item in values if str(item).strip()]
        elif isinstance(values, str):
            targets = [item.strip() for item in values.replace("\uFF0C", ",").split(",") if item.strip()]
        else:
            targets = []
        single = str(
            metadata.get("target_id")
            or metadata.get("target_hint")
            or metadata.get("target")
            or metadata.get("recipient")
            or ""
        ).strip()
        if single and single not in targets:
            targets.insert(0, single)
        return list(dict.fromkeys(targets))

    def _resolve_agent_targets(self, hints: list[str]) -> tuple[list[str] | None, bool]:
        resolved: list[str] = []
        for hint in hints[:MAX_CANDIDATES_PER_ACTION]:
            direct = self.court.get_official(hint)
            if direct is not None and direct.enabled:
                matches = [direct]
            else:
                matches = [item for item in self.court.resolve_target_hint(hint, limit=MAX_CANDIDATES_PER_ACTION + 1) if item.enabled]
            if not matches:
                return None, False
            if len(matches) > 1:
                return None, True
            if matches[0].id not in resolved:
                resolved.append(matches[0].id)
        return resolved, False

    def _route_report(self, action: AgentAction) -> tuple[RouterCommand | None, bool]:
        source = self.court.get_official(action.agent_id)
        if source is None or not source.enabled:
            self._dead_letter(action, "invalid_source", "source Agent does not exist or is disabled")
            return None, False
        hint = self._target_hint(action.metadata)
        route_reason = "direct_superior"
        if hint:
            matches = self.court.resolve_target_hint(hint, limit=MAX_CANDIDATES_PER_ACTION + 1)
            if not matches:
                self._dead_letter(action, "invalid_target", f"target hint {hint!r} did not match an Agent")
                return None, False
            if len(matches) > 1:
                return None, True
            target = matches[0]
            route_reason = "explicit_target"
        else:
            target = self.court.get_superior(action.agent_id)
            if target is None or not target.enabled:
                self._dead_letter(action, "invalid_target", "report_message has no enabled superior")
                return None, False
        path = self.court.get_path(action.agent_id, target.id, enabled_only=True)
        if not path:
            self._dead_letter(action, "unreachable", f"no enabled hierarchy path to {target.id}", target=target.id)
            return None, False
        if target.id == action.agent_id:
            self._dead_letter(action, "invalid_target", "self-route is not allowed", target=target.id)
            return None, False
        return self._base_command(
            action, RouterCommandType.WAKE_AGENT, target=target.id,
            payload={"message": action.content, "from": action.agent_id, "urgency": self._transport_urgency(action.urgency)},
            metadata={"route_strategy": "deterministic", "route_reason": route_reason,
                      "path": [official.id for official in path],
                      "skip_level": not self.court.is_direct_relation(action.agent_id, target.id)},
        ), False

    def _route_policy(self, action: AgentAction) -> tuple[RouterCommand | None, bool]:
        source = self.court.get_official(action.agent_id)
        if source is None or not source.enabled:
            self._dead_letter(action, "invalid_source", "source Agent does not exist or is disabled")
            return None, False
        if not (action.content or "").strip():
            return None, True
        metadata = action.metadata
        executor: Official = source
        executor_hint = str(metadata.get("executor") or metadata.get("executor_hint") or "").strip()
        if executor_hint:
            matches = self.court.resolve_target_hint(executor_hint, limit=MAX_CANDIDATES_PER_ACTION + 1)
            if not matches:
                self._dead_letter(action, "invalid_target", f"executor hint {executor_hint!r} did not match an Agent")
                return None, False
            if len(matches) > 1:
                return None, True
            executor = matches[0]
            if not self.court.is_reachable(action.agent_id, executor.id, max_depth=MAX_HOPS, enabled_only=True):
                self._dead_letter(action, "unreachable", f"executor {executor.id} is unreachable", target=executor.id)
                return None, False
        location = str(metadata.get("target_location") or metadata.get("location") or source.location_id or "").strip()
        if not location:
            return None, True
        return self._base_command(
            action, RouterCommandType.EXECUTE_POLICY, target=executor.id,
            payload={"policy": action.content, "description": action.content, "location": location,
                     "from": action.agent_id, "intent_id": metadata.get("intent_id", "")},
            metadata={"route_strategy": "deterministic", "route_reason": "structured_policy"},
        ), False

    def _route_decree(self, action: AgentAction) -> tuple[RouterCommand | None, bool]:
        source = self.court.get_official(action.agent_id)
        if source is None or not source.enabled:
            self._dead_letter(action, "invalid_source", "decree source does not exist or is disabled")
            return None, False
        raw_targets = action.metadata.get("targets") or action.metadata.get("target_hint")
        targets: list[Official] = []
        if isinstance(raw_targets, str) and raw_targets.strip():
            for part in [item.strip() for item in raw_targets.replace("，", ",").split(",") if item.strip()]:
                matches = self.court.resolve_target_hint(part, limit=2)
                if len(matches) != 1:
                    if not matches:
                        self._dead_letter(action, "invalid_target", f"decree target {part!r} not found")
                        return None, False
                    return None, True
                targets.append(matches[0])
        elif isinstance(raw_targets, list):
            for item in raw_targets[:MAX_CANDIDATES_PER_ACTION]:
                matches = self.court.resolve_target_hint(str(item), limit=2)
                if not matches:
                    self._dead_letter(action, "invalid_target", f"decree target {item!r} not found")
                    return None, False
                if len(matches) != 1:
                    return None, True
                targets.append(matches[0])
        else:
            targets = [item for item in self.court.get_subordinates(source.id) if item.enabled]
        unique_targets = list(dict.fromkeys(item.id for item in targets if item.enabled and item.id != source.id))
        if not unique_targets:
            self._dead_letter(action, "invalid_target", "decree has no enabled recipients")
            return None, False
        for target_id in unique_targets:
            if not self.court.is_reachable(source.id, target_id, max_depth=MAX_HOPS, enabled_only=True):
                self._dead_letter(action, "unreachable", f"decree target {target_id} is unreachable", target=target_id)
                return None, False
        return self._base_command(
            action, RouterCommandType.RELAY_DECREE, targets=unique_targets,
            payload={"decree": action.content, "urgency": self._transport_urgency(action.urgency)},
            metadata={"route_strategy": "deterministic", "route_reason": "imperial_decree"},
        ), False

    async def _route_ambiguous_chunk(self, actions: list[AgentAction], context: dict[str, Any] | None) -> tuple[list[RouterCommand], str]:
        binding = self._capture_runtime_binding()
        messages = [{"role": "system", "content": binding.prompt}, {"role": "user", "content": self._build_batch_description(actions, context)}]
        primary_chain = str(actions[0].metadata.get("chain_id", "")) if actions else ""
        tools = ROUTER_TOOLS
        catalog = self._capability_catalog
        if catalog is not None and catalog.configured:
            route_tool = catalog.build_tool(
                audience="router",
                name="route_capability",
                description="\u4e3a Agent \u7684 DLC \u80fd\u529b\u8c03\u7528\u9009\u62e9\u5408\u6cd5\u76ee\u6807\uff0c\u4e0d\u5f97\u6539\u53d8\u80fd\u529b ID\u3002",
            )
            tools = [LIST_AGENTS_TOOL, route_tool] if route_tool is not None else [LIST_AGENTS_TOOL]
        try:
            response = await binding.llm.chat(
                messages=messages, tools=tools, temperature=0.2,
                trace_context={"trace_id": uuid.uuid4().hex, "turn_id": uuid.uuid4().hex,
                               "chain_id": primary_chain, "agent_id": "router", "task_type": "router.ambiguous",
                               "runtime_generation": binding.generation},
            )
        except Exception as exc:
            logger.error("Router ambiguous LLM failed: %s", exc)
            for action in actions:
                self._dead_letter(action, "llm_error", str(exc))
            return [], ""
        return self._parse_commands(response, actions), response.content or ""

    def _build_batch_description(self, actions: list[AgentAction], context: dict[str, Any] | None) -> str:
        parts = ["以下行动无法由确定性规则唯一解析。请只使用每项列出的候选目标："]
        for index, action in enumerate(actions, 1):
            candidates = self.court.get_routing_candidates(action.agent_id, hint=self._target_hint(action.metadata), limit=MAX_CANDIDATES_PER_ACTION)
            candidate_desc = ", ".join(f"{item.id}({item.name}, rank={item.rank}, loc={item.location_id})" for item in candidates) or "无"
            parts.append("\n".join([
                f"【行动 {index}】", f"intent_id: {action.metadata.get('intent_id', '')}",
                f"from_agent: {action.agent_id}", f"action: {action.action}", f"content: {action.content}",
                f"urgency: {action.urgency}", f"metadata: {json.dumps(action.metadata, ensure_ascii=False, default=str)[:1000]}",
                f"候选目标(最多{MAX_CANDIDATES_PER_ACTION}个): {candidate_desc}",
            ]))
        if context:
            parts.append("\n有限世界上下文：" + json.dumps(context, ensure_ascii=False, default=str)[:2000])
        return "\n\n".join(parts)

    def _parse_commands(self, response: LLMResponse, actions: list[AgentAction]) -> list[RouterCommand]:
        commands: list[RouterCommand] = []
        unmatched = list(actions)
        for tool_call in response.tool_calls:
            function = tool_call.get("function", {})
            name = function.get("name", "")
            try:
                args = json.loads(function.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                logger.warning("Router: invalid FC JSON for %s", name)
                continue
            source_action = self._match_source_action(args, unmatched)
            if source_action is None:
                logger.warning("Router: could not associate %s with a source action", name)
                continue
            if source_action in unmatched:
                unmatched.remove(source_action)
            command = self._fc_to_command(name, args, source_action)
            if command is not None:
                commands.append(command)
        if not commands and response.content:
            logger.info("Router returned text only: %s", response.content[:200])
        return commands

    @staticmethod
    def _match_source_action(args: dict[str, Any], unmatched: list[AgentAction]) -> AgentAction | None:
        requested_intent = str(args.get("intent_id", "") or "")
        if requested_intent:
            for action in unmatched:
                if str(action.metadata.get("intent_id", "")) == requested_intent:
                    return action
        from_agent = str(args.get("from_agent", "") or "")
        if from_agent:
            for action in unmatched:
                if action.agent_id == from_agent:
                    return action
        return unmatched[0] if len(unmatched) == 1 else None

    def _fc_to_command(self, fc_name: str, args: dict[str, Any], source_action: AgentAction) -> RouterCommand | None:
        candidate_ids = [
            item.id for item in self.court.get_routing_candidates(
                source_action.agent_id,
                hint=self._target_hint(source_action.metadata),
                limit=MAX_CANDIDATES_PER_ACTION,
            )
        ]
        metadata = {
            "route_strategy": "llm",
            "route_reason": "ambiguous_intent",
            "candidate_ids": candidate_ids,
        }
        catalog = self._capability_catalog
        if catalog is not None and catalog.configured:
            if fc_name != "route_capability":
                return None
            requested = str(source_action.metadata.get("capability_id") or source_action.action or "").strip()
            definition = catalog.resolve(requested, audience="agent")
            selected = catalog.resolve(str(args.get("capability_id", "") or ""), audience="router")
            if definition is None or selected is None or selected.capability_id != definition.capability_id:
                self._dead_letter(source_action, "capability_changed", "Router LLM attempted to change the source capability")
                return None
            selected_targets = args.get("target_ids") or []
            if isinstance(selected_targets, str):
                selected_targets = [item.strip() for item in selected_targets.replace("\uFF0C", ",").split(",") if item.strip()]
            if not isinstance(selected_targets, list):
                selected_targets = []
            selected_target = str(args.get("target_id", "") or "").strip()
            if selected_target and selected_target not in selected_targets:
                selected_targets.insert(0, selected_target)
            outside = [str(item) for item in selected_targets if str(item) not in candidate_ids]
            if outside:
                self._dead_letter(source_action, "invalid_target", f"Router LLM selected targets outside bounded candidates: {outside}")
                return None
            routed_action = AgentAction(
                agent_id=source_action.agent_id,
                action=definition.capability_id,
                content=source_action.content,
                original_intent=source_action.original_intent,
                is_distorting=source_action.is_distorting,
                urgency=source_action.urgency,
                metadata={
                    **dict(source_action.metadata or {}),
                    "target_id": selected_targets[0] if selected_targets else "",
                    "target_ids": [str(item) for item in selected_targets],
                },
            )
            command, _ = self._route_capability(routed_action, definition)
            if command is not None:
                command.source_action = source_action
                command.metadata.update(metadata)
                command.metadata["route_reason"] = f"capability:{definition.handler}:llm_target"
            return command
        if fc_name == "wake_agent":
            return self._base_command(source_action, RouterCommandType.WAKE_AGENT, target=str(args.get("target", "") or ""),
                payload={"message": args.get("message", ""), "from": args.get("from_agent", source_action.agent_id),
                         "urgency": self._transport_urgency(args.get("urgency", source_action.urgency))}, metadata=metadata)
        if fc_name == "execute_policy":
            return self._base_command(source_action, RouterCommandType.EXECUTE_POLICY,
                target=str(args.get("executor") or source_action.agent_id),
                payload={"policy": args.get("policy", source_action.content), "description": args.get("policy", source_action.content),
                         "location": args.get("location", ""), "from": args.get("from_agent", source_action.agent_id),
                         "intent_id": source_action.metadata.get("intent_id", "")}, metadata=metadata)
        if fc_name == "relay_decree":
            return self._base_command(source_action, RouterCommandType.RELAY_DECREE,
                targets=[str(item) for item in (args.get("targets") or [])],
                payload={"decree": args.get("decree", source_action.content),
                         "urgency": self._transport_urgency(args.get("urgency", source_action.urgency))}, metadata=metadata)
        return None

    def _base_command(self, action: AgentAction, command_type: RouterCommandType, *, target: str | None = None,
                      targets: list[str] | None = None, payload: dict[str, Any] | None = None,
                      metadata: dict[str, Any] | None = None) -> RouterCommand:
        action_meta = action.metadata or {}
        chain_id = str(action_meta.get("chain_id", "") or f"chain_{uuid.uuid4().hex[:8]}")
        parent_chain_ids = [str(item) for item in (action_meta.get("parent_chain_ids") or action_meta.get("chain_ids") or []) if item]
        return RouterCommand(
            command_type=command_type, target=target, targets=list(targets or []), payload=dict(payload or {}),
            chain_id=chain_id, source_action=action, intent_id=str(action_meta.get("intent_id", "")),
            source_agent_id=action.agent_id, hop_count=int(action_meta.get("hop_count", 0) or 0),
            schema_version=ROUTER_SCHEMA_VERSION,
            metadata={**dict(metadata or {}), "parent_chain_ids": parent_chain_ids},
        )

    def _accept_command(self, command: RouterCommand) -> RouterCommand | None:
        action = command.source_action or AgentAction(agent_id=command.source_agent_id)
        if command.hop_count > MAX_HOPS:
            self._dead_letter(action, "hop_limit", f"hop_count {command.hop_count} exceeds {MAX_HOPS}", command=command)
            return None

        is_capability = command.command_type == RouterCommandType.INVOKE_CAPABILITY
        allow_self = command.command_type == RouterCommandType.EXECUTE_POLICY or is_capability
        if is_capability:
            target_ids = list(dict.fromkeys(
                str(item) for item in (command.metadata.get("route_target_ids") or []) if str(item)
            ))
            handler = str(command.payload.get("handler", "") or "")
            if handler in {"message", "broadcast"} and not target_ids:
                self._dead_letter(action, "invalid_target", f"{handler} capability has no route target", command=command)
                return None
        elif command.command_type == RouterCommandType.RELAY_DECREE:
            target_ids = list(dict.fromkeys(command.targets))
            command.targets = target_ids
        elif command.target:
            target_ids = [command.target]
        else:
            target_ids = []
        if not target_ids and not is_capability:
            self._dead_letter(action, "invalid_target", "command has no target", command=command)
            return None

        source_id = command.source_agent_id or action.agent_id
        if command.metadata.get("route_strategy") == "llm":
            allowed = set(command.metadata.get("candidate_ids") or [])
            outside = [target_id for target_id in target_ids if target_id not in allowed]
            if outside:
                self._dead_letter(
                    action,
                    "invalid_target",
                    f"LLM selected targets outside bounded candidates: {outside}",
                    target=outside[0],
                    command=command,
                )
                return None
        for target_id in target_ids:
            target = self.court.get_official(target_id)
            if target is None or not target.enabled:
                self._dead_letter(action, "invalid_target", f"target {target_id!r} does not exist or is disabled", target=target_id, command=command)
                return None
            if source_id == target_id and not allow_self:
                self._dead_letter(action, "invalid_target", "self-route is not allowed", target=target_id, command=command)
                return None
            if source_id != target_id and not self.court.is_reachable(source_id, target_id, max_depth=MAX_HOPS, enabled_only=True):
                self._dead_letter(action, "unreachable", f"target {target_id!r} is not reachable", target=target_id, command=command)
                return None
        command.idempotency_key = command.idempotency_key or self._idempotency_key(command)
        command.command_id = command.command_id or f"cmd_{command.idempotency_key[:16]}"
        if command.idempotency_key in self._recent_keys:
            self._dead_letter(action, "duplicate", "idempotency key already accepted", command=command)
            return None
        edges = [(source_id, target_id) for target_id in target_ids if source_id != target_id]
        seen_edges = self._chain_edges.get(command.chain_id, set())
        repeated = next((edge for edge in edges if edge in seen_edges), None)
        if repeated is not None:
            self._dead_letter(action, "loop_detected", f"directed edge {repeated[0]} -> {repeated[1]} already exists in chain", target=repeated[1], command=command)
            return None
        self._remember_key(command.idempotency_key)
        if command.chain_id not in self._chain_edges:
            self._chain_edges[command.chain_id] = set()
            self._chain_order.append(command.chain_id)
            while len(self._chain_order) > CHAIN_EDGE_LIMIT:
                old_chain = self._chain_order.popleft()
                self._chain_edges.pop(old_chain, None)
        self._chain_edges[command.chain_id].update(edges)
        return command

    def reject_command(self, command: RouterCommand, status: str, reason: str) -> None:
        action = command.source_action or AgentAction(agent_id=command.source_agent_id)
        self._dead_letter(action, status, reason, command=command)

    def _dead_letter(self, action: AgentAction, status: str, reason: str, *, target: str = "", command: RouterCommand | None = None) -> None:
        metadata = action.metadata or {}
        self.dead_letters.append({
            "dead_letter_id": f"dlq_{uuid.uuid4().hex[:10]}", "status": status, "reason": reason[:500],
            "intent_id": str(metadata.get("intent_id", "")),
            "chain_id": command.chain_id if command else str(metadata.get("chain_id", "")),
            "source_agent_id": action.agent_id, "action_type": action.action,
            "command_type": command.command_type.value if command else "",
            "target": target or (command.target if command else ""), "targets": list(command.targets) if command else [],
            "hop_count": command.hop_count if command else int(metadata.get("hop_count", 0) or 0),
            "schema_version": ROUTER_SCHEMA_VERSION,
        })

    def _remember_key(self, key: str) -> None:
        self._recent_keys.add(key); self._recent_key_order.append(key)
        while len(self._recent_key_order) > RECENT_IDEMPOTENCY_LIMIT:
            old_key = self._recent_key_order.popleft(); self._recent_keys.discard(old_key)

    @staticmethod
    def _idempotency_key(command: RouterCommand) -> str:
        material = {"chain_id": command.chain_id, "source": command.source_agent_id, "intent_id": command.intent_id,
                    "type": command.command_type.value, "target": command.target, "targets": sorted(command.targets),
                    "capability_id": command.payload.get("capability_id", ""),
                    "invocation_id": command.payload.get("invocation_id", "")}
        return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _target_hint(metadata: dict[str, Any]) -> str:
        return str(metadata.get("target_id") or metadata.get("target_hint") or metadata.get("target") or metadata.get("recipient") or "").strip()

    @staticmethod
    def _transport_urgency(value: Any) -> str:
        normalized = str(value or "normal").lower()
        if normalized in {"critical", "urgent"}: return "urgent"
        if normalized == "express": return "express"
        return "normal"

    @staticmethod
    def _chunks(items: list[AgentAction], size: int) -> Iterable[list[AgentAction]]:
        for start in range(0, len(items), size):
            yield items[start:start + size]

    def update_time_scale(self, seconds_per_day: float) -> None:
        self.seconds_per_day = seconds_per_day
