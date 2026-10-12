"""Shared asyncio harness helpers for COM-free bridge integration tests.

Provides a ready-to-run mock bridge (serial device + loopback TCP server
on an OS-assigned port) plus a thorough teardown helper.  Each test owns
and fully releases its own resources; teardown closes every client, the
listener, the serial read task and the mock serial, then asserts the
post-conditions so a failing cleanup is visible rather than swallowed.
"""

import asyncio

from bridge import SerialBridge

from mock_serial import MockSerial


async def start_mock_bridge(com: str = "MOCK", baud: int = 115200):
    """Start a SerialBridge on an OS-assigned loopback port over a mock serial.

    Returns ``(bridge, mock, port)``.  Pair with :func:`stop_mock_bridge`.
    """
    bridge = SerialBridge(com, baud, tcp_port=0)
    mock = MockSerial()
    bridge.running = True
    bridge._loop = asyncio.get_running_loop()
    bridge.serial_port = mock
    bridge._serial_task = None
    try:
        bridge.server = await asyncio.start_server(
            lambda r, w: bridge._handle_client(r, w), "127.0.0.1", 0
        )
        port = bridge.server.sockets[0].getsockname()[1]
        bridge._serial_task = asyncio.create_task(bridge._serial_read_loop())
    except BaseException:
        try:
            await stop_mock_bridge(bridge)
        except Exception as cleanup_exc:
            raise RuntimeError(
                "mock bridge startup failed and cleanup was incomplete"
            ) from cleanup_exc
        raise
    return bridge, mock, port


async def stop_mock_bridge(bridge):
    """Fully tear down a mock bridge, asserting the post-conditions.

    Closes every connected client writer (and clears ``bridge._clients``),
    cancels/awaits the serial read task, closes the listener and the mock
    serial.  Cleanup failures are collected and raised as a ``RuntimeError``
    so a test can detect that teardown did not complete; it never silently
    swallows an unexpected exception.  Idempotent.
    """
    problems = []
    bridge.running = False

    # 1. Stop accepting new clients before closing existing connections.
    server = bridge.server
    if server is not None:
        try:
            server.close()
            await server.wait_closed()
        except Exception as exc:
            problems.append(f"server close failed: {exc}")
        else:
            bridge.server = None
        # Proactor accept cancellation settles on the next loop turn.
        await asyncio.sleep(0)

    # 2. Close every connected client and remove only successful closes.
    for cid, cinfo in list(bridge._clients.items()):
        w = cinfo.get("writer")
        if w is None:
            bridge._clients.pop(cid, None)
            continue
        try:
            await close_writer(w)
        except Exception as exc:
            problems.append(f"client close failed: {exc}")
        else:
            bridge._clients.pop(cid, None)

    # 3. Cancel and await the serial read task.
    task = getattr(bridge, "_serial_task", None)
    if task is not None:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # expected when the task is cancelled
        except Exception as exc:
            problems.append(f"serial task failed: {exc}")
        finally:
            bridge._serial_task = None

    # 4. Close the mock serial and drop the handle.
    sp = bridge.serial_port
    if sp is not None:
        try:
            sp.close()
        except Exception as exc:
            problems.append(f"serial close failed: {exc}")
        else:
            bridge.serial_port = None

    # 5. Assert the post-conditions so a failed cleanup is visible.
    if bridge.server is not None:
        problems.append("server still set after cleanup")
    if bridge._clients:
        problems.append("client registry not cleared after cleanup")
    if getattr(bridge, "_serial_task", None) is not None:
        problems.append("serial task still set after cleanup")
    if bridge.serial_port is not None:
        problems.append("serial handle still set after cleanup")
    if sp is not None and sp.is_open:
        problems.append("serial still open after cleanup")

    if problems:
        raise RuntimeError("stop_mock_bridge cleanup incomplete: " + "; ".join(problems))


async def close_writer(writer):
    """Close a client writer and wait for the transport to close.

    Mirrors the bridge's own client-drain so tests can assert
    ``writer.is_closing()`` after release.
    """
    if writer is None:
        return
    writer.close()
    await writer.wait_closed()
    if not writer.is_closing():
        raise RuntimeError("client writer is not closing after wait_closed()")


async def drain(reader: asyncio.StreamReader, timeout: float = 1.0) -> bytes:
    """Read whatever is available within *timeout*, or return b''."""
    try:
        return await asyncio.wait_for(reader.read(4096), timeout=timeout)
    except asyncio.TimeoutError:
        return b""


async def assert_clean(bridge, mock):
    """Assert the mock bridge is fully released after ``stop_mock_bridge``."""
    assert bridge.server is None, "server should be None after cleanup"
    assert not bridge._clients, "client registry should be empty after cleanup"
    task = getattr(bridge, "_serial_task", None)
    assert task is None or task.done(), "serial task should be finished after cleanup"
    assert bridge.serial_port is None, "serial handle should be None after cleanup"
    assert not mock.is_open, "mock serial should be closed after cleanup"
