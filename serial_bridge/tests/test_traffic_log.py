"""COM-free tests for queue-backed raw serial communication logs."""

import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.traffic_log import TrafficLogWriter, prepare_log_directory


results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


def _log_threads():
    return [t for t in threading.enumerate() if t.name.startswith("traffic-log-")]


def _all_text(path):
    parts = sorted(
        path.parent.glob(path.name + "*"),
        key=lambda p: (p.suffix == ".log", p.name),
    )
    return "".join(p.read_text(encoding="utf-8") for p in parts)


def test_format_and_run_isolation():
    baseline = len(_log_threads())
    with tempfile.TemporaryDirectory() as td:
        writer = TrafficLogWriter(enabled=True, directory=td)
        first = writer.start_run(
            session_id="one", session_name="调试连接", com_port="COM4",
            baud_rate=115200, tcp_port=9999,
        )
        writer.record("SERIAL_TX", b"ping 192.168.1.160\r", "Client-1@127.0.0.1:5000")
        writer.record("SERIAL_RX", b"ok\r\n\x00\x1b[31m\xff", "COM4")
        writer.stop()
        text = _all_text(first)
        check("log file created", first is not None and first.exists(), str(first))
        check("metadata identifies session and COM",
              '"session_id": "one"' in text and '"com_port": "COM4"' in text)
        check("TX includes client source",
              "[SERIAL_TX]" in text and "Client-1@127.0.0.1:5000" in text)
        check("RX includes device source", "[SERIAL_RX]" in text and 'source="COM4"' in text)
        check("hex preserves exact control bytes",
              "6F 6B 0D 0A 00 1B 5B 33 31 6D FF" in text)
        check("text preview escapes line/control bytes",
              'text="ok\\r\\n\\u0000\\u001b[31m\\\\xff"' in text)

        second = writer.start_run(
            session_id="one", session_name="调试连接", com_port="COM4",
            baud_rate=115200, tcp_port=9999,
        )
        writer.record("SERIAL_TX", b"second", "Client-2@127.0.0.1:5001")
        writer.stop()
        check("each start uses a distinct file", second is not None and second != first)
        check("second run does not append to first", "second" not in _all_text(first))
    check("format test leaves no log thread", len(_log_threads()) == baseline)


def test_two_sessions_and_rotation():
    baseline = len(_log_threads())
    with tempfile.TemporaryDirectory() as td:
        a = TrafficLogWriter(enabled=True, directory=td, max_bytes=320, backup_count=2)
        b = TrafficLogWriter(enabled=True, directory=td, max_bytes=320, backup_count=2)
        pa = a.start_run(session_id="a", session_name="A", com_port="COM4",
                         baud_rate=115200, tcp_port=9999)
        pb = b.start_run(session_id="b", session_name="B", com_port="COM18",
                         baud_rate=1500000, tcp_port=10000)
        for index in range(12):
            a.record("SERIAL_TX", (f"A-{index}-" + "x" * 50).encode(), "Client-A")
        b.record("SERIAL_RX", b"only-B", "COM18")
        a.stop()
        b.stop()
        a_text = _all_text(pa)
        b_text = _all_text(pb)
        check("two sessions use separate base files", pa != pb)
        check("session A has no B payload", "only-B" not in a_text)
        check("session B has only its payload", "only-B" in b_text and "A-0" not in b_text)
        check("rotation creates bounded backups",
              pa.with_name(pa.name + ".1").exists()
              and len(list(pa.parent.glob(pa.name + "*"))) <= 3)
    check("rotation test leaves no log thread", len(_log_threads()) == baseline)


def test_queue_overflow_is_nonblocking_and_reported():
    gate = threading.Event()

    class GatedWriter(TrafficLogWriter):
        def _run(self, records, path, metadata):
            gate.wait(timeout=2)
            super()._run(records, path, metadata)

    with tempfile.TemporaryDirectory() as td:
        writer = GatedWriter(enabled=True, directory=td, queue_size=1)
        path = writer.start_run(session_id="q", session_name="Q", com_port="COM1",
                                baud_rate=9600, tcp_port=9000)
        first = writer.record("SERIAL_TX", b"first", "Client-1")
        started = time.perf_counter()
        second = writer.record("SERIAL_TX", b"second", "Client-1")
        elapsed = time.perf_counter() - started
        gate.set()
        writer.stop()
        text = _all_text(path)
        check("queue accepts first and drops overflow", first and not second)
        check("overflow path does not block serial caller", elapsed < 0.1, f"{elapsed:.4f}s")
        check("dropped count is written", "# dropped_records=1" in text)


def test_write_failure_isolated_and_reported_once():
    errors = []
    with tempfile.TemporaryDirectory() as td:
        writer = TrafficLogWriter(enabled=True, directory=td, on_error=errors.append)
        with mock.patch.object(Path, "open", side_effect=OSError("disk unavailable")):
            writer.start_run(session_id="e", session_name="E", com_port="COM2",
                             baud_rate=115200, tcp_port=9999)
            deadline = time.time() + 2
            while not errors and time.time() < deadline:
                time.sleep(0.01)
            writer.record("SERIAL_RX", b"data", "COM2")
            writer.stop()
        check("write failure reported exactly once", len(errors) == 1, repr(errors))
        check("write failure does not leave active logger", not writer.active)


def test_prepare_directory_contract():
    with tempfile.TemporaryDirectory() as td:
        nested = Path(td) / "new" / "logs"
        result = prepare_log_directory(nested)
        check("directory validation creates requested path", result == nested and nested.is_dir())
        check("directory validation removes probe",
              not any(p.name.startswith(".serial-bridge-") for p in nested.iterdir()))
    try:
        prepare_log_directory("")
        check("empty directory rejected", False)
    except ValueError:
        check("empty directory rejected", True)


if __name__ == "__main__":
    import traceback

    tests = [
        test_format_and_run_isolation,
        test_two_sessions_and_rotation,
        test_queue_overflow_is_nonblocking_and_reported,
        test_write_failure_isolated_and_reported_once,
        test_prepare_directory_contract,
    ]
    for test in tests:
        try:
            test()
            print(f"  [PASS] {test.__name__}")
        except Exception:
            print(f"  [FAIL] {test.__name__}")
            traceback.print_exc()
    passed = sum(1 for _name, ok, _detail in results if ok)
    failed = len(results) - passed
    print(f"\nRESULTS: {passed} passed, {failed} failed, {len(results)} total checks")
    sys.exit(0 if failed == 0 else 1)
