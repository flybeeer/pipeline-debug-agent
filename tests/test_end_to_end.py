"""
ทดสอบ loop เต็มวง (Action→Check→Repeat→Review) แบบ offline เป็น regression guard
ใช้ fake LLM + DuckDB เป็น harness รัน fix SQL + Iceberg adapter อ่าน output
(catalog เป็น fake ที่อ่าน duckdb output → arrow แทน Iceberg จริง) — offline ล้วน
"""

from types import SimpleNamespace

import duckdb
import yaml

import integrations.runner as runner
import integrations.validation as validation
import integrations.warehouse as warehouse
import nodes.tiering as tiering
from integrations.llm import set_llm

FIXED_SQL = (
    "CREATE OR REPLACE TABLE daily_sales AS "
    "SELECT user_id, SUM(amount) AS amount FROM raw_sales "
    "WHERE user_id IS NOT NULL GROUP BY user_id"
)


def _duckdb_backed_catalog(db_path):
    """fake Iceberg catalog — อ่าน output ที่ runner เขียนลง duckdb แล้วคืนเป็น Arrow
    (แทน catalog จริง: prod คือ REST/Glue ส่วน offline อ่านจาก duckdb harness)"""
    class _Scan:
        def __init__(self, ident):
            self._ident = ident

        def to_arrow(self):
            con = duckdb.connect(db_path, read_only=True)
            try:
                return con.execute(f'SELECT * FROM "{self._ident}"').to_arrow_table()
            finally:
                con.close()

    class _Table:
        def __init__(self, ident):
            self._ident = ident

        def scan(self):
            return _Scan(self._ident)

    class _Catalog:
        def load_table(self, identifier):
            return _Table(identifier)

    return _Catalog()

class _FakeLLM:
    def invoke(self, prompt: str):
        if "แก้โค้ดนี้" in prompt:
            return SimpleNamespace(content=FIXED_SQL)
        return SimpleNamespace(content="null user_id — กรองออก + ทำ idempotent")


def test_full_loop_reaches_auto_merge(tmp_path, monkeypatch):
    # ── staging DuckDB + source ที่มี null user_id ──
    db = tmp_path / "staging.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE raw_sales (user_id INTEGER, amount DOUBLE)")
    con.executemany(
        "INSERT INTO raw_sales VALUES (?, ?)",
        [(1, 10.0), (1, 5.0), (2, 20.0), (3, 30.0), (None, 99.0)],
    )
    con.close()

    # ── check spec ต่อ issue (warehouse: iceberg) ──
    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    (checks_dir / "e2e.yaml").write_text(yaml.safe_dump({
        "warehouse": "iceberg",
        "iceberg_table": "daily_sales",
        "key_columns": ["user_id"],
        "sum_columns": ["amount"],
        "baseline_row_count": 3,
        "row_count_tolerance": 0.10,
    }))

    # ── runner = duckdb harness (รัน fix SQL) / validation = iceberg (อ่าน output) ──
    monkeypatch.setattr(runner, "TARGET_ENV", "staging")
    monkeypatch.setattr(runner, "PIPELINE_DUCKDB", str(db))
    monkeypatch.setattr(validation, "TARGET_ENV", "staging")
    monkeypatch.setattr(validation, "CHECKS_DIR", str(checks_dir))
    monkeypatch.setattr(warehouse, "TARGET_ENV", "staging")
    # Iceberg catalog (fake) อ่าน output จาก duckdb ที่ runner เขียน → arrow
    monkeypatch.setattr(warehouse, "_load_iceberg_catalog",
                        lambda name: _duckdb_backed_catalog(str(db)))
    # lineage: daily_sales มี downstream 1 ตัว → blast เล็ก → ผ่านเงื่อนไข T1
    monkeypatch.setattr(tiering, "count_downstream", lambda _fp: 1)

    set_llm(_FakeLLM())
    try:
        from graph import build_app
        from state import new_state

        app = build_app()
        result = app.invoke(
            new_state(
                issue_id="e2e",
                error_log="null user_id",
                pipeline_code="INSERT INTO daily_sales ...",
                file_path="models/sales/daily_sales.sql",
                runbook="กรอง null ก่อน aggregate",
            ),
            config={"configurable": {"thread_id": "e2e"}},
        )
    finally:
        set_llm(None)   # คืนค่าเดิม ไม่รบกวน test อื่น

    assert result["status"] == "auto_merged"
    assert result["tier"] == "T1"
    assert result["test_result"] == "PASS"
    assert result["semantic_result"].startswith("PASS")
    assert result["attempts"] == 1

    # output ใน staging ต้องไม่มี null user_id แล้ว
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT user_id FROM daily_sales").fetchall()
    con.close()
    assert rows and all(r[0] is not None for r in rows)
