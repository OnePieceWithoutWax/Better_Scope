"""Live decode triggering against a fake instrument with scripted STATE/NUMACq."""

import numpy as np
import pytest

from better_scope.core import BetterScope
from better_scope.live import LiveAction, LiveMonitor, live_cycle


class FakeAcquisition:
    """``ACQuire`` subsystem: STATE/NUMACq are set by the test, writes are logged."""

    def __init__(self) -> None:
        self.writes: list[str] = []
        self._state = "0"
        self.num_acquisitions: float = 0.0
        self._stop_after = "RUNSTOP"

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        self.writes.append(f"STATE {value}")
        self._state = "1" if value == "RUN" else "0"

    @property
    def stop_after(self) -> str:
        return self._stop_after

    @stop_after.setter
    def stop_after(self, value: str) -> None:
        self.writes.append(f"STOPAFTER {value}")
        self._stop_after = value

    def scope_is(self, running: bool, num: int) -> None:
        """What the front panel did since the last poll."""
        self._state = "1" if running else "0"
        self.num_acquisitions = float(num)


class FakeWaveforms:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.transfers = 0

    def get_multiple_waveforms(self, sources: list[str]) -> dict:
        if self.fail:
            raise TimeoutError("VISA timeout")
        self.transfers += 1
        t = np.arange(10) * 1e-6
        return {s: (t, np.zeros(10)) for s in sources}


class FakeInstrument:
    def __init__(self) -> None:
        self.acquisition = FakeAcquisition()
        self.waveforms = FakeWaveforms()


@pytest.fixture
def scope() -> BetterScope:
    s = BetterScope()
    s._instrument = FakeInstrument()
    return s


def _poll(scope: BetterScope, monitor: LiveMonitor) -> LiveAction:
    return monitor.poll(*scope.acquisition_status())


def _acq(scope: BetterScope) -> FakeAcquisition:
    return scope._instrument.acquisition


@pytest.mark.parametrize(("state", "running"), [("1", True), ("0", False), (1.0, True), ("RUN", True), ("STOP", False)])
def test_acquisition_status_parsing(scope: BetterScope, state: object, running: bool) -> None:
    _acq(scope)._state = state
    _acq(scope).num_acquisitions = 7.0
    assert scope.acquisition_status() == (running, 7)


def test_stopped_new_acquisition_decodes_once(scope: BetterScope) -> None:
    monitor = LiveMonitor()
    monitor.start()
    _acq(scope).scope_is(running=False, num=3)
    assert _poll(scope, monitor) is LiveAction.TRANSFER  # current stopped capture
    monitor.cycle_done(0.1, 0.1)

    assert _poll(scope, monitor) is LiveAction.NONE  # NUMACq unchanged
    assert _poll(scope, monitor) is LiveAction.NONE

    _acq(scope).scope_is(running=False, num=1)  # Single pressed, finished between polls
    assert _poll(scope, monitor) is LiveAction.TRANSFER
    monitor.cycle_done(0.1, 0.1)
    assert monitor.decodes == 2 and monitor.skipped == 0


def test_single_twice_with_same_numacq_is_caught_via_running_state(scope: BetterScope) -> None:
    monitor = LiveMonitor()
    monitor.start()
    _acq(scope).scope_is(running=False, num=1)
    assert _poll(scope, monitor) is LiveAction.TRANSFER
    monitor.cycle_done(0.1, 0.1)

    _acq(scope).scope_is(running=True, num=0)  # Single pressed, waiting for trigger
    assert _poll(scope, monitor) is LiveAction.NONE
    _acq(scope).scope_is(running=False, num=1)  # same NUMACq as before
    assert _poll(scope, monitor) is LiveAction.TRANSFER


def test_running_needs_decode_while_running(scope: BetterScope) -> None:
    monitor = LiveMonitor()
    monitor.start()
    _acq(scope).scope_is(running=True, num=5)
    assert _poll(scope, monitor) is LiveAction.NONE
    _acq(scope).scope_is(running=True, num=9)
    assert _poll(scope, monitor) is LiveAction.NONE

    monitor.decode_while_running = True
    _acq(scope).scope_is(running=True, num=10)
    assert _poll(scope, monitor) is LiveAction.STOP_TRANSFER_RUN

    capture = live_cycle(scope, ["CH1", "CH2"], stop_first=True)
    assert _acq(scope).writes == ["STATE STOP", "STATE RUN"]
    assert list(capture.waveforms) == ["CH1", "CH2"]
    assert capture.num_at_stop == 10


def test_decode_while_running_without_stop(scope: BetterScope) -> None:
    monitor = LiveMonitor(decode_while_running=True, stop_for_transfer=False)
    monitor.start()
    _acq(scope).scope_is(running=True, num=2)
    assert _poll(scope, monitor) is LiveAction.TRANSFER
    live_cycle(scope, ["CH1"], stop_first=False)
    assert _acq(scope).writes == []


def test_skip_while_busy_and_count(scope: BetterScope) -> None:
    monitor = LiveMonitor(decode_while_running=True)
    monitor.start()
    _acq(scope).scope_is(running=True, num=4)
    assert _poll(scope, monitor) is LiveAction.STOP_TRANSFER_RUN
    monitor.transferred(4)

    # RUN reset the counter; acquisitions keep coming while the decode runs.
    for num in (2, 5, 5, 8):
        _acq(scope).scope_is(running=True, num=num)
        assert _poll(scope, monitor) is LiveAction.NONE
    assert monitor.skipped == 8

    monitor.cycle_done(0.2, 0.3)
    _acq(scope).scope_is(running=True, num=11)
    assert _poll(scope, monitor) is LiveAction.STOP_TRANSFER_RUN
    assert monitor.skipped == 8 + 2  # 9 and 10 were never transferred
    assert monitor.decodes == 1
    assert monitor.last is not None and monitor.last.decode_s == 0.3


def test_stopped_acquisition_while_busy_waits_instead_of_skipping(scope: BetterScope) -> None:
    monitor = LiveMonitor(decode_while_running=True)
    monitor.start()
    _acq(scope).scope_is(running=True, num=1)
    assert _poll(scope, monitor) is LiveAction.STOP_TRANSFER_RUN
    monitor.transferred(1)

    _acq(scope).scope_is(running=False, num=6)  # user pressed Stop mid-decode
    assert _poll(scope, monitor) is LiveAction.NONE
    assert _poll(scope, monitor) is LiveAction.NONE
    monitor.cycle_done(0.1, 0.1)
    assert _poll(scope, monitor) is LiveAction.TRANSFER
    assert monitor.skipped == 0


def test_not_polling_when_off(scope: BetterScope) -> None:
    monitor = LiveMonitor()
    _acq(scope).scope_is(running=False, num=3)
    assert not monitor.polling
    assert _poll(scope, monitor) is LiveAction.NONE
    monitor.start()
    monitor.stop()
    assert _poll(scope, monitor) is LiveAction.NONE


def test_arm_single_one_shot_and_repeat(scope: BetterScope) -> None:
    previous = scope.arm_single()
    assert previous == "RUNSTOP"
    assert _acq(scope).writes == ["STOPAFTER SEQUENCE", "STATE RUN"]

    monitor = LiveMonitor()
    monitor.armed_single()
    assert monitor.polling and not monitor.live
    _acq(scope).scope_is(running=True, num=0)  # waiting for trigger
    assert _poll(scope, monitor) is LiveAction.NONE
    _acq(scope).scope_is(running=False, num=1)
    assert _poll(scope, monitor) is LiveAction.TRANSFER
    assert monitor.cycle_done(0.1, 0.1) is False  # live off: one shot
    assert not monitor.polling

    monitor.start()
    monitor.armed_single()
    _acq(scope).scope_is(running=False, num=1)  # same NUMACq, but armed
    assert _poll(scope, monitor) is LiveAction.TRANSFER
    assert monitor.cycle_done(0.1, 0.1) is True  # live on: re-arm
    assert scope.get_stop_after() == "SEQUENCE"


def test_armed_single_is_not_stopped_early_by_decode_while_running(scope: BetterScope) -> None:
    monitor = LiveMonitor(decode_while_running=True)
    monitor.start()
    monitor.armed_single()
    _acq(scope).scope_is(running=True, num=1)
    assert _poll(scope, monitor) is LiveAction.NONE


def test_live_cycle_restarts_scope_when_transfer_fails(scope: BetterScope) -> None:
    scope._instrument.waveforms = FakeWaveforms(fail=True)
    with pytest.raises(TimeoutError):
        live_cycle(scope, ["CH1"], stop_first=True)
    assert _acq(scope).writes == ["STATE STOP", "STATE RUN"]
