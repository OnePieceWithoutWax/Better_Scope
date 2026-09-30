"""Decoder registry discovery tests."""

import logging
from pathlib import Path

import pytest

from better_scope.decode.registry import PLUGIN_PATH_ENV, DecoderRegistry

PLUGIN_OK = '''
from better_scope.decode.api import Decoder, Option, Role
from better_scope.decode.model import Level


class EdgeCounter(Decoder):
    id = "edge_counter"
    name = "Edge counter"
    roles = (Role("in", "Input"),)
    options = (Option("scale", "Scale", float, 1.0),)

    def decode(self, signals, opts):
        sig = signals["in"]
        yield self.frame(sig.t_start, sig.t_end, "count", Level.PACKET,
                         data={"edges": int(sig.edges.size * opts["scale"])})


class EdgeCounterSummary(Decoder):
    id = "edge_summary"
    name = "Edge summary"
    stacks_on = "edge_counter"

    def decode_frames(self, frames, opts):
        for f in frames:
            yield self.frame(f.start, f.end, "summary", Level.SEMANTIC, text=(f"{f.data['edges']} edges",))
'''

PLUGIN_BROKEN_IMPORT = "import this_module_does_not_exist\n"

PLUGIN_BAD_METADATA = '''
from better_scope.decode.api import Decoder, Option, Role


class BadDefaults(Decoder):
    id = "bad_defaults"
    name = "Bad defaults"
    roles = (Role("a", "A"), Role("a", "A again"))
    options = (Option("n", "N", int, "not an int"),)

    def decode(self, signals, opts):
        yield from ()


class Orphan(Decoder):
    id = "orphan"
    name = "Orphan"
    stacks_on = "no_such_decoder"

    def decode_frames(self, frames, opts):
        yield from ()
'''

PLUGIN_OVERRIDE_UART = '''
from better_scope.decode.decoders.uart import UartDecoder


class MyUart(UartDecoder):
    id = "uart"
    name = "UART (site override)"
    version = "9.9"
'''


@pytest.fixture
def plugin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "plugins"
    folder.mkdir()
    monkeypatch.setenv(PLUGIN_PATH_ENV, str(folder))
    return folder


def _registry(tmp_path: Path) -> DecoderRegistry:
    # An empty user dir keeps the real ~/.better_scope/plugins out of the tests.
    user_dir = tmp_path / "user_plugins"
    user_dir.mkdir(exist_ok=True)
    return DecoderRegistry(user_dir=user_dir).discover()


def test_builtin_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PLUGIN_PATH_ENV, raising=False)
    reg = _registry(tmp_path)
    assert "uart" in reg
    assert reg.origins["uart"].startswith("built-in")
    assert reg.errors == []


def test_plugin_from_env_path_and_stacking(tmp_path: Path, plugin_dir: Path) -> None:
    (plugin_dir / "edge_counter.py").write_text(PLUGIN_OK)
    reg = _registry(tmp_path)
    assert {"edge_counter", "edge_summary"} <= set(reg.ids())
    assert [c.id for c in reg.chain("edge_summary")] == ["edge_counter", "edge_summary"]


def test_plugin_from_user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PLUGIN_PATH_ENV, raising=False)
    user_dir = tmp_path / "user_plugins"
    user_dir.mkdir()
    (user_dir / "edge_counter.py").write_text(PLUGIN_OK)
    reg = DecoderRegistry(user_dir=user_dir).discover()
    assert "edge_counter" in reg


def test_broken_plugins_are_skipped(tmp_path: Path, plugin_dir: Path) -> None:
    (plugin_dir / "broken.py").write_text(PLUGIN_BROKEN_IMPORT)
    (plugin_dir / "bad_meta.py").write_text(PLUGIN_BAD_METADATA)
    (plugin_dir / "edge_counter.py").write_text(PLUGIN_OK)
    reg = _registry(tmp_path)
    assert "uart" in reg and "edge_counter" in reg
    assert "bad_defaults" not in reg and "orphan" not in reg
    joined = "\n".join(reg.errors)
    assert "broken.py" in joined
    assert "duplicate role ids" in joined
    assert "default 'not an int'" in joined
    assert "no_such_decoder" in joined


def test_id_override_warns(tmp_path: Path, plugin_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    (plugin_dir / "my_uart.py").write_text(PLUGIN_OVERRIDE_UART)
    with caplog.at_level(logging.WARNING):
        reg = _registry(tmp_path)
    assert reg.get("uart").version == "9.9"
    assert "my_uart.py" in reg.origins["uart"]
    assert any("overrides" in r.message for r in caplog.records)


def test_plugin_decoder_runs_through_engine(tmp_path: Path, plugin_dir: Path) -> None:
    import numpy as np

    from better_scope.decode import BusConfig, Level, run

    (plugin_dir / "edge_counter.py").write_text(PLUGIN_OK)
    reg = _registry(tmp_path)
    levels = np.array([0, 1, 1, 0, 1, 0, 0, 1], dtype=bool)
    t = np.arange(levels.size) * 1e-6
    bus = BusConfig("e", "Edges", "edge_summary", role_map={"in": "D0"}, options={"scale": 2})
    result = run([bus], {"D0": (t, levels)}, registry=reg)
    assert result.for_bus("e", Level.PACKET)[0].data == {"edges": 10}
    assert result.for_bus("e", Level.SEMANTIC)[0].text == ("10 edges",)


PLUGIN_HINTS = '''
from better_scope.decode.api import Decoder, Role
from better_scope.decode.model import Level


class HintBase(Decoder):
    id = "hint_base"
    name = "Hint base"
    roles = (Role("in", "Input"),)

    def decode(self, signals, opts):
        sig = signals["in"]
        yield self.frame(sig.t_start, sig.t_end, "hints", Level.PACKET, data=dict(self.hints))


class HintMiddle(Decoder):
    id = "hint_middle"
    name = "Hint middle"
    stacks_on = "hint_base"

    @classmethod
    def hints_for_lower(cls, opts):
        return {"shared": "middle", "middle_only": 1}

    def decode_frames(self, frames, opts):
        yield from ()


class HintTop(Decoder):
    id = "hint_top"
    name = "Hint top"
    stacks_on = "hint_middle"

    @classmethod
    def hints_for_lower(cls, opts):
        return {"shared": "top", "top_only": 2}

    def decode_frames(self, frames, opts):
        yield from ()
'''


def test_hints_flow_down_nearest_wins(tmp_path: Path, plugin_dir: Path) -> None:
    import numpy as np

    from better_scope.decode import BusConfig, Level, run

    (plugin_dir / "hints.py").write_text(PLUGIN_HINTS)
    reg = _registry(tmp_path)
    t = np.arange(4) * 1e-6
    bus = BusConfig("h", "Hints", "hint_top", role_map={"in": "D0"})
    result = run([bus], {"D0": (t, np.array([0, 1, 1, 0], dtype=bool))}, registry=reg)
    (frame,) = result.for_bus("h", Level.PACKET)
    assert frame.data == {"shared": "middle", "middle_only": 1, "top_only": 2}
