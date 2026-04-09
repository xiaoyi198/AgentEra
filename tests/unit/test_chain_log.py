"""ChainLog 单元测试."""

import os
import tempfile

import pytest

from multiagent_framework.chain_log import ChainLog
from multiagent_framework.types import ChainLogEntry


class TestChainLog:

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
        self.tmp.close()
        # 清空文件
        open(self.tmp.name, "w").close()
        self.log = ChainLog(self.tmp.name)

    def teardown_method(self):
        os.unlink(self.tmp.name)

    def test_append_and_query(self):
        entry = ChainLogEntry(
            chain_id="c1", event_id="e1", caller="知县",
            caller_agent_type="official",
            original_text="蝗灾严重", transmitted_text="略有灾情",
            is_distorted=True, hop_count=1,
        )
        self.log.append(entry)

        assert len(self.log.all_entries()) == 1
        assert len(self.log.get_chain("c1")) == 1
        assert len(self.log.get_event_chains("e1")) == 1

    def test_distortion_query(self):
        self.log.append(ChainLogEntry(chain_id="c1", is_distorted=True, caller="A"))
        self.log.append(ChainLogEntry(chain_id="c1", is_distorted=False, caller="B"))
        self.log.append(ChainLogEntry(chain_id="c2", is_distorted=True, caller="C"))

        assert len(self.log.get_distortions()) == 2
        assert len(self.log.get_distortions("c1")) == 1

    def test_withheld_query(self):
        self.log.append(ChainLogEntry(chain_id="c1", status="withheld", caller="太监"))
        self.log.append(ChainLogEntry(chain_id="c1", status="delivered", caller="知县"))

        withheld = self.log.get_withheld()
        assert len(withheld) == 1
        assert withheld[0].caller == "太监"

    def test_max_hop(self):
        self.log.append(ChainLogEntry(chain_id="c1", hop_count=1))
        self.log.append(ChainLogEntry(chain_id="c1", hop_count=3))
        self.log.append(ChainLogEntry(chain_id="c1", hop_count=2))

        assert self.log.get_max_hop("c1") == 3
        assert self.log.get_max_hop("nonexistent") == 0

    def test_god_view(self):
        self.log.append(ChainLogEntry(
            chain_id="c1", caller="知县", caller_agent_type="official",
            original_text="真实情况", transmitted_text="修改后的",
            is_distorted=True, distortion_note="隐瞒了",
            status="delivered", hop_count=1,
        ))
        view = self.log.to_god_view("c1")
        assert len(view) == 1
        assert view[0]["distorted"] is True
        assert view[0]["original"] == "真实情况"
        assert view[0]["transmitted"] == "修改后的"

    def test_persistence(self):
        self.log.append(ChainLogEntry(chain_id="c1", caller="A", hop_count=1))
        self.log.append(ChainLogEntry(chain_id="c2", caller="B", hop_count=2))

        # 新实例从文件恢复
        log2 = ChainLog(self.tmp.name)
        loaded = log2.load_from_file()
        assert loaded == 2
        assert len(log2.get_chain("c1")) == 1
