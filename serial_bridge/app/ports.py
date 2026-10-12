"""COM port enumeration utilities."""

from __future__ import annotations

from dataclasses import dataclass

import serial.tools.list_ports


@dataclass
class PortInfo:
    device: str       # e.g. "COM9"
    description: str  # e.g. "Silicon Labs CP210x USB to UART Bridge"
    label: str        # combined display label


def list_ports() -> list[PortInfo]:
    """Return all available serial ports, USB/Serial devices first."""
    raw = sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)
    result: list[PortInfo] = []
    for p in raw:
        desc = p.description or ""
        label = f"{p.device} - {desc}" if desc else p.device
        result.append(PortInfo(device=p.device, description=desc, label=label))
    return result


def auto_select_port(ports: list[PortInfo]) -> str | None:
    """Pick the best default COM port from a list.

    Prefer devices whose description mentions USB, Serial, or UART.
    Otherwise return the first available device.
    """
    if not ports:
        return None
    for p in ports:
        d = p.description.lower()
        if any(kw in d for kw in ("usb", "serial", "uart", "cp210x", "ftdi", "ch340")):
            return p.device
    return ports[0].device
