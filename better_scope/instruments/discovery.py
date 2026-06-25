"""VISA instrument discovery and ``*IDN?`` parsing."""

import logging
from typing import Any

import pyvisa

logger = logging.getLogger(__name__)


def scpi_id_parser(id_string: str) -> dict[str, str | None]:
    """Parse a ``*IDN?`` response into its components.

    Args:
        id_string: The identification string returned by ``*IDN?``.

    Returns:
        Dict with ``manufacturer``, ``model_num``, ``serial_num`` and
        ``software_rev`` keys (values may be ``None`` if unparsable).
    """
    result: dict[str, str | None] = {
        "manufacturer": None,
        "model_num": None,
        "serial_num": None,
        "software_rev": None,
    }

    if id_string and isinstance(id_string, str):
        parts = id_string.split(",")
        if len(parts) >= 4:
            result["manufacturer"] = parts[0].strip()
            result["model_num"] = parts[1].strip()
            result["serial_num"] = parts[2].strip()
            result["software_rev"] = parts[3].strip()

    return result


def find_instruments() -> list[dict[str, Any]]:
    """Discover and identify all VISA instruments on the system.

    Lists every VISA resource, queries each with ``*IDN?`` and parses the
    response. Failures on individual resources are logged and skipped.

    Returns:
        A list of instrument-info dicts with keys: ``n``, ``addr``, ``id``,
        ``manufacturer``, ``model_num``, ``serial_num``, ``software_rev``.
    """
    rm = pyvisa.ResourceManager()
    found: list[dict[str, Any]] = []

    try:
        for n, addr in enumerate(rm.list_resources()):
            info: dict[str, Any] = {
                "n": n,
                "addr": addr,
                "id": "Not known",
                "manufacturer": None,
                "model_num": None,
                "serial_num": None,
                "software_rev": None,
            }

            try:
                resource = rm.open_resource(addr)
                try:
                    idn = resource.query("*idn?").strip()
                    info["id"] = idn
                    info.update(scpi_id_parser(idn))
                except pyvisa.Error as query_error:
                    logger.warning(f"Cannot query *IDN? for {addr}: {query_error}")
                finally:
                    resource.close()
            except pyvisa.VisaIOError as e:
                logger.warning(f"{n}: {addr}: VISA IO error (check connections): {e}")

            logger.debug(f"{n}: {addr}: {info['id']}")
            found.append(info)
    finally:
        rm.close()

    return found
