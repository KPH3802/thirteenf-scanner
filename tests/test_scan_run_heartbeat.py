"""SCANNER-HYGIENE (legacy_estate_audit for thirteenf):
  * add a shared scan_runs heartbeat (13F only had a LOCAL scan_log, so a dead run
    was invisible in the cross-scanner monitor);
  * make the per-signal signal_log write failure loud instead of except: pass.
"""
import sqlite3


def test_log_scan_run_writes_one_row(scanner, tmp_path):
    db = tmp_path / "intel.db"
    scanner.log_scan_run("THIRTEENF", "OK", 5, 2, note="2026-06-30", db_path=str(db))
    rows = sqlite3.connect(str(db)).execute(
        "SELECT scanner, source_status, n_evaluated, n_fired, note FROM scan_runs").fetchall()
    assert rows == [("THIRTEENF", "OK", 5, 2, "2026-06-30")]


def test_log_scan_run_loud_on_bad_path(scanner, capsys):
    scanner.log_scan_run("THIRTEENF", "OK", 1, 0, db_path="/does/not/exist/nope/intel.db")
    assert "[SCAN_RUN_FAIL]" in capsys.readouterr().out


def test_signal_log_loud_on_bad_path(scanner, monkeypatch, capsys):
    monkeypatch.setattr(scanner.os.path, "expanduser",
                        lambda p: "/does/not/exist/nope/signal_intelligence.db")
    scanner.log_signal_intelligence("2026-07-22", "THIRTEENF", "ABC", "BUY", 1)
    out = capsys.readouterr().out
    assert "[SIGNAL_LOG_FAIL]" in out and "THIRTEENF" in out and "ABC" in out


def test_log_scan_routes_shared_heartbeat(scanner, monkeypatch):
    calls = []
    monkeypatch.setattr(scanner, "log_scan_run", lambda *a, **k: calls.append(a))

    class FakeConn:
        def cursor(self):
            return self
        def execute(self, *a):
            pass
        def commit(self):
            pass

    scanner._log_scan(FakeConn(), "2026-07-22", "2026-06-30", True, 5, 2, False, "")
    assert calls[-1][:2] == ("THIRTEENF", "OK")
    scanner._log_scan(FakeConn(), "2026-07-22", "", False, 0, 0, False, "")
    assert calls[-1][:2] == ("THIRTEENF", "OUT_OF_WINDOW")
    scanner._log_scan(FakeConn(), "2026-07-22", "2026-06-30", True, 5, 0, False, "boom")
    assert calls[-1][:2] == ("THIRTEENF", "ERROR")
