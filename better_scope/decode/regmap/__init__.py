"""Register maps: load a device's registers and label bus traffic with them.

Typical use::

    from better_scope.decode import run
    from better_scope.decode.regmap import DeviceBinding, annotate, load_register_map

    device = load_register_map(Path("pmic.xlsx"))
    result = run([bus], waveforms)
    history = annotate(result, [DeviceBinding(bus.bus_id, device)])
"""

from better_scope.decode.regmap.importers import RegmapImporter, importer_registry, load_register_map
from better_scope.decode.regmap.mapper import DeviceBinding, RegisterAccess, SpiLayout, annotate, map_frames
from better_scope.decode.regmap.model import (
    Device,
    Field,
    FieldValue,
    RegmapError,
    RegmapIssue,
    Register,
    decode_value,
)

__all__ = [
    "Device",
    "DeviceBinding",
    "Field",
    "FieldValue",
    "RegisterAccess",
    "RegmapError",
    "RegmapImporter",
    "RegmapIssue",
    "Register",
    "SpiLayout",
    "annotate",
    "decode_value",
    "importer_registry",
    "load_register_map",
    "map_frames",
]
