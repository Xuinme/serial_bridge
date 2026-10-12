"""Shared mock serial device for COM-free bridge integration tests.

Simulates a serial port so full data-flow tests can run without a
physical COM device.  Bytes written by the bridge are accumulated in
``written``; call ``device_echo(data)`` to simulate the COM device
sending data back to the bridge.
"""


class MockSerial:
    def __init__(self):
        self.is_open = True
        self.written = bytearray()
        self._echo_queue = bytearray()
        self.in_waiting = 0

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def read(self, n: int) -> bytes:
        take = min(n, len(self._echo_queue))
        result = bytes(self._echo_queue[:take])
        self._echo_queue = self._echo_queue[take:]
        self.in_waiting = len(self._echo_queue)
        return result

    def device_echo(self, data: bytes) -> None:
        """Simulate the COM device sending data back to the bridge."""
        self._echo_queue.extend(data)
        self.in_waiting = len(self._echo_queue)

    def close(self) -> None:
        self.is_open = False
