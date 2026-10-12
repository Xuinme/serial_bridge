"""TCP port probing and exclusive-listener regression tests."""

import asyncio
import socket
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tcp_ports import create_listener_socket, is_tcp_port_available
from bridge import SerialBridge


class FakeSerial:
    def __init__(self):
        self.is_open = True
        self.in_waiting = 0

    def read(self, _size):
        return b""

    def write(self, data):
        return len(data)

    def close(self):
        self.is_open = False


def test_probe_detects_loopback_specific_listener():
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    try:
        assert not is_tcp_port_available(port)
    finally:
        occupied.close()
    assert is_tcp_port_available(port)


def test_exclusive_listener_blocks_second_bind():
    listener = create_listener_socket("0.0.0.0", 0)
    port = listener.getsockname()[1]
    try:
        assert not is_tcp_port_available(port)
    finally:
        listener.close()
    assert is_tcp_port_available(port)


def test_bridge_accepts_prebound_exclusive_socket():
    async def exercise():
        bridge = SerialBridge("MOCK", tcp_port=0)
        fake_serial = FakeSerial()
        writer = None
        with mock.patch("bridge.serial.Serial", return_value=fake_serial):
            try:
                assert await bridge.start()
                port = bridge.server.sockets[0].getsockname()[1]
                _reader, writer = await asyncio.open_connection("127.0.0.1", port)
                await asyncio.sleep(0.05)
                assert bridge.running
            finally:
                if writer is not None:
                    writer.close()
                    await writer.wait_closed()
                await bridge.stop()
        assert not fake_serial.is_open

    asyncio.run(exercise())


if __name__ == "__main__":
    test_probe_detects_loopback_specific_listener()
    print("  [PASS] loopback-specific listener detected")
    test_exclusive_listener_blocks_second_bind()
    print("  [PASS] exclusive listener blocks duplicate bind")
    test_bridge_accepts_prebound_exclusive_socket()
    print("  [PASS] bridge accepts prebound exclusive socket")
    print("\nRESULTS: 3 passed, 0 failed, 3 total checks")
