"""TCP listener probing and exclusive socket creation."""

from __future__ import annotations

import socket


LISTEN_HOST = "0.0.0.0"


class TcpPortUnavailableError(ValueError):
    """Raised when a requested listener port is already owned elsewhere."""

    def __init__(self, port: int, suggested_port: int | None = None):
        self.port = port
        self.suggested_port = suggested_port
        message = f"TCP 端口 {port} 已被其他程序占用。"
        if suggested_port is not None:
            message += f"建议改用 {suggested_port}。"
        super().__init__(message)


def create_listener_socket(host: str, port: int) -> socket.socket:
    """Create and bind the socket used by the TCP server.

    Windows permits surprising wildcard/specific-address sharing in some
    configurations. ``SO_EXCLUSIVEADDRUSE`` ensures localhost clients cannot
    be diverted to a different listener after this bind succeeds.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            listener.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        listener.bind((host, port))
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise


def is_tcp_port_available(port: int, host: str = LISTEN_HOST) -> bool:
    """Return whether the application can exclusively listen on ``port``."""
    if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
        return False
    try:
        listener = create_listener_socket(host, port)
    except OSError:
        return False
    listener.close()
    return True
