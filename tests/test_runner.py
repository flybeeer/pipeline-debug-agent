"""
ทดสอบ runner layer — DuckDB (จริง, offline harness) + Trino (mock client)
"""

import duckdb
import pytest

import integrations.runner as runner
from integrations.runner import get_runner

# ── get_runner ──

def test_get_runner_default_duckdb():
    assert get_runner().name == "duckdb"
    assert get_runner("trino").name == "trino"


def test_get_runner_unknown_raises():
    with pytest.raises(ValueError, match="ไม่รู้จัก runner"):
        get_runner("spark")


# ── prod guard (กฎเหล็ก #3) ──

def test_execute_pipeline_refuses_production(monkeypatch):
    monkeypatch.setattr(runner, "TARGET_ENV", "production")
    with pytest.raises(RuntimeError, match="production"):
        runner.execute_pipeline("SELECT 1")


# ── DuckDBRunner (จริง) ──

def test_duckdb_runner_executes(tmp_path, monkeypatch):
    db = tmp_path / "s.duckdb"
    monkeypatch.setattr(runner, "PIPELINE_DUCKDB", str(db))
    get_runner("duckdb").execute("CREATE TABLE t AS SELECT 1 AS x")

    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT x FROM t").fetchall()
    con.close()
    assert rows == [(1,)]


def test_duckdb_runner_requires_path(monkeypatch):
    monkeypatch.setattr(runner, "PIPELINE_DUCKDB", "")
    with pytest.raises(NotImplementedError, match="PIPELINE_DUCKDB"):
        get_runner("duckdb").execute("SELECT 1")


# ── TrinoRunner (mock client) ──

class _FakeCursor:
    def __init__(self, calls):
        self.calls = calls

    def execute(self, sql):
        self.calls.append(("execute", sql))

    def fetchall(self):
        self.calls.append(("fetchall",))
        return []


class _FakeConn:
    def __init__(self, calls):
        self.calls = calls

    def cursor(self):
        return _FakeCursor(self.calls)

    def close(self):
        self.calls.append(("close",))


def test_trino_runner_runs_each_statement(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "_trino_connect", lambda: _FakeConn(calls))

    get_runner("trino").execute("CREATE TABLE a AS SELECT 1;  SELECT * FROM a ; ")

    executed = [c[1] for c in calls if c[0] == "execute"]
    assert executed == ["CREATE TABLE a AS SELECT 1", "SELECT * FROM a"]  # แยก ; + strip
    assert ("fetchall",) in calls    # ต้อง fetch เพื่อให้ Trino รันจริง
    assert ("close",) in calls       # ปิด connection เสมอ


def test_execute_pipeline_routes_to_runner(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "TARGET_ENV", "staging")
    monkeypatch.setattr(runner, "RUNNER", "trino")
    monkeypatch.setattr(runner, "_trino_connect", lambda: _FakeConn(calls))

    runner.execute_pipeline("CREATE OR REPLACE TABLE daily_sales AS SELECT 1")
    assert any(c[0] == "execute" for c in calls)
