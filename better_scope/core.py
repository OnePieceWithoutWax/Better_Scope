"""Better_Scope backend.

GUI-agnostic core that handles instrument discovery, connection, channel
configuration (naming, scale, offset, position, label placement), trigger
control, screenshot capture, waveform acquisition and file saving. Holds the
pymeasure instrument directly -- there is no wrapper layer. Designed to run
standalone (e.g. in a Jupyter notebook or script) without the GUI.

Set operations are verified by reading the value back from the instrument and
logging a warning on mismatch, so silent driver/firmware discrepancies surface
in the log.
"""

import logging
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from better_scope.config import AppConfig
from better_scope.instruments.discovery import find_instruments
from better_scope.instruments.drivers import driver_for
from better_scope.logger import ListHandler, setup_logger
from better_scope.utils import (
    filename_with_suffix,
    get_filename_with_datestamp,
    get_next_incremented_filename,
)
from better_scope.version import __version__, log_version_info

logger = logging.getLogger(__name__)

# Editable channel field -> pymeasure ScopeChannel attribute.
_CHANNEL_FIELD_ATTR: dict[str, str] = {
    "name": "label_name",
    "scale": "scale",
    "offset": "offset",
    "position": "position",
    "label_x": "label_x",
    "label_y": "label_y",
}

# Editable trigger field -> pymeasure Trigger attribute.
_TRIGGER_FIELD_ATTR: dict[str, str] = {
    "mode": "a_mode",
    "type": "a_type",
    "source": "a_edge_source",
    "slope": "a_edge_slope",
    "coupling": "a_edge_coupling",
    "level": "a_level",
}


class BetterScope:
    """Backend controller for the oscilloscope tool (no GUI dependencies)."""

    def __init__(self) -> None:
        self.log_handler: ListHandler = setup_logger()
        logger.info("BetterScope initializing...")
        log_version_info()

        self._instrument: Any = None
        self.scope_addr: str | None = None
        self.instrument_list: list[dict[str, Any]] = []
        self.device_id: str | None = None
        self.meta: dict[str, Any] = {}

        self.config = AppConfig()
        self.recent: dict[str, Any] = {
            "save_dir": None,
            "filename": None,
            "suffix": None,
            "save_path": None,
            "screenshot_data": None,
            "metadata": None,
        }
        logger.info("Application initialized")

    @property
    def version(self) -> str:
        """Application version string."""
        return __version__

    # -- Discovery & connection ------------------------------------------------

    def scan_for_instruments(self) -> list[dict[str, Any]]:
        """Scan for connected VISA instruments and store the result."""
        logger.info("Scanning for instruments...")
        self.instrument_list = find_instruments()
        logger.info(f"Found {len(self.instrument_list)} instrument(s)")
        for instr in self.instrument_list:
            logger.debug(
                f"  - {instr.get('manufacturer', 'Unknown')} "
                f"{instr.get('model_num', 'Unknown')} at {instr.get('addr', 'Unknown')}"
            )
        return self.instrument_list

    def auto_setup_scope(self) -> bool:
        """Connect to the last-used scope if present, else any compatible one."""
        last_scope = self.config.last_connected_scope
        if last_scope:
            last_serial = last_scope.get("serial_num")
            last_addr = last_scope.get("addr")
            for instr in self.instrument_list:
                serial_match = last_serial and instr.get("serial_num") == last_serial
                addr_match = last_addr and instr.get("addr") == last_addr
                if (serial_match or addr_match) and driver_for(instr):
                    logger.info(
                        f"Reconnecting to last scope: {instr.get('model_num', 'Unknown')}"
                    )
                    return self.setup_scope(instr["addr"], instr_info=instr)

        for instr in self.instrument_list:
            if driver_for(instr):
                logger.info(f"Found compatible scope: {instr.get('model_num', 'Unknown')}")
                return self.setup_scope(instr["addr"], instr_info=instr)

        logger.warning("No compatible scope found")
        return False

    def setup_scope(
        self, address: str, instr_info: dict[str, Any] | None = None
    ) -> bool:
        """Connect to a scope at ``address``.

        Args:
            address: VISA resource address.
            instr_info: Optional discovered instrument-info dict (for the driver
                lookup and metadata). Resolved from the scan list if omitted.

        Returns:
            ``True`` on success, ``False`` otherwise.
        """
        if instr_info is None:
            instr_info = next(
                (i for i in self.instrument_list if i.get("addr") == address), None
            )
        driver = driver_for(instr_info) if instr_info else None
        if driver is None:
            logger.error("No scope driver available for this instrument")
            return False

        try:
            self._instrument = driver(address)
        except Exception as e:
            logger.error(f"Failed to connect to {address}: {e}", exc_info=True)
            self._instrument = None
            return False

        self.scope_addr = address
        self.device_id = address
        if instr_info:
            model = instr_info.get("model_num", "")
            serial = instr_info.get("serial_num", "")
            if model and serial:
                self.device_id = f"{model} (SN: {serial})"
            self.config.last_connected_scope = dict(instr_info)
        logger.info(f"Connected: {self.device_id}")
        return True

    def disconnect(self) -> bool:
        """Disconnect from the scope if connected."""
        if self._instrument is None:
            return False
        try:
            adapter = getattr(self._instrument, "adapter", None)
            if adapter is not None:
                adapter.close()
        except Exception as e:
            logger.warning(f"Error during disconnect: {e}")
        finally:
            self._instrument = None
            self.scope_addr = None
        logger.info("Disconnected from scope")
        return True

    def is_connected(self) -> bool:
        """Whether a scope is currently connected."""
        return self._instrument is not None

    def get_device_info(self) -> str | None:
        """Human-readable identifier for the connected device, if any."""
        return self.device_id if self.is_connected() else None

    # -- Verified set helper ---------------------------------------------------

    @staticmethod
    def _values_match(expected: Any, actual: Any) -> bool:
        """Compare an intended value to the read-back value (type-aware)."""
        if isinstance(expected, bool):
            return bool(actual) == expected
        if isinstance(expected, (int, float)):
            try:
                return math.isclose(
                    float(actual), float(expected), rel_tol=1e-3, abs_tol=1e-9
                )
            except (TypeError, ValueError):
                return False
        return str(actual).strip().strip('"') == str(expected).strip()

    def _apply_verified(
        self, obj: Any, attr: str, value: Any, label: str
    ) -> tuple[Any, bool]:
        """Set ``obj.attr = value`` then read it back and verify.

        Args:
            obj: The pymeasure channel/trigger object.
            attr: Attribute (pymeasure property) name.
            value: Value to write.
            label: Human-readable label for log messages.

        Returns:
            ``(readback_value, matched)``. ``matched`` is ``False`` if the write
            failed, the readback failed, or the values differ.
        """
        try:
            setattr(obj, attr, value)
        except Exception as e:
            logger.error(f"{label}: failed to set {attr}={value!r}: {e}")
            return None, False
        try:
            actual = getattr(obj, attr)
        except Exception as e:
            logger.warning(f"{label}: set {attr}={value!r} but readback failed: {e}")
            return None, False

        if self._values_match(value, actual):
            logger.debug(f"{label}: {attr} confirmed = {actual!r}")
            return actual, True
        logger.warning(f"{label}: wrote {attr}={value!r} but scope reports {actual!r}")
        return actual, False

    @staticmethod
    def _read(obj: Any, attr: str, label: str) -> Any:
        """Read a pymeasure property, returning ``None`` (and logging) on error."""
        try:
            return getattr(obj, attr)
        except Exception as e:
            logger.debug(f"{label}: read {attr} failed: {e}")
            return None

    # -- Channels --------------------------------------------------------------

    @property
    def analog_channel_count(self) -> int:
        """Number of analog channels on the connected scope (0 if none)."""
        if self._instrument is None:
            return 0
        count = getattr(self._instrument, "analog_channels_count", None)
        if isinstance(count, int) and count > 0:
            return count
        return len(getattr(self._instrument, "channels", ()))

    def _channel(self, index: int) -> Any:
        """Return the 1-based analog channel object, validating the range."""
        channels = getattr(self._instrument, "channels", ())
        if not (1 <= index <= len(channels)):
            raise ValueError(f"Channel index {index} out of range (1..{len(channels)})")
        return channels[index - 1]

    def get_channels(self) -> list[dict[str, Any]]:
        """Read per-channel state from the instrument.

        Returns:
            One dict per analog channel with ``index``, ``name``, ``enabled``,
            ``scale``, ``offset``, ``position``, ``label_x`` and ``label_y``.
        """
        if not self.is_connected():
            return []
        channels = getattr(self._instrument, "channels", ())
        result: list[dict[str, Any]] = []
        for i, channel in enumerate(channels, start=1):
            label = f"CH{i}"
            name = self._read(channel, "label_name", label)
            result.append(
                {
                    "index": i,
                    "name": "" if name is None else str(name).strip().strip('"'),
                    "enabled": bool(self._read(channel, "enable", label)),
                    "scale": self._read(channel, "scale", label),
                    "offset": self._read(channel, "offset", label),
                    "position": self._read(channel, "position", label),
                    "label_x": self._read(channel, "label_x", label),
                    "label_y": self._read(channel, "label_y", label),
                }
            )
        return result

    def set_channel_properties(
        self, index: int, props: dict[str, Any]
    ) -> dict[str, Any]:
        """Write channel properties with read-back verification.

        Args:
            index: 1-based channel index.
            props: Mapping of field name (``name``, ``scale``, ``offset``,
                ``position``, ``label_x``, ``label_y``) to the desired value.

        Returns:
            ``{"applied": {field: readback_value}, "mismatches": [field, ...]}``.
        """
        if not self.is_connected():
            raise ValueError("No oscilloscope connected")
        channel = self._channel(index)
        applied: dict[str, Any] = {}
        mismatches: list[str] = []
        for field, value in props.items():
            attr = _CHANNEL_FIELD_ATTR.get(field)
            if attr is None:
                continue
            actual, ok = self._apply_verified(channel, attr, value, f"CH{index} {field}")
            applied[field] = actual
            if not ok:
                mismatches.append(field)
        return {"applied": applied, "mismatches": mismatches}

    def set_channel_name(self, index: int, name: str) -> bool:
        """Set a channel label with verification; return ``True`` if confirmed."""
        result = self.set_channel_properties(index, {"name": name})
        return "name" not in result["mismatches"]

    # -- Trigger ---------------------------------------------------------------

    @property
    def _trigger(self) -> Any:
        """The pymeasure trigger sub-object."""
        if self._instrument is None:
            raise ValueError("No oscilloscope connected")
        return self._instrument.trigger

    def get_trigger(self) -> dict[str, Any]:
        """Read the current (A-)trigger settings and live status."""
        if not self.is_connected():
            return {}
        t = self._trigger
        return {
            "mode": self._read(t, "a_mode", "trigger"),
            "type": self._read(t, "a_type", "trigger"),
            "source": self._read(t, "a_edge_source", "trigger"),
            "slope": self._read(t, "a_edge_slope", "trigger"),
            "coupling": self._read(t, "a_edge_coupling", "trigger"),
            "level": self._read(t, "a_level", "trigger"),
            "state": self._read(t, "state", "trigger"),
            "frequency": self._read(t, "frequency", "trigger"),
        }

    def apply_trigger(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Write trigger settings with read-back verification.

        Args:
            settings: Mapping of field (``mode``, ``type``, ``source``,
                ``slope``, ``coupling``, ``level``) to the desired value.

        Returns:
            ``{"applied": {field: readback_value}, "mismatches": [field, ...]}``.
        """
        if not self.is_connected():
            raise ValueError("No oscilloscope connected")
        t = self._trigger
        applied: dict[str, Any] = {}
        mismatches: list[str] = []
        for field, value in settings.items():
            attr = _TRIGGER_FIELD_ATTR.get(field)
            if attr is None:
                continue
            actual, ok = self._apply_verified(t, attr, value, f"trigger {field}")
            applied[field] = actual
            if not ok:
                mismatches.append(field)
        return {"applied": applied, "mismatches": mismatches}

    def force_trigger(self) -> None:
        """Force an immediate trigger event."""
        if not self.is_connected():
            raise ValueError("No oscilloscope connected")
        # The driver exposes this as a no-argument SCPI setting; issue it directly.
        self._instrument.write("TRIGger:FORCe")
        logger.info("Forced trigger")

    def set_trigger_level_50(self) -> None:
        """Set the A-trigger level to 50% of the current signal."""
        if not self.is_connected():
            raise ValueError("No oscilloscope connected")
        self._instrument.write("TRIGger:A SETLevel")
        logger.info("Set trigger level to 50%")

    # -- Waveforms -------------------------------------------------------------

    def acquire_waveforms(self, sources: list[str]) -> dict[str, tuple]:
        """Acquire scaled ``(time, voltage)`` arrays for ``sources``.

        Args:
            sources: Source names such as ``["CH1", "CH2"]``.

        Returns:
            Mapping of source name to a ``(time_array, voltage_array)`` tuple.
        """
        if not self.is_connected():
            raise ValueError("No oscilloscope connected")
        if not sources:
            return {}
        waveforms = getattr(self._instrument, "waveforms", None)
        if waveforms is None:
            raise AttributeError("Instrument has no waveform-transfer interface")
        if hasattr(waveforms, "get_multiple_waveforms"):
            return waveforms.get_multiple_waveforms(sources)
        return {src: waveforms.get_scaled_waveform(src) for src in sources}

    # -- Capture & saving ------------------------------------------------------

    def capture(
        self,
        save_dir: str | Path | None = None,
        filename: str | None = None,
        suffix: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bytes:
        """Capture a screenshot, save it to disk, and return the image bytes.

        Args:
            save_dir: Directory to save into (defaults to config).
            filename: Base filename without path (defaults to config).
            suffix: File extension (defaults to config file format).
            metadata: Optional metadata saved to a companion ``*_metadata.txt``.

        Returns:
            The captured PNG image bytes.

        Raises:
            ValueError: If no scope is connected.
        """
        logger.info("Starting capture...")
        if not self.is_connected():
            raise ValueError("No oscilloscope connected")

        if save_dir is None:
            save_dir = self.config.get_save_directory()
        if filename is None:
            filename = self.config.default_filename
        if suffix is None:
            suffix = self.config.formatted_file_format

        save_path = Path(save_dir)
        save_path.mkdir(parents=True, exist_ok=True)

        screenshot_data = self._instrument.capture_screenshot()
        logger.debug(f"Received {len(screenshot_data)} bytes of image data")

        self.save_file(save_dir, filename, suffix, screenshot_data)

        if metadata:
            self.meta = metadata
            self._save_metadata(save_dir, filename, suffix)
            self.config.last_used_metadata = metadata

        self.config.set_save_directory(save_dir)
        self.recent = {
            "save_dir": str(save_dir),
            "filename": filename,
            "suffix": suffix,
            "save_path": save_path,
            "screenshot_data": screenshot_data,
            "metadata": metadata,
        }

        if self.config.auto_copy_to_clipboard:
            self.copy_to_clipboard(screenshot_data)

        logger.info("Capture completed successfully")
        return screenshot_data

    def save_file(
        self, save_dir: str | Path, filename: str, suffix: str, file_data: bytes
    ) -> Path:
        """Write ``file_data`` to ``save_dir/filename.suffix`` and return the path."""
        filename = filename_with_suffix(filename, suffix)
        file_path = Path(save_dir) / filename
        with open(file_path, "wb") as f:
            f.write(file_data)
        logger.info(f"Saved: {file_path}")
        return file_path

    def _save_metadata(self, save_dir: str | Path, filename: str, suffix: str) -> None:
        """Write a companion ``*_metadata.txt`` next to the saved image."""
        image_name = filename_with_suffix(filename, suffix)
        path = Path(save_dir) / image_name
        metadata_path = path.with_stem(f"{path.stem}_metadata").with_suffix(".txt")

        with open(metadata_path, "w", encoding="utf-8") as f:
            f.write(f"Image file: {path.name}\n")
            f.write(f"Capture time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            if self.device_id:
                f.write(f"Device: {self.device_id}\n\n")
            f.write("Custom Metadata:\n")
            for key, value in self.meta.items():
                f.write(f"{key}: {value}\n")

    def get_capture_filename(
        self,
        save_dir: str | Path | None = None,
        base_filename: str | None = None,
        suffix: str | None = None,
    ) -> str:
        """Generate a filename per config (auto-increment / datestamp / plain)."""
        if save_dir is None:
            save_dir = self.config.get_save_directory()
        if base_filename is None:
            base_filename = self.config.default_filename
        if suffix is None:
            suffix = self.config.formatted_file_format

        if self.config.auto_increment:
            return get_next_incremented_filename(save_dir, base_filename, suffix)
        if self.config.datestamp:
            return get_filename_with_datestamp(save_dir, base_filename, suffix)
        return filename_with_suffix(base_filename, suffix)

    # -- Logging & clipboard ---------------------------------------------------

    def save_log(self, filename: str | None = None) -> Path:
        """Save the in-memory log to the app data directory."""
        if filename is None:
            timestamp = datetime.now().strftime("%Y.%m.%d_%H.%M.%S")
            filename = f"better_scope_log_{timestamp}.txt"
        log_path = self.config._app_data_dir / filename  # type: ignore[operator]
        return self.log_handler.save(log_path, self.version)

    def copy_to_clipboard(self, image_data: bytes | None = None) -> bool:
        """Copy PNG image bytes to the Windows clipboard.

        Args:
            image_data: PNG bytes; defaults to the most recent capture.

        Returns:
            ``True`` on success, ``False`` otherwise.
        """
        if image_data is None:
            image_data = self.recent.get("screenshot_data")
        if not image_data:
            logger.warning("No image data available for clipboard")
            return False

        try:
            import io

            import win32clipboard
            import win32con
            from PIL import Image

            image = Image.open(io.BytesIO(image_data))
            output = io.BytesIO()
            image.convert("RGB").save(output, "BMP")
            bmp_data = output.getvalue()[14:]  # Strip the 14-byte BMP file header.

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, bmp_data)
            win32clipboard.CloseClipboard()
            logger.info("Image copied to clipboard")
            return True
        except ImportError as e:
            logger.error(f"Missing dependency for clipboard: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to copy to clipboard: {e}")
            return False
