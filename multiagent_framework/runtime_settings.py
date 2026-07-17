"""全局运行时设置加载、校验与中文字段说明。"""

from __future__ import annotations

import json
import math
import os
import tempfile
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


RUNTIME_SETTINGS_PATH = "config/runtime.json"


class RuntimeSettingsError(ValueError):
    """运行时设置格式或取值不合法。"""


@dataclass
class SimulationSettings:
    seconds_per_day: float = 600.0
    tick_interval: float = 0.25


@dataclass
class AgentDefaultSettings:
    max_memory: int = 20
    compress_threshold: int = 40
    keep_recent: int = 20
    max_rounds: int = 4
    max_tool_calls: int = 12
    max_turn_tokens: int = 12_000
    max_turn_seconds: float = 60.0
    no_progress_rounds: int = 2
    structured_memory_max_records: int = 500
    structured_memory_retrieval_limit: int = 8
    structured_memory_char_budget: int = 2400
    structured_memory_min_query_overlap: float = 0.08
    structured_memory_unrelated_commitment_limit: int = 1
    structured_memory_duplicate_overlap_threshold: float = 0.85
    structured_memory_kind_diversity_bonus: float = 0.6
    knowledge_context_max_records: int = 24
    knowledge_context_char_budget: int = 6000
    derived_metric_context_max_records: int = 16
    derived_metric_context_char_budget: int = 4000


@dataclass
class ActionAgentSettings:
    max_rounds: int = 10
    max_tool_calls: int = 40
    max_turn_tokens: int = 50_000
    max_turn_seconds: float = 120.0
    no_progress_rounds: int = 2


@dataclass
class MemorySettings:
    compression_temperature: float = 0.3


@dataclass
class RuntimeSettings:
    simulation: SimulationSettings = field(default_factory=SimulationSettings)
    agent_defaults: AgentDefaultSettings = field(default_factory=AgentDefaultSettings)
    action_agent: ActionAgentSettings = field(default_factory=ActionAgentSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    source_path: str = RUNTIME_SETTINGS_PATH

    @classmethod
    def from_file(cls, path: str = RUNTIME_SETTINGS_PATH) -> "RuntimeSettings":
        target = Path(path)
        if not target.exists():
            settings = cls(source_path=path)
            legacy = target.parent / "time.json"
            if legacy.exists():
                with legacy.open(encoding="utf-8-sig") as handle:
                    time_data = json.load(handle)
                settings.simulation = SimulationSettings(
                    seconds_per_day=_number(
                        time_data.get("seconds_per_day", settings.simulation.seconds_per_day),
                        "simulation.seconds_per_day", 1.0, 604800.0,
                    ),
                    tick_interval=_number(
                        time_data.get("tick_interval", settings.simulation.tick_interval),
                        "simulation.tick_interval", 0.05, 10.0,
                    ),
                )
            return settings
        with target.open(encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return cls.from_dict(data, source_path=path)

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any] | None, *, source_path: str = RUNTIME_SETTINGS_PATH
    ) -> "RuntimeSettings":
        if data is None:
            data = {}
        if not isinstance(data, Mapping):
            raise RuntimeSettingsError("运行时设置必须是 JSON 对象")
        defaults = cls(source_path=source_path)
        sim = _section(data, "simulation")
        agents = _section(data, "agent_defaults")
        action = _section(data, "action_agent")
        memory = _section(data, "memory")

        settings = cls(
            simulation=SimulationSettings(
                seconds_per_day=_number(
                    sim.get("seconds_per_day", defaults.simulation.seconds_per_day),
                    "simulation.seconds_per_day", 1.0, 604800.0,
                ),
                tick_interval=_number(
                    sim.get("tick_interval", defaults.simulation.tick_interval),
                    "simulation.tick_interval", 0.05, 10.0,
                ),
            ),
            agent_defaults=AgentDefaultSettings(
                max_memory=_integer(agents.get("max_memory", 20), "agent_defaults.max_memory", 1, 5000),
                compress_threshold=_integer(agents.get("compress_threshold", 40), "agent_defaults.compress_threshold", 2, 10000),
                keep_recent=_integer(agents.get("keep_recent", 20), "agent_defaults.keep_recent", 1, 5000),
                max_rounds=_integer(agents.get("max_rounds", 4), "agent_defaults.max_rounds", 1, 64),
                max_tool_calls=_integer(agents.get("max_tool_calls", 12), "agent_defaults.max_tool_calls", 1, 512),
                max_turn_tokens=_integer(agents.get("max_turn_tokens", 12000), "agent_defaults.max_turn_tokens", 256, 2_000_000),
                max_turn_seconds=_number(agents.get("max_turn_seconds", 60.0), "agent_defaults.max_turn_seconds", 0.1, 3600.0),
                no_progress_rounds=_integer(agents.get("no_progress_rounds", 2), "agent_defaults.no_progress_rounds", 1, 32),
                structured_memory_max_records=_integer(agents.get("structured_memory_max_records", 500), "agent_defaults.structured_memory_max_records", 10, 100000),
                structured_memory_retrieval_limit=_integer(agents.get("structured_memory_retrieval_limit", 8), "agent_defaults.structured_memory_retrieval_limit", 1, 256),
                structured_memory_char_budget=_integer(agents.get("structured_memory_char_budget", 2400), "agent_defaults.structured_memory_char_budget", 128, 200000),
                structured_memory_min_query_overlap=_number(agents.get("structured_memory_min_query_overlap", 0.08), "agent_defaults.structured_memory_min_query_overlap", 0.0, 1.0),
                structured_memory_unrelated_commitment_limit=_integer(agents.get("structured_memory_unrelated_commitment_limit", 1), "agent_defaults.structured_memory_unrelated_commitment_limit", 0, 16),
                structured_memory_duplicate_overlap_threshold=_number(agents.get("structured_memory_duplicate_overlap_threshold", 0.85), "agent_defaults.structured_memory_duplicate_overlap_threshold", 0.5, 1.0),
                structured_memory_kind_diversity_bonus=_number(agents.get("structured_memory_kind_diversity_bonus", 0.6), "agent_defaults.structured_memory_kind_diversity_bonus", 0.0, 4.0),
                knowledge_context_max_records=_integer(agents.get("knowledge_context_max_records", 24), "agent_defaults.knowledge_context_max_records", 1, 256),
                knowledge_context_char_budget=_integer(agents.get("knowledge_context_char_budget", 6000), "agent_defaults.knowledge_context_char_budget", 128, 200000),
                derived_metric_context_max_records=_integer(agents.get("derived_metric_context_max_records", 16), "agent_defaults.derived_metric_context_max_records", 0, 64),
                derived_metric_context_char_budget=_integer(agents.get("derived_metric_context_char_budget", 4000), "agent_defaults.derived_metric_context_char_budget", 0, 16000),
            ),
            action_agent=ActionAgentSettings(
                max_rounds=_integer(action.get("max_rounds", 10), "action_agent.max_rounds", 1, 64),
                max_tool_calls=_integer(action.get("max_tool_calls", 40), "action_agent.max_tool_calls", 1, 512),
                max_turn_tokens=_integer(action.get("max_turn_tokens", 50000), "action_agent.max_turn_tokens", 256, 2_000_000),
                max_turn_seconds=_number(action.get("max_turn_seconds", 120.0), "action_agent.max_turn_seconds", 0.1, 3600.0),
                no_progress_rounds=_integer(action.get("no_progress_rounds", 2), "action_agent.no_progress_rounds", 1, 32),
            ),
            memory=MemorySettings(
                compression_temperature=_number(
                    memory.get("compression_temperature", 0.3),
                    "memory.compression_temperature", 0.0, 2.0,
                )
            ),
            source_path=source_path,
        )
        if settings.agent_defaults.keep_recent >= settings.agent_defaults.compress_threshold:
            raise RuntimeSettingsError(
                "agent_defaults.keep_recent 必须小于 compress_threshold，否则无法形成可压缩的历史区间"
            )
        if settings.agent_defaults.no_progress_rounds > settings.agent_defaults.max_rounds:
            raise RuntimeSettingsError(
                "agent_defaults.no_progress_rounds 不能大于 max_rounds"
            )
        if settings.action_agent.no_progress_rounds > settings.action_agent.max_rounds:
            raise RuntimeSettingsError(
                "action_agent.no_progress_rounds 不能大于 max_rounds"
            )
        return settings

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation": asdict(self.simulation),
            "agent_defaults": asdict(self.agent_defaults),
            "action_agent": asdict(self.action_agent),
            "memory": asdict(self.memory),
        }

    def save_to_file(self, path: str | None = None) -> None:
        atomic_write_json(path or self.source_path, self.to_dict())


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise RuntimeSettingsError(f"{name} 必须是 JSON 对象")
    return value


def _number(value: Any, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise RuntimeSettingsError(f"{path} 必须是数字")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeSettingsError(f"{path} 必须是数字") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise RuntimeSettingsError(f"{path} 必须在 {minimum:g} 到 {maximum:g} 之间")
    return result


def _integer(value: Any, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise RuntimeSettingsError(f"{path} 必须是整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeSettingsError(f"{path} 必须是整数") from exc
    if str(value).strip() not in {str(result), f"{result}.0"} and not isinstance(value, int):
        try:
            if float(value) != result:
                raise RuntimeSettingsError(f"{path} 必须是整数")
        except (TypeError, ValueError) as exc:
            raise RuntimeSettingsError(f"{path} 必须是整数") from exc
    if not minimum <= result <= maximum:
        raise RuntimeSettingsError(f"{path} 必须在 {minimum} 到 {maximum} 之间")
    return result


def atomic_write_json(path: str | Path, data: Mapping[str, Any]) -> None:
    """同目录临时文件 + os.replace，避免中途退出写坏配置。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", delete=False,
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp",
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def merge_known_model_config(
    previous: Mapping[str, Any] | None, known: Mapping[str, Any]
) -> dict[str, Any]:
    """替换已知配置，同时保留未来版本增加的未知字段。"""
    old = deepcopy(dict(previous or {}))
    result = {key: deepcopy(value) for key, value in old.items() if key not in {
        "providers", "models", "task_policies", "dlc_task_policies", "agent_task_policies"
    }}
    for section in ("task_policies", "dlc_task_policies", "agent_task_policies"):
        result[section] = deepcopy(known.get(section, {}))
    for section, known_fields in (
        ("providers", {"base_url", "api_key", "api_key_env", "timeout_s", "retry_delay_s", "max_concurrent", "queue_interval_s"}),
        ("models", {"provider", "model", "capabilities", "max_concurrent", "max_retries"}),
    ):
        output: dict[str, Any] = {}
        old_section = old.get(section, {}) if isinstance(old.get(section), Mapping) else {}
        for name, current in dict(known.get(section, {})).items():
            previous_entry = old_section.get(name, {})
            extras = {
                key: deepcopy(value)
                for key, value in dict(previous_entry).items()
                if key not in known_fields
            } if isinstance(previous_entry, Mapping) else {}
            extras.update(deepcopy(current))
            output[name] = extras
        result[section] = output
    return result


def settings_schema() -> dict[str, Any]:
    """前端设置中心使用的中文说明、范围与安全元数据。"""
    def field_meta(path: str, label: str, description: str, kind: str = "number", **extra: Any) -> dict[str, Any]:
        return {"path": path, "label": label, "description": description, "type": kind, **extra}

    return {
        "schema_version": "1.0",
        "categories": [
            {"id": "overview", "label": "配置总览", "description": "查看配置来源、热重载代次和安全状态。"},
            {"id": "providers", "label": "接口服务商", "description": "管理 OpenAI 兼容接口地址、本地明文 API Key 和超时。"},
            {"id": "models", "label": "模型 Profile", "description": "为速度、推理、工具能力等任务建立可复用模型档案。"},
            {"id": "routing", "label": "任务模型路由", "description": "配置全局、DLC 和 Agent 三级候选模型及故障回退顺序。"},
            {"id": "turns", "label": "Agent 多轮调用", "description": "控制普通 Agent 与世界执行 Agent 的轮次、工具、Token 和时间预算。"},
            {"id": "memory", "label": "多轮记忆", "description": "控制短期对话压缩与结构化长期记忆召回。"},
            {"id": "simulation", "label": "世界模拟", "description": "控制游戏时间倍率和世界主循环刷新间隔。"},
            {"id": "security", "label": "安全与运维", "description": "查看管理接口、指标接口、归档签名与告警阈值环境配置。"},
            {"id": "advanced", "label": "高级 JSON", "description": "直接编辑模型网关 JSON，并在保存前执行同一套后端校验。"},
        ],
        "fields": [
            field_meta("providers.*.base_url", "接口地址", "OpenAI 兼容 API 的根地址，必须使用 http 或 https。", "url", required=True, hot_reload=True),
            field_meta("providers.*.api_key", "API Key", "以明文写入本机 config/models.json，保存后立即热更新模型客户端。", "secret", persist=True, hot_reload=True),
            field_meta("providers.*.timeout_s", "请求超时（秒）", "单次模型 HTTP 请求允许等待的最长时间。", minimum=1, maximum=3600, hot_reload=True),
            field_meta("providers.*.retry_delay_s", "失败重试冷却（秒）", "API 请求或上游响应失败后，发起下一次重试前固定等待的时间；设为 0 可关闭等待。", minimum=0, maximum=3600, step=0.5, hot_reload=True),
            field_meta("providers.*.max_concurrent", "Provider 共享并发上限", "该 Provider 下所有模型 Profile 合计最多同时发出的 HTTP 请求数，避免各 Profile 的并发数叠加压垮上游。不确定时建议设为 1–2。", minimum=1, maximum=128, hot_reload=True),
            field_meta("providers.*.queue_interval_s", "API 出队间隔（秒）", "同一 Provider 相邻 HTTP 请求开始之间的最小停顿；失败重试也会重新排队并遵守此间隔。0 表示不主动停顿，第三方接口可先设 0.5–1 秒。", minimum=0, maximum=3600, step=0.1, hot_reload=True),
            field_meta("models.*.provider", "接口服务商", "该 Profile 使用哪个接口服务商。", "select", required=True, hot_reload=True),
            field_meta("models.*.model", "模型标识", "发送给兼容 API 的实际模型 ID。", "string", required=True, hot_reload=True),
            field_meta("models.*.capabilities", "能力标签", "用于声明工具调用、JSON、流式输出、长上下文等能力；工具任务会优先要求 tools/function_calling。", "multiselect", hot_reload=True),
            field_meta("models.*.max_concurrent", "Profile 最大并发", "只限制该 Profile 的逻辑调用并发；实际 HTTP 请求还会受 Provider 共享并发上限和出队间隔约束。", minimum=1, maximum=256, hot_reload=True),
            field_meta("models.*.max_retries", "失败重试次数", "网络或服务错误后在切换到下一候选模型前的重试次数。", minimum=0, maximum=20, hot_reload=True),
            field_meta("task_policies", "全局任务路由", "候选 Profile 按从左到右顺序尝试；未命中更精确规则时使用。", "policy", hot_reload=True),
            field_meta("dlc_task_policies", "DLC 任务路由", "仅对指定 DLC 生效，优先级高于全局任务路由。", "scoped_policy", hot_reload=True),
            field_meta("agent_task_policies", "Agent 任务路由", "指定 DLC 内单个 Agent 的覆盖规则，优先级高于 DLC 与全局规则。", "agent_policy", hot_reload=True),
            field_meta("simulation.seconds_per_day", "现实多少秒等于游戏一天", "数值越小，世界时间流逝越快。修改后立即影响时间推进和路程换算。", minimum=1, maximum=604800, hot_reload=True),
            field_meta("simulation.tick_interval", "世界主循环间隔（秒）", "检查计时器、事件和广播状态的频率。过小会增加 CPU 与前端刷新压力。", minimum=0.05, maximum=10, hot_reload=True),
            field_meta("agent_defaults.max_rounds", "普通 Agent 最大轮次", "一次唤醒中允许模型观察工具结果并继续思考的最大轮数。", minimum=1, maximum=64, hot_reload=True),
            field_meta("agent_defaults.max_tool_calls", "普通 Agent 最大工具调用", "一次唤醒允许提交的工具调用总数，用于防止无限操作。", minimum=1, maximum=512, hot_reload=True),
            field_meta("agent_defaults.max_turn_tokens", "普通 Agent Token 预算", "一次完整多轮调用累计输入与输出 Token 的软上限。", minimum=256, maximum=2000000, hot_reload=True),
            field_meta("agent_defaults.max_turn_seconds", "普通 Agent 时间预算", "一次完整多轮调用允许占用的现实时间。", minimum=0.1, maximum=3600, hot_reload=True),
            field_meta("agent_defaults.no_progress_rounds", "无进展停止轮数", "连续若干轮产生相同内容和工具结果时提前停止，避免死循环。", minimum=1, maximum=32, hot_reload=True),
            field_meta("action_agent.max_rounds", "世界执行 Agent 最大轮次", "执行复杂世界动作时允许的模型推理轮数。", minimum=1, maximum=64, hot_reload=True),
            field_meta("action_agent.max_tool_calls", "世界执行 Agent 最大工具调用", "一次世界动作允许执行的底层工具总数。", minimum=1, maximum=512, hot_reload=True),
            field_meta("action_agent.max_turn_tokens", "世界执行 Agent Token 预算", "一次世界动作累计 Token 上限。", minimum=256, maximum=2000000, hot_reload=True),
            field_meta("action_agent.max_turn_seconds", "世界执行 Agent 时间预算", "一次世界动作允许持续的现实时间。", minimum=0.1, maximum=3600, hot_reload=True),
            field_meta("action_agent.no_progress_rounds", "世界执行无进展停止轮数", "连续无进展时终止动作规划。", minimum=1, maximum=32, hot_reload=True),
            field_meta("agent_defaults.compress_threshold", "短期记忆压缩阈值", "对话消息数超过该值时调用记忆压缩模型。", minimum=2, maximum=10000, hot_reload=True),
            field_meta("agent_defaults.keep_recent", "压缩后保留最近消息", "压缩旧消息时原样保留的最近消息数，必须小于压缩阈值。", minimum=1, maximum=5000, hot_reload=True),
            field_meta("agent_defaults.max_memory", "短期记忆兼容上限", "保留给旧存档和旧版 Agent 的兼容字段；新流程主要由压缩阈值控制。", minimum=1, maximum=5000, hot_reload=True),
            field_meta("agent_defaults.structured_memory_max_records", "结构化记忆最大条数", "每个 Agent 可保存的事实、关系、承诺和经历记录上限。", minimum=10, maximum=100000, hot_reload=True),
            field_meta("agent_defaults.structured_memory_retrieval_limit", "每轮召回条数", "每次模型调用最多注入多少条相关结构化记忆。", minimum=1, maximum=256, hot_reload=True),
            field_meta("agent_defaults.structured_memory_char_budget", "召回字符预算", "注入提示词的结构化记忆最大字符数。", minimum=128, maximum=200000, hot_reload=True),
            field_meta("agent_defaults.structured_memory_min_query_overlap", "主题最小词元重合率", "当前回合有查询文本时，普通记忆至少命中该比例的中英文词元，或命中实体与元数据，才进入候选；设为 0 可关闭文本相关性门槛。", minimum=0, maximum=1, hot_reload=True),
            field_meta("agent_defaults.structured_memory_unrelated_commitment_limit", "无关未结承诺保底条数", "当前主题无关但尚未结案的承诺，每轮最多额外保留多少条；设为 0 完全关闭保底，过高会稀释当前任务。", minimum=0, maximum=16, hot_reload=True),
            field_meta("agent_defaults.structured_memory_duplicate_overlap_threshold", "近重复抑制阈值", "两条候选记忆的词元集合重合达到该比例时只保留更优一条；较低值去重更严格，1 仅抑制词元完全相同的内容。", minimum=0.5, maximum=1, hot_reload=True),
            field_meta("agent_defaults.structured_memory_kind_diversity_bonus", "记忆类型多样性加分", "召回结果尚未包含某类经历、事实、关系或承诺时给予的边际加分；设为 0 关闭类型多样性偏好。", minimum=0, maximum=4, hot_reload=True),
            field_meta("agent_defaults.knowledge_context_max_records", "知识上下文最大条数", "每轮 Agent 调用最多注入多少条该 Agent 可知的事实与信念；只读取对应持有者的知识视图。", minimum=1, maximum=256, hot_reload=True),
            field_meta("agent_defaults.knowledge_context_char_budget", "知识上下文字符预算", "限制知识与信念摘要占用的 Prompt 长度，避免隐藏知识外泄并控制 Token 消耗。", minimum=128, maximum=200000, hot_reload=True),
            field_meta("agent_defaults.derived_metric_context_max_records", "派生指标上下文最大条数", "每轮 Agent 调用最多注入多少条当前世界的只读派生指标；设为 0 可关闭注入，过大会增加 Token 消耗。", minimum=0, maximum=64, hot_reload=True),
            field_meta("agent_defaults.derived_metric_context_char_budget", "派生指标上下文字符预算", "限制派生指标摘要占用的 Prompt 长度；超出后按稳定顺序停止追加，不会注入公式或元数据。", minimum=0, maximum=16000, hot_reload=True),
            field_meta("memory.compression_temperature", "记忆压缩温度", "摘要模型的随机性；较低值更稳定，较高值更灵活。", minimum=0, maximum=2, hot_reload=True),
        ],
        "task_types": [
            {"id": "router.simple", "label": "路由：简单意图", "description": "可确定性解析的简单路由任务。"},
            {"id": "router.ambiguous", "label": "路由：歧义意图", "description": "需要模型判断目标和策略的复杂路由任务。"},
            {"id": "agent.turn", "label": "Agent 回合", "description": "普通官员、机构或 DLC Agent 的多轮处理。"},
            {"id": "action.simulation", "label": "世界动作模拟", "description": "把意图转换为真实世界状态修改。"},
            {"id": "memory.compress", "label": "记忆压缩", "description": "将旧对话整理成长期摘要。"},
            {"id": "tianji.chat", "label": "天玑对话", "description": "调试与管理 AI 对话。"},
            {"id": "default", "label": "默认回退", "description": "所有更精确规则均未命中时使用。"},
        ],
        "capabilities": [
            {"id": "tools", "label": "工具调用"},
            {"id": "function_calling", "label": "函数调用"},
            {"id": "json", "label": "JSON 输出"},
            {"id": "stream", "label": "流式输出"},
            {"id": "long_context", "label": "长上下文"},
            {"id": "reasoning", "label": "推理优化"},
        ],
        "routing_priority": [
            "调用时显式指定的 Profile",
            "DLC Agent 清单中的模型绑定",
            "Agent 级任务路由 agent_task_policies",
            "DLC 级任务路由 dlc_task_policies",
            "全局任务路由 task_policies",
            "default 回退规则",
            "配置中的全部模型 Profile",
        ],
    }
