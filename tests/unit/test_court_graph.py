"""CourtGraph 单元测试."""

import math
import os
import tempfile

import pytest

from multiagent_framework.court_graph import (
    CourtGraph, Official,
    TRAVEL_SPEED_LI_PER_DAY, MIN_TRAVEL_DAYS, TravelCoeff,
)
from multiagent_framework.world_state import WorldState, Entity


class TestCourtGraph:

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.court = CourtGraph(self.tmp.name)
        self._load_test_data()

    def teardown_method(self):
        try:
            os.unlink(self.tmp.name)
        except PermissionError:
            pass  # Windows SQLite file lock

    def _load_test_data(self):
        officials = [
            Official("皇帝",     "皇帝", 15, "京城", "player",   "official", None,      "忠直"),
            Official("太监_王振", "王振", 13, "京城", "eunuch",   "official", "皇帝",    "圆滑"),
            Official("内阁_杨士奇","杨士奇",10,"京城","official","official","太监_王振","忠直"),
            Official("苏州知府",  "况钟",  5, "苏州", "official", "official", "内阁_杨士奇","忠直"),
            Official("吴县知县",  "张三",  1, "吴县", "official", "official", "苏州知府","贪腐"),
        ]
        self.court.bulk_load_officials(officials)

        # 通过 WorldState 提供坐标
        self.world_state = WorldState(self.tmp.name)
        self.world_state.upsert_entity(Entity(
            id="京城", type="location", name="京城",
            metadata={"x": 0.0, "y": 0.0, "region": "直隶"},
        ))
        self.world_state.upsert_entity(Entity(
            id="苏州", type="location", name="苏州府",
            metadata={"x": 450.0, "y": -1730.0, "region": "江南"},
        ))
        self.world_state.upsert_entity(Entity(
            id="吴县", type="location", name="吴县",
            metadata={"x": 448.0, "y": -1732.0, "region": "江南"},
        ))
        self.court.set_world_state(self.world_state)

    def test_get_official(self):
        o = self.court.get_official("皇帝")
        assert o is not None
        assert o.name == "皇帝"
        assert o.rank == 15

    def test_get_nonexistent(self):
        assert self.court.get_official("不存在") is None

    def test_get_superior(self):
        sup = self.court.get_superior("吴县知县")
        assert sup is not None
        assert sup.id == "苏州知府"

    def test_emperor_has_no_superior(self):
        assert self.court.get_superior("皇帝") is None

    def test_get_subordinates(self):
        subs = self.court.get_subordinates("苏州知府")
        assert len(subs) == 1
        assert subs[0].id == "吴县知县"

    def test_chain_to_emperor(self):
        chain = self.court.get_chain_to_emperor("吴县知县")
        ids = [o.id for o in chain]
        assert ids == ["吴县知县", "苏州知府", "内阁_杨士奇", "太监_王振", "皇帝"]

    def test_get_all_officials(self):
        all_o = self.court.get_all_officials()
        assert len(all_o) == 5

    def test_get_by_rank(self):
        rank1 = self.court.get_officials_by_rank(1)
        assert len(rank1) == 1
        assert rank1[0].id == "吴县知县"

    def test_distance_same_city(self):
        # 吴县和苏州同城，应使用最短时间
        dist = self.court.get_distance("吴县知县", "苏州知府")
        assert dist == pytest.approx(MIN_TRAVEL_DAYS)

    def test_distance_to_capital(self):
        # 苏州 → 京城 ≈ sqrt(450²+1730²)/300 ≈ 5.96 天
        dist = self.court.get_distance("苏州知府", "皇帝")
        expected = math.sqrt(450**2 + 1730**2) / TRAVEL_SPEED_LI_PER_DAY
        assert dist == pytest.approx(expected, rel=0.01)
        assert 5.5 < dist < 6.5

    def test_distance_urgent(self):
        dist_normal = self.court.get_distance("苏州知府", "皇帝", TravelCoeff.NORMAL)
        dist_urgent = self.court.get_distance("苏州知府", "皇帝", TravelCoeff.URGENT)
        assert dist_urgent == pytest.approx(dist_normal * TravelCoeff.URGENT)

    def test_distance_slow(self):
        dist_normal = self.court.get_distance("苏州知府", "皇帝", TravelCoeff.NORMAL)
        dist_slow   = self.court.get_distance("苏州知府", "皇帝", TravelCoeff.SLOW)
        assert dist_slow == pytest.approx(dist_normal * TravelCoeff.SLOW)

    def test_distance_unknown_official(self):
        dist = self.court.get_distance("不存在A", "不存在B")
        assert dist == pytest.approx(5.0)

    def test_distance_no_world_state(self):
        """没有注入 WorldState 时，get_distance 应返回默认值."""
        court_no_ws = CourtGraph(self.tmp.name)
        court_no_ws.bulk_load_officials([
            Official("A", "A", 1, "loc1", "official", "official", None, "忠直"),
            Official("B", "B", 1, "loc2", "official", "official", None, "忠直"),
        ])
        dist = court_no_ws.get_distance("A", "B")
        assert dist == pytest.approx(5.0)
