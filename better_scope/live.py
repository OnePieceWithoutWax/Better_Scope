"""Live decode: decide when a new scope acquisition should be transferred.

Live mode is triggered by the scope's acquisition state, not by a timer. The
caller polls ``ACQuire:STATE?`` and ``ACQuire:NUMACq?`` (cheap queries, see
:meth:`BetterScope.acquisition_status`) and feeds them to
:meth:`LiveMonitor.poll`, which answers whether to transfer now:

- **Stopped** with a new acquisition: transfer as-is. "New" means NUMACq
  changed, or the scope was seen running since the last transfer (pressing
  Single twice gives NUMACq 1 both times), or an app-armed single finished.
  A new stopped acquisition found while busy waits for the current cycle; the
  data stays on the scope, so nothing is lost.
- **Running** with NUMACq advanced and *decode while running* on: STOP,
  transfer, RUN (:func:`live_cycle`), so every channel comes from the same
  acquisition. Acquisitions that arrive while busy are skipped and counted.

Only one cycle (transfer + decode) runs at a time: :meth:`LiveMonitor.poll`
marks the monitor busy when it asks for a transfer, and
:meth:`LiveMonitor.cycle_done` / :meth:`LiveMonitor.cycle_failed` clear it.
GUI-agnostic and thread-safe (polls run on a worker thread, completions on
the GUI thread).
"""

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class LiveAction(Enum):
    """What :meth:`LiveMonitor.poll` asks the caller to do."""

    NONE = "none"
    TRANSFER = "transfer"
    STOP_TRANSFER_RUN = "stop_transfer_run"


@dataclass
class LiveCapture:
    """Waveforms transferred by :func:`live_cycle`.

    Attributes:
        waveforms: Source name -> ``(t, v)`` arrays.
        num_at_stop: NUMACq read right after STOP (``None`` if not stopped).
        transfer_s: Wall time of the stop/transfer/run sequence in seconds.
    """

    waveforms: dict[str, Any]
    num_at_stop: int | None
    transfer_s: float


@dataclass
class LiveCycle:
    """Timing of the last completed live cycle.

    Attributes:
        finished: When the decode finished.
        transfer_s: Transfer time in seconds.
        decode_s: Decode time in seconds (``None`` if nothing was decoded).
    """

    finished: datetime
    transfer_s: float
    decode_s: float | None


def live_cycle(scope: Any, sources: list[str], stop_first: bool) -> LiveCapture:
    """Transfer ``sources``, stopping the scope around the transfer if asked.

    The scope is RUN again straight after the transfer (before decoding), and
    also when the transfer fails, so a live cycle never leaves it stopped.

    Args:
        scope: A connected :class:`~better_scope.core.BetterScope`.
        sources: Sources to transfer.
        stop_first: STOP before and RUN after the transfer.

    Returns:
        The transferred waveforms and timing.
    """
    start = time.perf_counter()
    num_at_stop: int | None = None
    if stop_first:
        scope.stop_acquisition()
        num_at_stop = scope.acquisition_status()[1]
    try:
        waveforms = scope.acquire_waveforms(sources)
    finally:
        if stop_first:
            scope.run_acquisition()
    return LiveCapture(waveforms, num_at_stop, time.perf_counter() - start)


class LiveMonitor:
    """Turns polled acquisition state into transfer decisions.

    Attributes:
        decode_while_running: Also transfer while the scope runs.
        stop_for_transfer: STOP/RUN around a transfer from a running scope.
        live: Live mode is on.
        armed: An app-armed single sequence is pending.
        repeat_single: Re-arm a single after each decode while live.
        busy: A transfer or decode is in progress.
        skipped: Acquisitions not decoded because a cycle was busy.
        decodes: Completed cycles since live mode started.
        last: Timing of the last completed cycle.
    """

    def __init__(self, decode_while_running: bool = False, stop_for_transfer: bool = True) -> None:
        self.decode_while_running = decode_while_running
        self.stop_for_transfer = stop_for_transfer
        self.live = False
        self.armed = False
        self.repeat_single = False
        self.busy = False
        self.skipped = 0
        self.decodes = 0
        self.last: LiveCycle | None = None
        self._last_seen: int | None = None
        self._saw_running = False
        self._lock = threading.Lock()

    @property
    def polling(self) -> bool:
        """Whether the caller should keep polling the scope."""
        return self.live or self.armed

    def start(self) -> None:
        """Turn live mode on; the current stopped acquisition counts as new."""
        with self._lock:
            self.live = True
            self.skipped = 0
            self.decodes = 0
            self._last_seen = None
            self._saw_running = False

    def stop(self) -> None:
        """Turn live mode off and forget any armed single."""
        with self._lock:
            self.live = False
            self.armed = False
            self.repeat_single = False
            self.busy = False

    def armed_single(self) -> None:
        """Record that a single sequence was just armed on the scope."""
        with self._lock:
            self.armed = True
            self._saw_running = False
            if self.live:
                self.repeat_single = True

    def _count_new(self, num: int) -> int:
        """Acquisitions since the last poll (NUMACq resets on RUN/Single)."""
        last = self._last_seen
        if last is None:
            return 1 if num > 0 else 0
        return num - last if num >= last else num

    def _begin(self, num: int) -> None:
        self.busy = True
        self._last_seen = num
        self._saw_running = False
        self.armed = False

    def poll(self, running: bool, num: int) -> LiveAction:
        """Decide what to do for one polled state.

        Args:
            running: ``ACQuire:STATE?`` is RUN.
            num: ``ACQuire:NUMACq?``.

        Returns:
            :attr:`LiveAction.NONE`, or a transfer request (the monitor is
            then busy until :meth:`cycle_done` or :meth:`cycle_failed`).
        """
        with self._lock:
            if not self.polling:
                return LiveAction.NONE
            new = self._count_new(num)
            if running:
                self._saw_running = True
                wanted = self.live and self.decode_while_running and not self.armed
                if not wanted or new == 0:
                    self._last_seen = num
                    return LiveAction.NONE
                if self.busy:
                    self.skipped += new
                    self._last_seen = num
                    return LiveAction.NONE
                self.skipped += new - 1
                self._begin(num)
                return LiveAction.STOP_TRANSFER_RUN if self.stop_for_transfer else LiveAction.TRANSFER
            if not (new > 0 or self._saw_running or self.armed) or self.busy:
                return LiveAction.NONE
            self._begin(num)
            return LiveAction.TRANSFER

    def transferred(self, num_at_stop: int | None) -> None:
        """Record NUMACq read after a STOP, the baseline for the next poll."""
        if num_at_stop is None:
            return
        with self._lock:
            self._last_seen = num_at_stop

    def cycle_done(self, transfer_s: float, decode_s: float | None) -> bool:
        """Mark the current cycle finished.

        Args:
            transfer_s: Transfer time in seconds.
            decode_s: Decode time in seconds, or ``None`` if nothing decoded.

        Returns:
            Whether to arm another single sequence now.
        """
        with self._lock:
            self.busy = False
            self.decodes += 1
            self.last = LiveCycle(datetime.now(), transfer_s, decode_s)
            return self.live and self.repeat_single

    def cycle_failed(self) -> None:
        """Mark the current cycle abandoned (transfer or decode failed)."""
        with self._lock:
            self.busy = False
