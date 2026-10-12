"""
Automated tests for the multi-session (v2) settings persistence and v1
migration in app.profiles (TASK-007).

Every test redirects the settings path into a TemporaryDirectory and restores
it afterwards.  It verifies v1 migration, v2 round-trip, invalid JSON
protection, one-time backup, repeat-load idempotency, atomic writes and that the
legacy single-config API is untouched.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.profiles as profiles
from app.models import AppSettings, SessionConfig

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


def test_v1_migration():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            tp.write_text(json.dumps({
                "last_com_port": "COM9", "baud_rate": 9600, "tcp_port": 8888,
                "newline_mode": "lf", "close_action": "exit",
            }), encoding="utf-8")
            app = profiles.load_app_settings()
            check("v1 schema_version", app.schema_version == 2)
            check("v1 single session", len(app.sessions) == 1)
            s = app.sessions[0]
            check("v1 session fields",
                  s.com_port == "COM9" and s.baud_rate == 9600 and
                  s.tcp_port == 8888 and s.newline_mode == "lf")
            check("v1 close_action", app.close_action == "exit")
            check("v1 traffic logging defaults off",
                  app.traffic_log_enabled is False and app.traffic_log_directory == "")


def test_v1_no_com_default_session():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            tp.write_text(json.dumps({"last_com_port": ""}), encoding="utf-8")
            app = profiles.load_app_settings()
            check("no COM keeps editable session",
                  len(app.sessions) == 1 and app.sessions[0].com_port == "")


def test_v1_invalid_close_falls_back():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            tp.write_text(json.dumps({"close_action": "bogus"}), encoding="utf-8")
            app = profiles.load_app_settings()
            check("v1 invalid close -> ask", app.close_action == "ask")


def test_missing_file_default():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            app = profiles.load_app_settings()
            check("missing -> version 2", app.schema_version == 2)
            check("missing -> one default session", len(app.sessions) == 1)
            check("missing -> editable default",
                  app.sessions[0].com_port == "" and app.sessions[0].name == "Connection 1")


def test_backup_on_first_save():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            v1 = json.dumps({"last_com_port": "COM9", "baud_rate": 9600})
            tp.write_text(v1, encoding="utf-8")
            app = profiles.load_app_settings()
            profiles.save_app_settings(app)
            backup = tp.with_name(tp.name + ".bak")
            check("backup created once", backup.exists())
            check("backup preserves original v1",
                  json.loads(backup.read_text(encoding="utf-8")) == json.loads(v1))
            backup_text = backup.read_text(encoding="utf-8")
            app2 = profiles.load_app_settings()
            app2.close_action = "minimize_to_tray"
            profiles.save_app_settings(app2)
            check("backup preserved on second save",
                  backup.read_text(encoding="utf-8") == backup_text)


def test_backup_failure_preserves_source():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            original = json.dumps({"last_com_port": "COM9", "baud_rate": 9600})
            tp.write_text(original, encoding="utf-8")
            app = profiles.load_app_settings()
            real_replace = profiles.os.replace

            def fail_backup_replace(src, dst):
                if str(dst).endswith(".bak"):
                    raise OSError("simulated backup failure")
                return real_replace(src, dst)

            try:
                with mock.patch.object(
                    profiles.os, "replace", side_effect=fail_backup_replace
                ):
                    profiles.save_app_settings(app)
                check("backup failure raises", False)
            except OSError:
                check("backup failure raises", True)
            check("backup failure preserves source",
                  tp.read_text(encoding="utf-8") == original)
            check("backup failure leaves no backup",
                  not tp.with_name(tp.name + ".bak").exists())
            check("backup failure removes temp files",
                  not any(p.suffix == ".tmp" for p in Path(td).iterdir()))


def test_v2_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            app = AppSettings(
                schema_version=2, close_action="minimize_to_tray",
                active_session_id="abc",
                traffic_log_enabled=True,
                traffic_log_directory=str(Path(td) / "traffic logs"),
                sessions=[
                    SessionConfig(session_id="abc", name="A", com_port="COM1",
                                  baud_rate=115200, tcp_port=9999,
                                  newline_mode="crlf", auto_reconnect=True),
                    SessionConfig(session_id="def", name="B", com_port="COM2",
                                  baud_rate=9600, tcp_port=10000,
                                  newline_mode="lf", auto_reconnect=False),
                ],
            )
            profiles.save_app_settings(app)
            loaded = profiles.load_app_settings()
            check("v2 roundtrip count", len(loaded.sessions) == 2)
            check("v2 roundtrip close/active",
                  loaded.close_action == "minimize_to_tray" and loaded.active_session_id == "abc")
            check("v2 roundtrip fields",
                  loaded.sessions[0].com_port == "COM1" and
                  loaded.sessions[0].baud_rate == 115200 and
                  loaded.sessions[0].auto_reconnect is True and
                  loaded.sessions[1].newline_mode == "lf")
            check("v2 traffic log settings roundtrip",
                  loaded.traffic_log_enabled is True and
                  loaded.traffic_log_directory == str(Path(td) / "traffic logs"))
            check("v2 no backup created",
                  not (tp.with_name(tp.name + ".bak")).exists())


def test_invalid_json_load_preserves_file():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            bad = "{not json"
            tp.write_text(bad, encoding="utf-8")
            app = profiles.load_app_settings()
            check("invalid load -> default", app.schema_version == 2 and len(app.sessions) == 1)
            check("invalid load leaves file unchanged", tp.read_text(encoding="utf-8") == bad)
            check("invalid load no backup", not (tp.with_name(tp.name + ".bak")).exists())


def test_unknown_schema_is_not_migrated_or_overwritten():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            original = json.dumps({"schema_version": 3, "sessions": [{"future": True}]})
            tp.write_text(original, encoding="utf-8")
            try:
                profiles.load_app_settings()
                check("unknown schema load raises", False)
            except ValueError:
                check("unknown schema load raises", True)
            try:
                profiles.save_app_settings(AppSettings())
                check("unknown schema save refuses overwrite", False)
            except ValueError:
                check("unknown schema save refuses overwrite", True)
            check("unknown schema source preserved",
                  tp.read_text(encoding="utf-8") == original)
            check("unknown schema creates no backup",
                  not tp.with_name(tp.name + ".bak").exists())


def test_save_over_invalid_raises():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            bad = "{not json"
            tp.write_text(bad, encoding="utf-8")
            try:
                profiles.save_app_settings(profiles.load_app_settings())
                check("save over invalid raises ValueError", False)
            except ValueError:
                check("save over invalid raises ValueError", True)
            check("save over invalid leaves file unchanged", tp.read_text(encoding="utf-8") == bad)


def test_save_refuses_non_object():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            tp.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            try:
                profiles.save_app_settings(AppSettings())
                check("save over non-object raises ValueError", False)
            except ValueError:
                check("save over non-object raises ValueError", True)
            check("non-object file preserved", tp.read_text(encoding="utf-8") == json.dumps([1, 2, 3]))


def test_repeated_load_no_duplicate():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            tp.write_text(json.dumps({"last_com_port": "COM9"}), encoding="utf-8")
            a1 = profiles.load_app_settings()
            a2 = profiles.load_app_settings()
            check("repeat load same count", len(a1.sessions) == len(a2.sessions) == 1)
            check("repeat load same id", a1.sessions[0].session_id == a2.sessions[0].session_id)


def test_atomic_no_temp_leftover():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            app = AppSettings(sessions=[
                SessionConfig(session_id="a", name="A", com_port="COM1", tcp_port=9999),
            ])
            profiles.save_app_settings(app)
            leftovers = [p.name for p in Path(td).iterdir() if p.suffix == ".tmp"]
            check("no temp leftovers", leftovers == [])
            check("final file is valid v2",
                  json.loads(tp.read_text(encoding="utf-8"))["schema_version"] == 2)


def test_legacy_api_unchanged():
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "settings.json"
        with mock.patch.object(profiles, "_settings_path", return_value=tp):
            tp.write_text(json.dumps({"last_com_port": "COM9", "baud_rate": 9600}), encoding="utf-8")
            cfg = profiles.load_settings()
            check("legacy load keeps fields", cfg.com_port == "COM9" and cfg.baud_rate == 9600)
            cfg.com_port = "COM10"
            profiles.save_settings(cfg)
            back = profiles.load_settings()
            check("legacy save/load roundtrip", back.com_port == "COM10")


# -- Runner ------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    tests = [
        test_v1_migration,
        test_v1_no_com_default_session,
        test_v1_invalid_close_falls_back,
        test_missing_file_default,
        test_backup_on_first_save,
        test_backup_failure_preserves_source,
        test_v2_roundtrip,
        test_invalid_json_load_preserves_file,
        test_unknown_schema_is_not_migrated_or_overwritten,
        test_save_over_invalid_raises,
        test_save_refuses_non_object,
        test_repeated_load_no_duplicate,
        test_atomic_no_temp_leftover,
        test_legacy_api_unchanged,
    ]

    for test in tests:
        try:
            test()
            print(f"  [PASS] {test.__name__}")
        except Exception:
            print(f"  [FAIL] {test.__name__}")
            traceback.print_exc()
            results.append((f"EXCEPTION in {test.__name__}", False, ""))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\nRESULTS: {passed} passed, {failed} failed, {len(results)} total checks")
    sys.exit(0 if failed == 0 else 1)
