"""Static seven-stage playability audit for authored DLC bundles.

The audit is intentionally independent from registry validation. Validation answers
"is this document legal?" while this module answers "does the document declare a
credible player loop?". A real integration playthrough remains the stronger proof;
the report gives DLC Studio authors deterministic, Chinese gap diagnostics before
running that playthrough.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping


_AUDIT_VERSION = "1.0"


@dataclass(frozen=True)
class PlayabilityEvidence:
    kind: str
    item_id: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PlayabilityCheck:
    check_id: str
    title: str
    passed: bool
    summary: str
    gaps: tuple[str, ...] = ()
    evidence: tuple[PlayabilityEvidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "passed": self.passed,
            "status": "pass" if self.passed else "gap",
            "summary": self.summary,
            "gaps": list(self.gaps),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class DLCPlayabilityReport:
    dlc_id: str
    audit_version: str
    playable: bool
    passed_count: int
    total_count: int
    checks: tuple[PlayabilityCheck, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dlc_id": self.dlc_id,
            "audit_version": self.audit_version,
            "playable": self.playable,
            "passed_count": self.passed_count,
            "total_count": self.total_count,
            "checks": [item.to_dict() for item in self.checks],
        }


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _enabled(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in rows if bool(item.get("enabled", True))]


def _identifier(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(item.get(key, "")).strip()
        if value:
            return value
    return ""


def _label(item: Mapping[str, Any], item_id: str) -> str:
    return str(item.get("display_name") or item.get("name") or item.get("title") or item_id)


def _evidence(kind: str, item: Mapping[str, Any], *keys: str) -> PlayabilityEvidence:
    item_id = _identifier(item, *keys)
    return PlayabilityEvidence(kind=kind, item_id=item_id, label=_label(item, item_id))


def _result_nodes(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, Mapping):
        return []
    row = dict(result)
    nodes = [row]
    if str(row.get("mode", "")) == "composite":
        for child in row.get("results", []):
            nodes.extend(_result_nodes(child))
    return nodes


def _player_recipient_ids(content: Mapping[str, Any]) -> set[str]:
    gameplay = content.get("gameplay", {})
    player = gameplay.get("player", {}) if isinstance(gameplay, Mapping) else {}
    if not isinstance(player, Mapping):
        return set()
    recipients = {
        str(item.get("id", "")).strip()
        for item in player.get("controlled_objects", [])
        if isinstance(item, Mapping)
        and str(item.get("kind", "")) == "agent"
        and str(item.get("mode", "direct")) == "direct"
        and str(item.get("id", "")).strip()
    }
    actor_id = str(player.get("actor_id", "")).strip()
    identity_id = str(player.get("identity_id", "")).strip()
    for agent in _rows(content.get("agents", [])):
        agent_id = str(agent.get("id", "")).strip()
        if agent_id and (
            (actor_id and str(agent.get("actor_id", "")).strip() == actor_id)
            or (identity_id and agent_id == identity_id)
        ):
            recipients.add(agent_id)
    return recipients


def _event_ingress_rows(content: Mapping[str, Any], player_ids: set[str]) -> list[PlayabilityEvidence]:
    evidence: list[PlayabilityEvidence] = []
    for key, kind in (("initial_events", "initial_event"), ("random_events", "random_event")):
        for row in _enabled(_rows(content.get(key, []))):
            targets = {str(item) for item in row.get("target_agents", [])}
            payload = row.get("initial_payload", {})
            if not targets.intersection(player_ids) or not isinstance(payload, Mapping):
                continue
            if payload.get("issue_type_id") or payload.get("type") or payload.get("event_type"):
                evidence.append(_evidence(kind, row, "event_id", "template_id", "event_type"))
    time_system = content.get("time_system", {})
    if isinstance(time_system, Mapping) and bool(time_system.get("enabled", False)):
        for row in _enabled(_rows(time_system.get("boundary_events", []))):
            if str(row.get("issue_type_id", "")).strip():
                evidence.append(_evidence("time_boundary", row, "boundary_event_id", "event_type"))
    for row in _enabled(_rows(content.get("causal_event_rules", []))):
        targets = {str(item) for item in row.get("target_agents", [])}
        payload = row.get("initial_payload", {})
        if targets.intersection(player_ids) and isinstance(payload, Mapping) and (
            payload.get("issue_type_id") or payload.get("type") or payload.get("event_type")
        ):
            evidence.append(_evidence("causal_issue_event", row, "rule_id", "event_type"))
    return evidence


def audit_dlc_playability(dlc_id: str, content: Mapping[str, Any]) -> DLCPlayabilityReport:
    """Return deterministic evidence for the seven generic gameplay-loop stages."""

    actions = _enabled(_rows(content.get("actions", [])))
    action_by_id = {
        _identifier(item, "action_id"): item for item in actions
        if _identifier(item, "action_id")
    }
    agents = _enabled(_rows(content.get("agents", [])))
    agent_ids = {_identifier(item, "id") for item in agents}
    player_ids = _player_recipient_ids(content)
    autonomous_agents = [item for item in agents if _identifier(item, "id") not in player_ids]
    issues = _enabled(_rows(content.get("issue_types", [])))
    objectives = _enabled(_rows(content.get("objectives", [])))
    endings = _enabled(_rows(content.get("endings", [])))
    ui = content.get("ui_manifest", {}) if isinstance(content.get("ui_manifest", {}), Mapping) else {}
    gameplay = content.get("gameplay", {}) if isinstance(content.get("gameplay", {}), Mapping) else {}
    player = gameplay.get("player", {}) if isinstance(gameplay.get("player", {}), Mapping) else {}
    controls = _rows(player.get("controlled_objects", []))

    checks: list[PlayabilityCheck] = []

    identity_ok = bool(str(player.get("identity_id", "")).strip() and controls)
    ui_ok = bool(str(ui.get("title", "")).strip() and (_rows(ui.get("navigation", [])) or ui.get("dashboard")))
    identity_evidence: list[PlayabilityEvidence] = []
    if identity_ok:
        identity_evidence.append(PlayabilityEvidence("player", str(player.get("identity_id")), str(player.get("display_name") or player.get("identity_id"))))
    if ui_ok:
        identity_evidence.append(PlayabilityEvidence("ui_manifest", "ui_manifest", str(ui.get("title"))))
    identity_gaps = []
    if not identity_ok:
        identity_gaps.append("配置 gameplay.player.identity_id，并至少声明一个 controlled_objects 控制对象。")
    if not ui_ok:
        identity_gaps.append("配置 ui_manifest.title，并声明导航或仪表盘。")
    checks.append(PlayabilityCheck(
        "player_surface", "1. 玩家身份与主界面", identity_ok and ui_ok,
        "玩家身份、控制边界和 DLC 主界面均已声明。" if identity_ok and ui_ok else "玩家入口尚未形成完整可操作界面。",
        tuple(identity_gaps), tuple(identity_evidence),
    ))

    player_action_ok = bool(actions and controls)
    agent_action_evidence: list[PlayabilityEvidence] = []
    for action in actions:
        for node in _result_nodes(action.get("result", {})):
            if str(node.get("mode", "")) == "wake_agent" and str(node.get("agent_id", "")).strip() in agent_ids:
                agent_action_evidence.append(_evidence("agent_dispatch", action, "action_id"))
                break
    for capability in _enabled(_rows(content.get("capabilities", []))):
        if "agent" in {str(item) for item in capability.get("audiences", [])}:
            agent_action_evidence.append(_evidence("agent_capability", capability, "capability_id"))
    agent_action_ok = bool(autonomous_agents and agent_action_evidence)
    action_evidence = [_evidence("player_action", item, "action_id") for item in actions[:4]]
    action_evidence.extend(agent_action_evidence[:4])
    action_gaps = []
    if not player_action_ok:
        action_gaps.append("至少声明一个启用行动，并让玩家拥有可执行该行动的控制对象。")
    if not agent_action_ok:
        action_gaps.append("至少声明一个非玩家 Agent，并通过 wake_agent 或 agent audience capability 给出 Agent 行动入口。")
    checks.append(PlayabilityCheck(
        "action_roles", "2. 玩家与 Agent 行动", player_action_ok and agent_action_ok,
        "玩家行动和 Agent 执行入口均可由 DLC 配置解析。" if player_action_ok and agent_action_ok else "玩家或 Agent 的行动入口仍有缺口。",
        tuple(action_gaps), tuple(action_evidence),
    ))

    issue_ingress = _event_ingress_rows(content, player_ids)
    issue_ok = bool(issues and issue_ingress)
    issue_gaps = []
    if not issues:
        issue_gaps.append("至少声明一个启用的 issue_types。")
    if not issue_ingress:
        issue_gaps.append("通过初始事件、随机事件、时间边界或因果事件，把带有 Issue 类型信息的事件投递给玩家 Agent。")
    issue_evidence = [_evidence("issue_type", item, "issue_type_id") for item in issues[:4]] + issue_ingress[:4]
    checks.append(PlayabilityCheck(
        "issue_intake", "3. 世界或 Agent 上报形成 Issue", issue_ok,
        "已找到从世界/Agent 事件进入玩家 Issue 队列的声明路径。" if issue_ok else "尚未找到可静态追踪的玩家 Issue 入口。",
        tuple(issue_gaps), tuple(issue_evidence),
    ))

    decision_paths: list[PlayabilityEvidence] = []
    costly_actions: set[str] = set()
    limited_information = False
    for issue in issues:
        limited_information = limited_information or any(
            bool(item.get("required", False)) for item in _rows(issue.get("evidence", []))
        )
        for request in _rows(issue.get("decision_requests", [])):
            for option in _rows(request.get("options", [])):
                action_id = str(option.get("action_id", "")).strip()
                action = action_by_id.get(action_id)
                if not action:
                    continue
                if _rows(action.get("costs", [])):
                    costly_actions.add(action_id)
                    decision_paths.append(PlayabilityEvidence("decision_action", action_id, str(option.get("display_name") or action_id)))
    decision_ok = bool(decision_paths and limited_information)
    decision_gaps = []
    if not decision_paths:
        decision_gaps.append("让至少一个决策选项调用带有 costs 的 DLC 行动。")
    if not limited_information:
        decision_gaps.append("在 Issue 中声明至少一项 required evidence，明确玩家基于有限信息决策。")
    checks.append(PlayabilityCheck(
        "costly_decision", "4. 有限信息、决策与成本", decision_ok,
        "Issue 证据约束、决策选项和资源成本已经连成一条路径。" if decision_ok else "决策尚未同时具备信息约束和可验证成本。",
        tuple(decision_gaps), tuple(decision_paths[:6]),
    ))

    world_actions: list[PlayabilityEvidence] = []
    time_actions: list[PlayabilityEvidence] = []
    agent_actions: list[PlayabilityEvidence] = []
    emitted_types: set[str] = set()
    for action in actions:
        nodes = _result_nodes(action.get("result", {}))
        if any(str(node.get("mode", "")) in {"world_commands", "ongoing_event"} for node in nodes):
            world_actions.append(_evidence("world_change", action, "action_id"))
        duration = action.get("duration", {})
        if isinstance(duration, Mapping) and duration:
            time_actions.append(_evidence("time_cost", action, "action_id"))
        if any(str(node.get("mode", "")) == "ongoing_event" for node in nodes):
            if not any(item.item_id == _identifier(action, "action_id") for item in time_actions):
                time_actions.append(_evidence("ongoing_event", action, "action_id"))
        if any(str(node.get("mode", "")) == "wake_agent" for node in nodes):
            agent_actions.append(_evidence("agent_dispatch", action, "action_id"))
        for node in nodes:
            if str(node.get("mode", "")) != "emit_event":
                continue
            event = node.get("event", {})
            if isinstance(event, Mapping) and str(event.get("event_type", "")).strip():
                emitted_types.add(str(event.get("event_type")).strip())
    causal_rules: list[PlayabilityEvidence] = []
    for rule in _enabled(_rows(content.get("causal_event_rules", []))):
        source_types = {str(item) for item in rule.get("source_event_types", [])}
        if emitted_types.intersection(source_types) and _rows(rule.get("world_effects", [])):
            causal_rules.append(_evidence("causal_rule", rule, "rule_id"))
    simulation_ok = bool(world_actions and time_actions and agent_actions and causal_rules)
    simulation_gaps = []
    if not world_actions:
        simulation_gaps.append("行动结果需要 world_commands 或 ongoing_event 来改变世界。")
    if not time_actions:
        simulation_gaps.append("行动需要声明 duration 或 ongoing_event 耗时。")
    if not agent_actions:
        simulation_gaps.append("行动结果需要 wake_agent，把玩家命令交给 Agent。")
    if not causal_rules:
        simulation_gaps.append("行动 emit_event 后，需要一条匹配 source_event_types 且包含 world_effects 的因果规则。")
    simulation_evidence = (world_actions + time_actions + agent_actions + causal_rules)[:8]
    checks.append(PlayabilityCheck(
        "world_simulation", "5. Agent、时间与因果规则改变世界", simulation_ok,
        "行动已跨越 Agent 派发、耗时、世界命令与因果规则。" if simulation_ok else "行动到世界结果的模拟链路尚不完整。",
        tuple(simulation_gaps), tuple(simulation_evidence),
    ))

    feedback = content.get("outcome_feedback", {})
    feedback_enabled = isinstance(feedback, Mapping) and bool(feedback.get("enabled", False))
    immediate = feedback.get("immediate", {}) if isinstance(feedback, Mapping) else {}
    rules = _enabled(_rows(feedback.get("rules", []))) if isinstance(feedback, Mapping) else []
    meaningful_rules = [
        item for item in rules
        if {str(value) for value in item.get("source_kinds", [])}.intersection({"action", "decision", "causal_event", "case_closed"})
    ]
    feedback_ok = bool(feedback_enabled and isinstance(immediate, Mapping) and bool(immediate.get("enabled", False)) and meaningful_rules)
    feedback_gaps = []
    if not feedback_enabled:
        feedback_gaps.append("启用 outcome_feedback。")
    if not isinstance(immediate, Mapping) or not bool(immediate.get("enabled", False)):
        feedback_gaps.append("启用 outcome_feedback.immediate，即时展示可理解变化。")
    if not meaningful_rules:
        feedback_gaps.append("至少声明一条 action、decision、causal_event 或 case_closed 来源的反馈规则。")
    checks.append(PlayabilityCheck(
        "outcome_feedback", "6. 玩家收到可理解后果反馈", feedback_ok,
        "即时反馈与规则化后续结果均已声明。" if feedback_ok else "后果反馈尚未覆盖玩家行动或因果结果。",
        tuple(feedback_gaps), tuple(_evidence("feedback_rule", item, "rule_id") for item in meaningful_rules[:6]),
    ))

    configured_campaign = bool(objectives and endings)
    time_system = content.get("time_system", {})
    cycles = _enabled(_rows(time_system.get("cycles", []))) if isinstance(time_system, Mapping) and bool(time_system.get("enabled", False)) else []
    sandbox = not objectives and not endings
    progression_ok = configured_campaign or (sandbox and bool(cycles))
    progression_gaps = []
    if not progression_ok:
        progression_gaps.append("配置目标与结局；若是沙盒 DLC，则至少配置一个启用的时间周期维持进程。")
    progression_evidence = [_evidence("objective", item, "objective_id") for item in objectives[:4]]
    progression_evidence.extend(_evidence("ending", item, "ending_id") for item in endings[:4])
    if sandbox:
        progression_evidence.extend(_evidence("sandbox_cycle", item, "cycle_id") for item in cycles[:4])
    checks.append(PlayabilityCheck(
        "progression", "7. 目标、失败条件或沙盒进程持续推进", progression_ok,
        "目标/结局或沙盒时间进程已经建立。" if progression_ok else "DLC 尚未声明可持续推进方式。",
        tuple(progression_gaps), tuple(progression_evidence),
    ))

    passed_count = sum(1 for item in checks if item.passed)
    return DLCPlayabilityReport(
        dlc_id=str(dlc_id), audit_version=_AUDIT_VERSION,
        playable=passed_count == len(checks), passed_count=passed_count,
        total_count=len(checks), checks=tuple(checks),
    )
