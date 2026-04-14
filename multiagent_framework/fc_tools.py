"""所有 FC（Function Call）工具定义的中央注册表.

各 Agent 按需 import 所需工具集：
  from .fc_tools import WORLD_STATE_TOOLS, ACTION_AGENT_TOOLS
  from .fc_tools import ROUTER_TOOLS
  from .fc_tools import SUBMIT_ACTION_TOOL, REQUEST_ACTION_TOOL, AGENT_TOOLS
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
        "description": "查询一个实体（地点/人物/机构/组织）的描述与当前状况。",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "实体 ID"},
            },
            "required": ["entity_id"],
        },
    },
}

QUERY_INVENTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_inventory",
        "description": "查询一个实体的库存（粮食、货币、兵力、物资等）。",
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
        "description": "相对增减某实体的库存条目。quantity_delta 正数为增加，负数为减少。",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "category": {"type": "string", "description": "类别：粮食/货币/兵力/物资"},
                "item_name": {"type": "string", "description": "条目名：稻米/白银/官兵 等"},
                "quantity_delta": {"type": "number", "description": "变化量，正增负减"},
                "reason": {"type": "string", "description": "变动原因（用于日志）"},
            },
            "required": ["entity_id", "category", "item_name", "quantity_delta"],
        },
    },
}

COMPLETE_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "complete_action",
        "description": "结束本次行动模拟，返回结果、耗时和对执行者的重要发现。必须作为最后一次工具调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "result_description": {
                    "type": "string",
                    "description": "行动结果描述，将作为消息送回执行者。简明扼要、聚焦关键发现。",
                },
                "duration_days": {
                    "type": "number",
                    "description": "行动耗时（模拟天数）",
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
        "description": "唤醒指定 Agent，直接投递消息",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "目标 Agent ID"},
                "message": {"type": "string", "description": "传递的消息内容"},
                "from_agent": {"type": "string", "description": "消息来源 Agent ID"},
            },
            "required": ["target", "message"],
        },
    },
}

CREATE_TIMER_TOOL = {
    "type": "function",
    "function": {
        "name": "create_timer",
        "description": "创建延迟定时器，消息在指定天数后送达",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "目标 Agent ID"},
                "delay_days": {"type": "number", "description": "延迟天数"},
                "message": {"type": "string", "description": "送达时的消息内容"},
                "from_agent": {"type": "string", "description": "消息来源 Agent ID"},
                "urgency": {"type": "string", "description": "紧急程度"},
            },
            "required": ["target", "delay_days", "message"],
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

# 世界状态工具子集（不含 complete_action），供其他 Agent 复用
WORLD_STATE_TOOLS = [
    LIST_ENTITIES_TOOL,
    QUERY_ENTITY_TOOL,
    QUERY_INVENTORY_TOOL,
    UPDATE_ENTITY_STATUS_TOOL,
    UPDATE_INVENTORY_TOOL,
]

# ActionAgent 使用：世界状态读写 + Agent 列表查询 + 结束行动
ACTION_AGENT_TOOLS = WORLD_STATE_TOOLS + [LIST_AGENTS_TOOL, COMPLETE_ACTION_TOOL]

# Router 使用
ROUTER_TOOLS = [
    LIST_AGENTS_TOOL,
    WAKE_AGENT_TOOL,
    CREATE_TIMER_TOOL,
    EXECUTE_POLICY_TOOL,
    RELAY_DECREE_TOOL,
]

# OfficialAgent / InstitutionAgent 使用
AGENT_TOOLS = [
    SUBMIT_ACTION_TOOL,
    REQUEST_ACTION_TOOL,
]

# 天玑 / 调试接口：全量工具
ALL_TOOLS = ACTION_AGENT_TOOLS + ROUTER_TOOLS + AGENT_TOOLS
