"""
integrations/runner.py — Pipeline execution layer (รัน fix SQL)
==============================================================
รัน SQL ของ fix กับ staging เพื่อทดสอบ (Check ขั้นที่ 1: รันผ่านไหม)

🚨 กฎเหล็ก #3: staging/test เท่านั้น — ห้ามรันบน production

เลือก engine จาก env RUNNER:
  • duckdb (default) — รัน SQL ลงไฟล์ DuckDB (offline harness ของ demo/test)
  • trino           — รัน SQL กับ Trino (Iceberg connector) → เขียนลง Iceberg จริง

interface คงที่: execute_pipeline(code) — node test เรียกตัวนี้ ไม่รู้ engine ข้างใน
"""

import os

# บังคับให้รู้ตัวว่ากำลังรันที่ไหน — กันพลาดไปแตะ prod
TARGET_ENV = os.environ.get("PIPELINE_TARGET_ENV", "staging")
RUNNER = os.environ.get("RUNNER", "duckdb")

# ── DuckDB (offline harness) ──
PIPELINE_DUCKDB = os.environ.get("PIPELINE_DUCKDB", "")

# ── Trino (Iceberg) ──
TRINO_HOST = os.environ.get("TRINO_HOST", "localhost")
TRINO_PORT = int(os.environ.get("TRINO_PORT", "8080"))
TRINO_USER = os.environ.get("TRINO_USER", "agent")
TRINO_CATALOG = os.environ.get("TRINO_CATALOG", "iceberg")
TRINO_SCHEMA = os.environ.get("TRINO_SCHEMA", "staging")


class DuckDBRunner:
    """รัน SQL ลงไฟล์ DuckDB (engine harness แบบ offline ของ demo/test)"""
    name = "duckdb"

    def execute(self, code: str) -> None:
        if not PIPELINE_DUCKDB:
            raise NotImplementedError(
                "ตั้ง env PIPELINE_DUCKDB ให้ชี้ไฟล์ DuckDB ของ staging ก่อน "
                "(หรือใช้ RUNNER=trino สำหรับ Iceberg จริง)"
            )
        import duckdb  # lazy

        con = duckdb.connect(database=PIPELINE_DUCKDB, read_only=False)
        try:
            con.execute(code)   # error ใดๆ จะ propagate → run_test จับไปวิเคราะห์ต่อ
        finally:
            con.close()


# indirection เพื่อให้ test monkeypatch ได้ (และ lazy import trino)
def _trino_connect():
    import trino

    return trino.dbapi.connect(
        host=TRINO_HOST, port=TRINO_PORT, user=TRINO_USER,
        catalog=TRINO_CATALOG, schema=TRINO_SCHEMA,
    )


class TrinoRunner:
    """รัน SQL ของ fix ผ่าน Trino (Iceberg connector) → เขียนลง Iceberg จริง

    fix มักเป็น CREATE OR REPLACE TABLE ... AS SELECT (idempotent) อ้างชื่อ table
    แบบ bare (resolve ด้วย catalog/schema ของ connection = iceberg.staging)
    """
    name = "trino"

    def execute(self, code: str) -> None:
        conn = _trino_connect()
        try:
            cur = conn.cursor()
            # Trino รันทีละ statement — แยกด้วย ';' (fix มักเป็น statement เดียว)
            for stmt in (s.strip() for s in code.split(";")):
                if not stmt:
                    continue
                cur.execute(stmt)
                cur.fetchall()   # Trino lazy — ต้อง fetch เพื่อให้รันจริงให้จบ
        finally:
            conn.close()


_RUNNERS = {"duckdb": DuckDBRunner, "trino": TrinoRunner}


def get_runner(name: str | None = None):
    key = (name or RUNNER or "duckdb").lower()
    if key not in _RUNNERS:
        raise ValueError(
            f"ไม่รู้จัก runner: {key!r} (รองรับ: {', '.join(_RUNNERS)})"
        )
    return _RUNNERS[key]()


def execute_pipeline(code: str) -> None:
    """รัน fix SQL กับ staging ผ่าน runner ที่เลือก (ถ้า fail จะ raise ออกไป)"""
    if TARGET_ENV == "production":
        raise RuntimeError(
            "❌ ปฏิเสธการรัน: execute_pipeline ต้องไม่ชี้ไป production "
            "(ตั้ง PIPELINE_TARGET_ENV=staging)"
        )
    get_runner().execute(code)
