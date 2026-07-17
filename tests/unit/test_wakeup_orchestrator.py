from __future__ import annotations

import asyncio
from types import SimpleNamespace

from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.timer_system import TimerSystem
from multiagent_framework.types import AgentAction, TimerEntry


class FakeCourt:
    def get_official(self, _agent_id):
        return None


class FakeChainLog:
    def get_max_hop(self, _chain_id):
        return 0

    def append(self, _entry):
        return None


class FakeRouter:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.llm = SimpleNamespace(token_counter=SimpleNamespace(to_dict=lambda: {}))
        self.last_response_text = ""
        self.last_stats = {}

    def update_time_scale(self, _value):
        return None

    async def process_batch(self, _actions):
        if self.fail:
            raise RuntimeError("transient router failure")
        return []


class FakeAgent:
    agent_id = "agent-a"
    agent_type = "official"
    last_turn_outcome = None

    def __init__(self, *, fail: bool = False, action: str = "do_nothing"):
        self.fail = fail
        self.action = action

    async def process(self, _payload):
        if self.fail:
            raise RuntimeError("transient agent failure")
        return AgentAction(agent_id=self.agent_id, action=self.action, content="done")

    async def record_execution_feedback(self, _feedback):
        return None


def make_orchestrator(timers=None, router=None):
    return Orchestrator(
        court_graph=FakeCourt(),
        timer_system=timers or TimerSystem(),
        chain_log=FakeChainLog(),
        router=router or FakeRouter(),
    )


def claim_one(orch, *, timer_id=""):
    entry = orch._pending_wakeups.enqueue({
        "delivery_id": f"timer:{timer_id}" if timer_id else "direct:1",
        "target": "agent-a",
        "payload": {"message": "work"},
        "timer_id": timer_id,
    })
    orch._pending_wakeups.claim_pending()
    return {
        "target": "agent-a",
        "payload": dict(entry["payload"]),
        "chain_id": entry["chain_id"],
        "chain_ids": list(entry["chain_ids"]),
        "delivery_ids": [entry["delivery_id"]],
        "timer_id": timer_id,
        "timer_ids": [timer_id],
    }


def test_successful_background_delivery_is_acked():
    orch = make_orchestrator()
    wakeup = claim_one(orch)

    asyncio.run(orch._process_agents_background([(FakeAgent(), wakeup)]))

    assert orch._pending_wakeups.total_count == 0


def test_unexpected_agent_failure_returns_delivery_to_pending():
    orch = make_orchestrator()
    wakeup = claim_one(orch)

    asyncio.run(orch._process_agents_background([(FakeAgent(fail=True), wakeup)]))

    assert orch._pending_wakeups.pending_count == 1
    assert orch._pending_wakeups.inflight_count == 0
    assert orch._pending_wakeups[0]["attempts"] == 1


def test_timer_row_is_acked_only_after_agent_delivery(tmp_path):
    db_path = tmp_path / "timers.db"
    timers = TimerSystem(str(db_path))
    timers.schedule(TimerEntry(
        timer_id="durable-timer",
        fire_at=1.0,
        target_agent="agent-a",
        payload={"message": "timer work"},
    ))
    assert [entry.timer_id for entry in timers.claim_due(1.0)] == ["durable-timer"]

    orch = make_orchestrator(timers)
    wakeup = claim_one(orch, timer_id="durable-timer")
    asyncio.run(orch._process_agents_background([(FakeAgent(), wakeup)]))

    restarted = TimerSystem(str(db_path))
    assert restarted.load_from_db() == 0
    assert orch._pending_wakeups.total_count == 0

def test_router_failure_returns_successful_agent_delivery_to_pending():
    orch = make_orchestrator(router=FakeRouter(fail=True))
    wakeup = claim_one(orch)

    asyncio.run(orch._process_agents_background([
        (FakeAgent(action="submit_action"), wakeup),
    ]))

    assert orch._pending_wakeups.pending_count == 1
    assert orch._pending_wakeups.inflight_count == 0
    assert orch._pending_wakeups[0]["attempts"] == 1


def test_runtime_restore_requeues_inflight_wakeup_for_delivery():
    source = make_orchestrator()
    entry = source._pending_wakeups.enqueue({
        "delivery_id": "event:restart",
        "target": "agent-a",
        "payload": {"message": "resume after crash"},
    })
    source._pending_wakeups.claim_pending()
    snapshot = source.runtime_snapshot()

    restored = make_orchestrator()
    restored.restore_runtime_snapshot(snapshot)

    assert restored._pending_wakeups.pending_count == 1
    assert restored._pending_wakeups.inflight_count == 0
    replay = restored._pending_wakeups[0]
    assert replay["delivery_id"] == entry["delivery_id"]
    assert replay["attempts"] == 1

def test_request_action_is_awaited_before_wakeup_ack():
    async def scenario():
        orch = make_orchestrator()
        orch.action_agent = object()
        wakeup = claim_one(orch)
        started = asyncio.Event()
        release = asyncio.Event()

        async def controlled_run_action(**_kwargs):
            started.set()
            await release.wait()

        orch._run_action = controlled_run_action

        class RequestActionAgent(FakeAgent):
            async def process(self, _payload):
                return AgentAction(
                    agent_id=self.agent_id,
                    action="request_action",
                    content="perform durable work",
                    metadata={"fc_type": "request_action", "intent_id": "intent-1"},
                )

        task = asyncio.create_task(
            orch._process_agents_background([(RequestActionAgent(), wakeup)])
        )
        await started.wait()
        assert orch._pending_wakeups.inflight_count == 1
        assert orch._pending_wakeups.total_count == 1

        release.set()
        await task
        assert orch._pending_wakeups.total_count == 0

    asyncio.run(scenario())

def test_timer_and_inflight_outbox_restart_replay_without_duplicate_entry(tmp_path):
    db_path = tmp_path / "timers.db"
    first_timers = TimerSystem(str(db_path))
    first_timers.schedule(TimerEntry(
        timer_id="timer-crash-replay",
        fire_at=1.0,
        target_agent="agent-a",
        payload={"message": "survive restart"},
        chain_id="chain-crash-replay",
    ))
    claimed = first_timers.claim_due(1.0)
    assert len(claimed) == 1

    source = make_orchestrator(first_timers)
    entry = source._pending_wakeups.enqueue({
        "delivery_id": "timer:timer-crash-replay",
        "target": claimed[0].target_agent,
        "payload": claimed[0].payload,
        "chain_id": claimed[0].chain_id,
        "timer_id": claimed[0].timer_id,
    })
    source._pending_wakeups.claim_pending()
    snapshot = source.runtime_snapshot()

    restarted_timers = TimerSystem(str(db_path))
    assert restarted_timers.load_from_db() == 1
    restored = make_orchestrator(restarted_timers)
    restored.restore_runtime_snapshot(snapshot)

    due_again = restarted_timers.claim_due(1.0)
    assert [timer.timer_id for timer in due_again] == ["timer-crash-replay"]
    duplicate = restored._pending_wakeups.enqueue({
        "delivery_id": "timer:timer-crash-replay",
        "target": due_again[0].target_agent,
        "payload": due_again[0].payload,
        "chain_id": due_again[0].chain_id,
        "timer_id": due_again[0].timer_id,
    })
    assert duplicate["delivery_id"] == entry["delivery_id"]
    assert restored._pending_wakeups.total_count == 1

    replay = restored._pending_wakeups.claim_pending()[0]
    wakeup = {
        "target": replay["target"],
        "payload": dict(replay["payload"]),
        "chain_id": replay["chain_id"],
        "chain_ids": list(replay["chain_ids"]),
        "delivery_ids": [replay["delivery_id"]],
        "timer_id": replay["timer_id"],
        "timer_ids": [replay["timer_id"]],
    }
    asyncio.run(restored._process_agents_background([(FakeAgent(), wakeup)]))

    assert restored._pending_wakeups.total_count == 0
    final_timers = TimerSystem(str(db_path))
    assert final_timers.load_from_db() == 0
