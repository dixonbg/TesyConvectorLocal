"""Find a convector's MAC address from its IP (the local API doesn't report it)."""
from __future__ import annotations

import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac

_HEX = re.compile(r"[^0-9a-f]")


def norm_mac(mac: str | None) -> str:
    """'1C:9D:C2:33:D0:60', '1c9dc233d060' -> '1c9dc233d060' (for comparing)."""
    return _HEX.sub("", (mac or "").lower())


def _arp_lookup(ip: str) -> str | None:
    try:
        with open("/proc/net/arp", encoding="ascii") as arp:
            for line in arp.readlines()[1:]:
                parts = line.split()
                # IP, HW type, flags (0x2 = complete), MAC, mask, device
                if len(parts) >= 4 and parts[0] == ip and parts[2] != "0x0":
                    if norm_mac(parts[3]) not in ("", "000000000000"):
                        return format_mac(parts[3])
    except OSError:
        pass
    return None


async def async_mac_for_ip(hass: HomeAssistant, ip: str) -> str | None:
    """MAC from the host's neighbour table. Works where HA shares the host network (HA OS,
    Docker host networking) and right after we talked to the device; None otherwise."""
    return await hass.async_add_executor_job(_arp_lookup, ip)
