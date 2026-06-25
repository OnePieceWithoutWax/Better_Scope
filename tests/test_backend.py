"""Hardware-free unit tests for the Better_Scope backend."""

from pathlib import Path

import pytest

from better_scope.config import AppConfig
from better_scope.core import BetterScope
from better_scope.instruments.discovery import scpi_id_parser
from better_scope.instruments.drivers import driver_for
from better_scope.utils import (
    filename_with_suffix,
    get_filename_with_datestamp,
    get_next_incremented_filename,
)


def test_scpi_id_parser_full() -> None:
    parsed = scpi_id_parser("TEKTRONIX,MSO54,B012345,CF:1.2.3")
    assert parsed == {
        "manufacturer": "TEKTRONIX",
        "model_num": "MSO54",
        "serial_num": "B012345",
        "software_rev": "CF:1.2.3",
    }


def test_scpi_id_parser_empty() -> None:
    parsed = scpi_id_parser("")
    assert all(v is None for v in parsed.values())


@pytest.mark.parametrize(
    ("model", "expected"),
    [("MSO54", "MSO54"), ("MSO44", "MSO44"), ("MSO58", "MSO58")],
)
def test_driver_for_known_models(model: str, expected: str) -> None:
    driver = driver_for({"manufacturer": "TEKTRONIX", "model_num": model})
    assert driver is not None
    assert driver.__name__ == expected


def test_driver_for_unknown_manufacturer() -> None:
    assert driver_for({"manufacturer": "RIGOL", "model_num": "DS1054"}) is None


def test_driver_for_unknown_tek_mso_falls_back_to_base() -> None:
    driver = driver_for({"manufacturer": "TEKTRONIX", "model_num": "MSO64"})
    assert driver is not None
    assert driver.__name__ == "TektronixBaseScope"


def test_config_increment_and_datestamp_mutually_exclusive(tmp_path: Path) -> None:
    cfg = AppConfig(_config_file=tmp_path / "config.json")
    cfg.auto_increment = True
    assert cfg.auto_increment is True
    assert cfg.datestamp is False
    cfg.datestamp = True
    assert cfg.datestamp is True
    assert cfg.auto_increment is False


def test_config_persists_to_disk(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    cfg = AppConfig(_config_file=config_file)
    cfg.default_filename = "scope_shot"
    assert config_file.exists()

    reloaded = AppConfig(_config_file=config_file)
    assert reloaded.default_filename == "scope_shot"


def test_filename_with_suffix() -> None:
    assert filename_with_suffix("capture", "png") == "capture.png"
    assert filename_with_suffix("capture.png", "png") == "capture.png"
    assert filename_with_suffix("capture", ".png") == "capture.png"


def test_next_incremented_filename(tmp_path: Path) -> None:
    assert get_next_incremented_filename(tmp_path, "capture", "png") == "capture_001.png"
    (tmp_path / "capture_001.png").touch()
    assert get_next_incremented_filename(tmp_path, "capture", "png") == "capture_002.png"


def test_datestamp_filename_unique(tmp_path: Path) -> None:
    name = get_filename_with_datestamp(tmp_path, "capture", "png")
    assert name.startswith("capture_")
    assert name.endswith(".png")


# -- Verified-set / fake-instrument tests (no hardware) ---------------------


class FakeChannel:
    """Channel that stores property writes, optionally clamping ``scale``."""

    def __init__(self, clamp_scale: float | None = None) -> None:
        self.label_name = ""
        self.enable = True
        self.scale = 1.0
        self.offset = 0.0
        self.position = 0.0
        self.label_x = 0.0
        self.label_y = 0.0
        self._clamp_scale = clamp_scale

    def __setattr__(self, name: str, value: object) -> None:
        if name == "scale" and getattr(self, "_clamp_scale", None) is not None:
            value = self._clamp_scale  # Simulate a driver/firmware clamp.
        object.__setattr__(self, name, value)


class FakeTrigger:
    def __init__(self) -> None:
        self.a_mode = "AUTO"
        self.a_type = "EDGE"
        self.a_edge_source = "CH1"
        self.a_edge_slope = "RISE"
        self.a_edge_coupling = "DC"
        self.a_level = 0.0
        self.state = "READY"
        self.frequency = 1000.0


class FakeInstrument:
    def __init__(self, channels: tuple[FakeChannel, ...]) -> None:
        self.channels = channels
        self.analog_channels_count = len(channels)
        self.trigger = FakeTrigger()
        self.written: list[str] = []

    def write(self, command: str) -> None:
        self.written.append(command)


def _connected_scope(instrument: FakeInstrument) -> BetterScope:
    scope = BetterScope()
    scope._instrument = instrument
    return scope


def test_values_match_float_tolerance() -> None:
    assert BetterScope._values_match(1.0, 1.0004) is True
    assert BetterScope._values_match(1.0, 1.5) is False


def test_set_channel_properties_verified() -> None:
    instr = FakeInstrument((FakeChannel(), FakeChannel()))
    scope = _connected_scope(instr)
    result = scope.set_channel_properties(1, {"name": "VCC", "scale": 0.5, "offset": 1.0})
    assert result["mismatches"] == []
    assert instr.channels[0].label_name == "VCC"
    assert instr.channels[0].scale == 0.5
    assert result["applied"]["name"] == "VCC"


def test_set_channel_properties_detects_mismatch() -> None:
    # Channel clamps scale to 2.0 regardless of the requested value.
    instr = FakeInstrument((FakeChannel(clamp_scale=2.0),))
    scope = _connected_scope(instr)
    result = scope.set_channel_properties(1, {"scale": 0.5})
    assert "scale" in result["mismatches"]
    assert result["applied"]["scale"] == 2.0


def test_get_channels_shape() -> None:
    scope = _connected_scope(FakeInstrument((FakeChannel(), FakeChannel())))
    channels = scope.get_channels()
    assert [c["index"] for c in channels] == [1, 2]
    assert set(channels[0]) >= {"name", "enabled", "scale", "offset", "position", "label_x", "label_y"}


def test_apply_trigger_and_actions() -> None:
    instr = FakeInstrument((FakeChannel(),))
    scope = _connected_scope(instr)
    result = scope.apply_trigger({"mode": "NORMAL", "level": 1.25})
    assert result["mismatches"] == []
    assert instr.trigger.a_mode == "NORMAL"
    assert instr.trigger.a_level == 1.25

    scope.force_trigger()
    scope.set_trigger_level_50()
    assert "TRIGger:FORCe" in instr.written
    assert "TRIGger:A SETLevel" in instr.written
