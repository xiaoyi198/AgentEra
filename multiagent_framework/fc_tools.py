"""所有 FC（Function Call）工具定义的中央注册表.

各 Agent 按需 import 所需工具集：
  from .fc_tools import ACTION_AGENT_TOOLS
  from .fc_tools import ROUTER_TOOLS
  from .fc_tools import AGENT_TOOLS
  from .fc_tools import ALL_TOOLS   # 天玑 / 调试用
"""

# ── 世界状态工具 ─────────────────────────────────────────────────────────────

LIST_ENTITIES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_entities",
        "description": "列出当前世界中所有实体的 ID、类型和名称。可按类型筛选。用于在操作实体前确认合法 ID。",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": ["location", "institution", "organization"],
                    "description": "（可选）按类型筛选",
                },
            },
            "required": [],
        },
    },
}

QUERY_ENTITY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_entity",
        "description": (
            "一次性查询实体的全部状态：基本情况、当前状况、库存（含资源代币与核心参数）、"
            "以及正在发生的事情列表（每条含 event_id、描述、剩余时间、预期结果）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "实体 ID"},
            },
            "required": ["entity_id"],
        },
    },
}

UPDATE_ENTITY_STATUS_TOOL = {
    "type": "function",
    "function": {
        "name": "update_entity_status",
        "description": "更新一个实体的当前状况（current_status 字段），用一句话描述该实体目前发生了什么。",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "实体 ID"},
                "new_status": {"type": "string", "description": "新的当前状况描述"},
            },
            "required": ["entity_id", "new_status"],
        },
    },
}

UPDATE_INVENTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "update_inventory",
        "description": "增减某实体的资源代币。系统只有 7 种固定资源，quantity_delta 正数为增加，负数为减少。",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "实体 ID"},
                "resource_name": {
                    "type": "string",
                    "enum": ["金钱", "粮草", "工料", "科技点", "兵力", "满意度", "人口",
                             "生活质量", "税率", "生产系数"],
                    "description": "资源代币或核心参数名称",
                },
                "quantity_delta": {"type": "number", "description": "变化量，正增负减"},
                "reason": {"type": "string", "description": "变动原因（用于日志）"},
            },
            "required": ["entity_id", "resource_name", "quantity_delta"],
        },
    },
}


PRODUCE_RESOURCE_TOOL = {
    "type": "function",
    "function": {
        "name": "produce_resource",
        "description": (
            "为实体生产、采集或征收正数量的资产资源。"
            "资源必须在 DLC 目录中声明为 kind=asset；系统按 anchor_rate 记录统一基础价值发行。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "接收资产的实体 ID"},
                "resource_name": {"type": "string", "description": "DLC 资源目录中的资源名称"},
                "quantity": {"type": "number", "exclusiveMinimum": 0, "description": "生产数量，必须大于 0"},
                "reason": {"type": "string", "description": "生产、采集或征收原因"},
                "expected_version": {"type": "integer", "minimum": 0, "description": "可选的实体乐观锁版本"},
            },
            "required": ["entity_id", "resource_name", "quantity", "reason"],
        },
    },
}

CONSUME_RESOURCE_TOOL = {
    "type": "function",
    "function": {
        "name": "consume_resource",
        "description": (
            "从实体库存消费、损失或销毁正数量的资产资源；余额不足时明确失败，不会静默归零。"
            "系统按 anchor_rate 记录统一基础价值销毁。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "扣减资产的实体 ID"},
                "resource_name": {"type": "string", "description": "DLC 资源目录中的资源名称"},
                "quantity": {"type": "number", "exclusiveMinimum": 0, "description": "消费数量，必须大于 0"},
                "reason": {"type": "string", "description": "消费、损失或销毁原因"},
                "expected_version": {"type": "integer", "minimum": 0, "description": "可选的实体乐观锁版本"},
            },
            "required": ["entity_id", "resource_name", "quantity", "reason"],
        },
    },
}

TRANSFER_ANCHORED_VALUE_TOOL = {
    "type": "function",
    "function": {
        "name": "transfer_anchored_value",
        "description": (
            "按统一基础价值在两个实体/资产资源之间原子转移，系统依据双方 anchor_rate 换算数量。"
            "用于拨款、调粮、兑换等守恒转移；余额不足时借贷两侧整批回滚，禁止拆成独立库存更新。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_entity_id": {"type": "string", "description": "转出实体 ID"},
                "from_resource": {"type": "string", "description": "转出资产资源名称"},
                "to_entity_id": {"type": "string", "description": "转入实体 ID"},
                "to_resource": {"type": "string", "description": "转入资产资源名称"},
                "base_value": {"type": "number", "exclusiveMinimum": 0, "description": "要转移的统一基础价值，必须大于 0"},
                "reason": {"type": "string", "description": "转移原因"},
                "from_expected_version": {"type": "integer", "minimum": 0, "description": "可选的转出实体版本"},
                "to_expected_version": {"type": "integer", "minimum": 0, "description": "可选的转入实体版本"},
            },
            "required": ["from_entity_id", "from_resource", "to_entity_id", "to_resource", "base_value", "reason"],
        },
    },
}

ADD_ONGOING_EVENT_TOOL = {
    "type": "function",
    "function": {
        "name": "add_ongoing_event",
        "description": (
            "在指定实体上排队一条『正在发生的事情』，到期由系统自动落账（写入 current_status 与库存）。"
            "用于目标侧的延迟变化（如拨款 N 天后到达、赈灾 M 天后完成）。"
            "执行者自身的钱粮已经发出脱手 → 用 update_inventory 立即扣除，不要排队。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "事件归属实体 ID（通常是目标地点）"},
                "event_type": {
                    "type": "string",
                    "enum": ["state_change", "inventory_arrival"],
                    "description": "state_change=状态变化（如赈灾完成）；inventory_arrival=物资到账。仅作分类。",
                },
                "description": {
                    "type": "string",
                    "description": "事件简述，如『户部拨款 3000 两运输中』『正在开粥铺赈灾』",
                },
                "duration_days": {
                    "type": "number",
                    "description": "距离当前还需多少天才到期生效",
                },
                "expected_result": {
                    "type": "object",
                    "description": (
                        "到期时要应用的变化。可包含："
                        "new_status（字符串，覆盖 current_status）；"
                        "inventory_deltas（数组，每项 {resource, delta, reason}，对该实体增减资源）。"
                    ),
                    "properties": {
                        "new_status": {"type": "string"},
                        "inventory_deltas": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "resource": {
                                        "type": "string",
                                        "enum": ["金钱", "粮草", "工料", "科技点", "兵力",
                                                 "满意度", "人口", "生活质量", "税率", "生产系数"],
                                    },
                                    "delta": {"type": "number"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["resource", "delta"],
                            },
                        },
                    },
                },
            },
            "required": ["entity_id", "description", "duration_days", "expected_result"],
        },
    },
}

MODIFY_ONGOING_EVENT_TOOL = {
    "type": "function",
    "function": {
        "name": "modify_ongoing_event",
        "description": (
            "修改一条已存在的『正在发生的事情』。用于事件叠加：如开粥铺进行中收到拨款 → "
            "缩短 duration_days、增强 expected_result。也可改描述。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "要修改的事件 ID"},
                "description": {"type": "string", "description": "（可选）新的事件描述"},
                "duration_days": {
                    "type": "number",
                    "description": "（可选）新的剩余天数（从当前 sim_time 起算）",
                },
                "expected_result": {
                    "type": "object",
                    "description": "（可选）整体替换 expected_result（同 add 的结构）",
                },
            },
            "required": ["event_id"],
        },
    },
}

DELETE_ONGOING_EVENT_TOOL = {
    "type": "function",
    "function": {
        "name": "delete_ongoing_event",
        "description": "取消一条进行中的事件（如灾情突变中止赈灾）。被删事件的 expected_result 不会被应用。",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "要取消的事件 ID"},
            },
            "required": ["event_id"],
        },
    },
}

COMPLETE_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "complete_action",
        "description": (
            "结束本次行动 FC 循环。只负责两件事：①终止循环 ②返回回执（result_description 在 "
            "duration_days 后由 orchestrator 定时送达发起者）。"
            "**不再承担任何状态变更**——执行者侧用 update_*，目标侧用 add_ongoing_event。"
            "必须作为最后一次工具调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "result_description": {
                    "type": "string",
                    "description": "行动回执，duration_days 后送回发起者。应基于实际执行结果（已扣资源+排队事件）。",
                },
                "duration_days": {
                    "type": "number",
                    "description": "回执送达发起者的延迟（模拟天数）。通常 = 行动+回程时间。",
                },
                "success": {
                    "type": "boolean",
                    "description": "行动是否成功达成目标",
                },
            },
            "required": ["result_description", "duration_days"],
        },
    },
}

# ── Router 工具 ───────────────────────────────────────────────────────────────

WAKE_AGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "wake_agent",
        "description": (
            "向目标 Agent 投递消息。延迟由系统根据双方坐标与紧急程度自动计算："
            "同城/无距离立即送达，否则按直线距离与 urgency 对应速度倍率延迟。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "目标 Agent ID"},
                "message": {"type": "string", "description": "传递的消息内容"},
                "from_agent": {"type": "string", "description": "消息来源 Agent ID（用于计算距离）"},
                "urgency": {
                    "type": "string",
                    "enum": ["urgent", "express", "normal"],
                    "description": (
                        "紧急程度，决定传递速度："
                        "urgent=急递(1.8×速度)，express=快马(1.5×速度)，normal=普通公文(1×速度)"
                    ),
                },
            },
            "required": ["target", "message", "from_agent"],
        },
    },
}

EXECUTE_POLICY_TOOL = {
    "type": "function",
    "function": {
        "name": "execute_policy",
        "description": "调用世界模拟 Agent 执行政策效果",
        "parameters": {
            "type": "object",
            "properties": {
                "policy": {"type": "string", "description": "政策描述"},
                "location": {"type": "string", "description": "执行地点"},
                "executor": {"type": "string", "description": "执行者 Agent ID"},
                "from_agent": {"type": "string", "description": "发起此行动的官员 Agent ID（行动结果将送回给他）"},
            },
            "required": ["policy", "location"],
        },
    },
}

RELAY_DECREE_TOOL = {
    "type": "function",
    "function": {
        "name": "relay_decree",
        "description": "传递皇帝圣旨给指定官员",
        "parameters": {
            "type": "object",
            "properties": {
                "decree": {"type": "string", "description": "圣旨内容"},
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "接收圣旨的 Agent ID 列表",
                },
                "urgency": {"type": "string"},
            },
            "required": ["decree", "targets"],
        },
    },
}

LIST_AGENTS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_agents",
        "description": "列出当前所有可唤醒的 Agent（官员/机构）的 ID 和基本信息。可按所在地筛选。用于在唤醒 Agent 前确认合法目标。",
        "parameters": {
            "type": "object",
            "properties": {
                "location_id": {
                    "type": "string",
                    "description": "（可选）按所在地点 ID 筛选",
                },
            },
            "required": [],
        },
    },
}

# ── Agent 行为工具 ────────────────────────────────────────────────────────────

REQUEST_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "request_action",
        "description": (
            "亲自执行一个需要时间和资源的行动（调查、赈灾、抓捕、建设、巡查等）。"
            "行动由世界模拟器直接推演结果，完成后把结果送回给你。"
            "与 submit_action(do_something) 的区别：本工具绕过世界法则直接执行，"
            "你需要自己指定行动类型、地点和资源；"
            "而 do_something 交由世界法则理解你的意图并安排执行。"
            "两个工具可以同时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": "行动类型：investigate/relief/construction/arrest/patrol/tax_collection 等",
                },
                "target_location": {
                    "type": "string",
                    "description": "行动目标地点 ID（如 吴县、苏州、北京）",
                },
                "description": {
                    "type": "string",
                    "description": "具体行动描述：你想做什么、为什么、如何做",
                },
                "resources": {
                    "type": "string",
                    "description": "投入的资源描述（可选，如 粮食1000石、官兵50人）",
                },
                "severity": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Optional problem severity; omit when unknown.",
                },
                "causal_event_type": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
                    "description": (
                        "可选的行动结果因果事件类型。仅当 DLC 明确需要把本次 ActionResult 作为后续因果源时填写；"
                        "省略时保持旧行为，不自动发布事件。"
                    ),
                },
            },
            "required": ["action_type", "target_location", "description"],
        },
    },
}

SUBMIT_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_action",
        "description": "表达你这一轮的意图。三选一：do_nothing（按兵不动）、report_message（向他人传递信息）、do_something（声明行动意图，由世界法则理解并安排执行，可同时通知相关方）。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["do_nothing", "report_message", "do_something"],
                    "description": (
                        "do_nothing：本轮不行动，其余字段会被忽略。\n"
                        "report_message：向他人传递信息（汇报/通报/私信/传令等），世界法则决定路径。\n"
                        "do_something：声明行动意图（调查/赈灾/建设/抓捕/巡查等），"
                        "世界法则会理解你的意图、安排执行、并自动协调相关方（如通知上级、调度下属）。"
                        "把你想做的事写在 content 里。"
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "实际传递给目标的内容(别人会看到这个)",
                },
                "original_intent": {
                    "type": "string",
                    "description": "你内心的真实想法（不会被传递，仅用于记录）",
                },
                "is_distorting": {
                    "type": "boolean",
                    "description": "你是否在故意歪曲信息（true=是，false=否）",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["critical", "urgent", "normal"],
                    "description": "紧急程度",
                },
                "metadata": {
                    "type": "object",
                    "description": "附加信息，如 target_hint 指定越级目标",
                },
            },
            "required": ["action", "content", "original_intent", "is_distorting"],
        },
    },
}

# ── 预组合工具集（各 Agent 按需取用）────────────────────────────────────────

# ActionAgent 使用：世界状态读写 + 事件队列 CRUD + Agent 列表查询 + 结束行动
FINISH_TURN_TOOL = {
    "type": "function",
    "function": {
        "name": "finish_turn",
        "description": "Finish the multi-round turn after all intended actions have been submitted.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Briefly summarize why the turn is complete and what was submitted.",
                },
            },
            "required": [],
        },
    },
}

# ActionAgent: world read/write tools and explicit completion.
ACTION_AGENT_TOOLS = [
    LIST_ENTITIES_TOOL,
    QUERY_ENTITY_TOOL,
    UPDATE_ENTITY_STATUS_TOOL,
    PRODUCE_RESOURCE_TOOL,
    CONSUME_RESOURCE_TOOL,
    TRANSFER_ANCHORED_VALUE_TOOL,
    ADD_ONGOING_EVENT_TOOL,
    MODIFY_ONGOING_EVENT_TOOL,
    DELETE_ONGOING_EVENT_TOOL,
    LIST_AGENTS_TOOL,
    COMPLETE_ACTION_TOOL,
]

# Router tools.
ROUTER_TOOLS = [
    LIST_AGENTS_TOOL,
    WAKE_AGENT_TOOL,
    EXECUTE_POLICY_TOOL,
    RELAY_DECREE_TOOL,
]

# Ordinary agents: read-only context, intent submission, explicit finish.
AGENT_READ_TOOLS = [
    LIST_ENTITIES_TOOL,
    QUERY_ENTITY_TOOL,
    LIST_AGENTS_TOOL,
]

AGENT_TOOLS = [
    *AGENT_READ_TOOLS,
    SUBMIT_ACTION_TOOL,
    REQUEST_ACTION_TOOL,
    FINISH_TURN_TOOL,
]

# Tianji/debug aggregate tool set.
ALL_TOOLS = [
    LIST_ENTITIES_TOOL,
    QUERY_ENTITY_TOOL,
    UPDATE_ENTITY_STATUS_TOOL,
    UPDATE_INVENTORY_TOOL,
    PRODUCE_RESOURCE_TOOL,
    CONSUME_RESOURCE_TOOL,
    TRANSFER_ANCHORED_VALUE_TOOL,
    ADD_ONGOING_EVENT_TOOL,
    MODIFY_ONGOING_EVENT_TOOL,
    DELETE_ONGOING_EVENT_TOOL,
    LIST_AGENTS_TOOL,
    WAKE_AGENT_TOOL,
    EXECUTE_POLICY_TOOL,
    RELAY_DECREE_TOOL,
    SUBMIT_ACTION_TOOL,
    REQUEST_ACTION_TOOL,
    FINISH_TURN_TOOL,
    COMPLETE_ACTION_TOOL,
]
