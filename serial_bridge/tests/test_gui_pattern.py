"""
Current-architecture test: the BridgeController owns the background
bridge thread and the asyncio event loop; the UI never runs the loop
directly.

This replaces the obsolete "create_task + run_forever in a raw thread"
pattern that predated the Controller.  ``app.controller.SerialBridge`` is
patched with a deterministic fake so the controller's start→running→stop
and thread/loop ownership can be asserted without touching real hardware.
"""
import sys
import time
from pathlib import Path as _Path
from unittest import mock

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import app.controller as controller_mod
from app.controller import BridgeController
from app.models import BridgeConfig
from fake_bridge import FakeBridge

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


def _wait_for(ctrl, *kinds, timeout=3.0):
    expected = set(kinds)
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        for e in ctrl.event_bus.drain():
            seen.add(e.kind)
        if expected <= seen:
            return True
        time.sleep(0.05)
    return expected <= seen


def main():
    results.clear()
    FakeBridge.mode = "success"
    ctrl = BridgeController(BridgeConfig(com_port="MOCK", baud_rate=115200, tcp_port=0))
    thread = None
    loop = None
    bridge = None
    check("Controller starts in a clean initial state",
          ctrl.state.running is False and ctrl.state.starting is False)

    with mock.patch.object(controller_mod, "SerialBridge", FakeBridge):
        try:
            # Start: the controller owns and spawns the background bridge thread.
            ctrl.start_bridge()
            thread = ctrl._thread
            check("Controller owns the background bridge thread",
                  thread is not None)

            # The controller holds the asyncio event loop and bridge instance.
            check("bridge_started event emitted",
                  _wait_for(ctrl, "bridge_started"))
            check("Controller state running after start",
                  ctrl.state.running is True)
            bridge = ctrl._bridge
            loop = ctrl._loop
            check("Controller holds the faked bridge instance",
                  isinstance(bridge, FakeBridge))
            check("Controller holds the asyncio event loop",
                  loop is not None)

            # Clean stop emits bridge_stopped.
            ctrl.stop_bridge()
            check("bridge_stopped event emitted after stop",
                  _wait_for(ctrl, "bridge_stopped"))
            check("Controller state stopped after stop",
                  ctrl.state.running is False)
        finally:
            ctrl.shutdown()

    check("Controller shutdown joins the worker thread",
          thread is not None and thread.is_alive() is False)
    check("Controller closes its event loop",
          loop is not None and loop.is_closed())
    check("Fake bridge releases its handles",
          bridge is not None and bridge.stop_calls == 1 and
          bridge.server is None and bridge.serial_port is None)
    check("Controller shutdown clears thread, loop, and bridge handles",
          ctrl._thread is None and ctrl._loop is None and ctrl._bridge is None)

    print("\n" + "=" * 55)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"RESULTS: {passed}/{len(results)} passed")
    for name, ok, detail in results:
        print(f"  [{'OK' if ok else 'XX'}] {name} {detail}")
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
