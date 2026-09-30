"""Plot-tab row for live decode: poll the scope, transfer, decode, repeat.

The decisions live in :class:`better_scope.live.LiveMonitor`; this class
only schedules VISA jobs on the worker (one at a time, serialised by a lock)
and applies their results on the main thread.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

from better_scope.live import LiveAction, LiveCapture, LiveMonitor, live_cycle

if TYPE_CHECKING:
    from better_scope.gui.app import BetterScopeApp

POLL_INTERVAL_S = 0.3
_ERROR = (255, 110, 110)
_NORMAL = (255, 255, 255)


class LivePanel:
    """Live decode controls and the worker jobs behind them."""

    def __init__(self, app: BetterScopeApp) -> None:
        self.app = app
        self.monitor = LiveMonitor()
        self.live_tag = "live_enable"
        self.status_tag = "live_status"
        self._job = False
        self._generation = 0
        self._last_poll = 0.0
        self._restore_stop_after: str | None = None
        self._visa_lock = threading.Lock()

    @property
    def visa_busy(self) -> bool:
        """Whether a live-mode VISA job is in flight."""
        return self._job

    def build(self) -> None:
        """Create the live decode row in the current DearPyGui container."""
        with dpg.group(horizontal=True):
            dpg.add_checkbox(label="Live decode", tag=self.live_tag, callback=lambda s, v: self.set_live(v))
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("Transfer and decode each new acquisition: when the scope stops "
                             "(Single/Stop), or while running if enabled. Slow on long records.", wrap=400)
            dpg.add_checkbox(label="Decode while running", default_value=self.monitor.decode_while_running,
                             callback=lambda s, v: setattr(self.monitor, "decode_while_running", bool(v)))
            dpg.add_checkbox(label="Stop for transfer", default_value=self.monitor.stop_for_transfer,
                             callback=lambda s, v: setattr(self.monitor, "stop_for_transfer", bool(v)))
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("While running: STOP before the transfer and RUN after it, so every "
                             "channel comes from the same acquisition.", wrap=400)
            dpg.add_button(label="Arm single", callback=lambda: self.arm())
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("Set Stop After = Sequence and RUN; decode when the scope stops. "
                             "With Live decode on, re-arms after each decode.", wrap=400)
            dpg.add_text("", tag=self.status_tag)

    # -- controls ----------------------------------------------------------------

    def set_live(self, on: bool) -> None:
        """Turn live mode on or off."""
        if on and not self.app.scope.is_connected():
            dpg.set_value(self.live_tag, False)
            self._status("Connect to a scope first.", error=True)
            return
        if on:
            self.monitor.start()
            self._status("Live: waiting for an acquisition.")
        else:
            self._stop("Live: off.")

    def arm(self) -> None:
        """Arm a single sequence on the scope and decode when it stops."""
        if not self.app.scope.is_connected():
            self._status("Connect to a scope first.", error=True)
            return
        if self._job or self.app.plot_tab.busy:
            self._status("Busy; try again when the current transfer ends.", error=True)
            return
        self._submit_arm()

    def on_connection_changed(self, connected: bool) -> None:
        """Stop live mode when the scope disconnects."""
        if not connected and self.monitor.polling:
            self._stop("Live: off (disconnected).")

    def _stop(self, message: str, error: bool = False) -> None:
        self.monitor.stop()
        self._generation += 1
        dpg.set_value(self.live_tag, False)
        self._status(message, error=error)
        self._restore()

    # -- per-frame ---------------------------------------------------------------

    def tick(self) -> None:
        """Called once per frame: start a poll job when one is due."""
        if not self.monitor.polling or self._job or self.app.plot_tab.busy:
            return
        now = time.monotonic()
        if now - self._last_poll < POLL_INTERVAL_S:
            return
        self._last_poll = now
        sources = self.app.plot_tab.acquire_sources()
        if not sources:
            self._status("Live: no channel to transfer; tick a source or enable a bus.", error=True)
            return
        self._submit_poll(sources)

    # -- worker jobs --------------------------------------------------------------

    def _submit_poll(self, sources: list[str]) -> None:
        scope = self.app.scope
        monitor = self.monitor
        generation = self._generation

        def work() -> LiveCapture | None:
            from better_scope.gui.plot_tab import as_float_arrays

            with self._visa_lock:
                running, num = scope.acquisition_status()
                action = monitor.poll(running, num)
                if action is LiveAction.NONE:
                    return None
                try:
                    capture = live_cycle(scope, sources, action is LiveAction.STOP_TRANSFER_RUN)
                except Exception:
                    monitor.cycle_failed()
                    raise
                monitor.transferred(capture.num_at_stop)
                capture.waveforms = as_float_arrays(capture.waveforms)
                return capture

        self._job = True
        self.app.worker.submit(work, lambda c: self._on_polled(c, generation), self._on_error)

    def _on_polled(self, capture: LiveCapture | None, generation: int) -> None:
        self._job = False
        if capture is None:
            if self.monitor.busy:
                return
            self._show_idle()
            return
        if generation != self._generation:
            self.monitor.cycle_failed()
            return
        self.app.plot_tab.show_acquired(capture.waveforms)
        self._status(f"Live: transferred in {capture.transfer_s:.2f} s, decoding...")
        self.app.decode_tab.decode(self.app.plot_tab.waveforms,
                                   on_done=lambda seconds: self._on_decoded(capture, seconds, generation))

    def _on_decoded(self, capture: LiveCapture, seconds: float | None, generation: int) -> None:
        if generation != self._generation:
            return
        rearm = self.monitor.cycle_done(capture.transfer_s, seconds)
        self._show_idle()
        if rearm:
            self._submit_arm()
        elif not self.monitor.polling:
            self._restore()

    def _submit_arm(self) -> None:
        scope = self.app.scope

        def work() -> str:
            with self._visa_lock:
                return scope.arm_single()

        def done(previous: str) -> None:
            self._job = False
            if self._restore_stop_after is None:
                self._restore_stop_after = previous
            self.monitor.armed_single()
            self._show_idle()

        self._job = True
        self.app.worker.submit(work, done, self._on_error)

    def _restore(self) -> None:
        """Put ``ACQuire:STOPAfter`` back the way it was before Arm single."""
        mode, self._restore_stop_after = self._restore_stop_after, None
        if mode is None or mode == "SEQUENCE" or not self.app.scope.is_connected():
            return
        scope = self.app.scope

        def work() -> None:
            with self._visa_lock:
                scope.set_stop_after(mode)

        self.app.worker.submit(work, None, lambda e: self._status(f"Could not restore Stop After: {e}", error=True))

    def _on_error(self, exc: Exception) -> None:
        self._job = False
        self._stop(f"Live stopped: {exc}", error=True)

    # -- status --------------------------------------------------------------------

    def _show_idle(self) -> None:
        m = self.monitor
        if m.armed:
            state = "armed, waiting for the scope to stop"
        elif m.live:
            state = "waiting" + (" (decoding while running)" if m.decode_while_running else " for Single/Stop")
        else:
            state = "off"
        parts = [f"Live: {state}."]
        if m.last is not None:
            decode = "n/a" if m.last.decode_s is None else f"{m.last.decode_s:.2f} s"
            parts.append(f"Last decode {m.last.finished:%H:%M:%S} (transfer {m.last.transfer_s:.2f} s, "
                         f"decode {decode}).")
        if m.live:
            parts.append(f"{m.decodes} decoded, {m.skipped} skipped.")
        self._status(" ".join(parts))

    def _status(self, text: str, error: bool = False) -> None:
        dpg.set_value(self.status_tag, text)
        dpg.configure_item(self.status_tag, color=_ERROR if error else _NORMAL)
