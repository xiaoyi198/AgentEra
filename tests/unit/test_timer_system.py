"""TimerSystem 单元测试."""

import os
import tempfile

import pytest

from multiagent_framework.timer_system import TimerSystem
from multiagent_framework.types import TimerEntry


class TestTimerSystemMemory:
    """纯内存模式测试."""

    def setup_method(self):
        self.ts = TimerSystem()

    def test_schedule_and_pop(self):
        e1 = TimerEntry(fire_at=10.0, target_agent="A", payload={"msg": "hello"})
        e2 = TimerEntry(fire_at=5.0, target_agent="B", payload={"msg": "world"})
        self.ts.schedule(e1)
        self.ts.schedule(e2)

        due = self.ts.pop_due(6.0)
        assert len(due) == 1
        assert due[0].target_agent == "B"

        due = self.ts.pop_due(11.0)
        assert len(due) == 1
        assert due[0].target_agent == "A"

    def test_cancel(self):
        e = TimerEntry(fire_at=10.0, target_agent="A")
        tid = self.ts.schedule(e)
        assert self.ts.cancel(tid)
        due = self.ts.pop_due(100.0)
        assert len(due) == 0

    def test_cancel_nonexistent(self):
        assert not self.ts.cancel("fake_id")

    def test_peek_next_time(self):
        assert self.ts.peek_next_time() is None
        self.ts.schedule(TimerEntry(fire_at=5.0, target_agent="A"))
        self.ts.schedule(TimerEntry(fire_at=3.0, target_agent="B"))
        assert self.ts.peek_next_time() == 3.0

    def test_peek_skips_cancelled(self):
        e1 = TimerEntry(fire_at=3.0, target_agent="A")
        e2 = TimerEntry(fire_at=5.0, target_agent="B")
        self.ts.schedule(e1)
        self.ts.schedule(e2)
        self.ts.cancel(e1.timer_id)
        assert self.ts.peek_next_time() == 5.0

    def test_get_in_transit(self):
        self.ts.schedule(TimerEntry(fire_at=10.0, target_agent="A"))
        self.ts.schedule(TimerEntry(fire_at=20.0, target_agent="B"))
        in_transit = self.ts.get_in_transit()
        assert len(in_transit) == 2

    def test_count(self):
        self.ts.schedule(TimerEntry(fire_at=10.0, target_agent="A"))
        e = TimerEntry(fire_at=20.0, target_agent="B")
        self.ts.schedule(e)
        self.ts.cancel(e.timer_id)
        assert self.ts.count() == 1

    def test_empty_pop(self):
        assert self.ts.pop_due(100.0) == []

    def test_multiple_due_at_same_time(self):
        for i in range(5):
            self.ts.schedule(TimerEntry(fire_at=10.0, target_agent=f"A{i}"))
        due = self.ts.pop_due(10.0)
        assert len(due) == 5


class TestTimerSystemPersistence:
    """SQLite 持久化测试."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.ts = TimerSystem(self.tmp.name)

    def teardown_method(self):
        try:
            os.unlink(self.tmp.name)
        except PermissionError:
            pass  # Windows SQLite file lock

    def test_save_and_load(self):
        self.ts.schedule(TimerEntry(fire_at=10.0, target_agent="A", payload={"x": 1}))
        self.ts.schedule(TimerEntry(fire_at=20.0, target_agent="B", payload={"x": 2}))
        saved = self.ts.save_all()
        assert saved == 2

        ts2 = TimerSystem(self.tmp.name)
        loaded = ts2.load_from_db()
        assert loaded == 2

        due = ts2.pop_due(15.0)
        assert len(due) == 1
        assert due[0].target_agent == "A"
        assert due[0].payload == {"x": 1}

    def test_cancelled_not_loaded(self):
        e = TimerEntry(fire_at=10.0, target_agent="A")
        self.ts.schedule(e)
        self.ts.cancel(e.timer_id)
        self.ts.save_all()

        ts2 = TimerSystem(self.tmp.name)
        loaded = ts2.load_from_db()
        assert loaded == 0



def test_claim_due_keeps_sqlite_row_until_ack(tmp_path):
    db_path = tmp_path / "timers.db"
    timer = TimerEntry(
        timer_id="tmr-durable",
        fire_at=5.0,
        target_agent="agent-a",
        payload={"message": "deliver"},
    )
    first = TimerSystem(str(db_path))
    first.schedule(timer)

    claimed = first.claim_due(5.0)
    assert [entry.timer_id for entry in claimed] == ["tmr-durable"]
    assert first.count() == 0

    restarted = TimerSystem(str(db_path))
    assert restarted.load_from_db() == 1
    assert [entry.timer_id for entry in restarted.claim_due(5.0)] == ["tmr-durable"]
    assert restarted.ack("tmr-durable")

    final = TimerSystem(str(db_path))
    assert final.load_from_db() == 0


def test_release_claim_returns_timer_to_heap():
    timers = TimerSystem()
    timers.schedule(TimerEntry(timer_id="retry", fire_at=1.0, target_agent="a"))

    assert [entry.timer_id for entry in timers.claim_due(1.0)] == ["retry"]
    assert timers.release_claim("retry")
    assert [entry.timer_id for entry in timers.claim_due(1.0)] == ["retry"]
