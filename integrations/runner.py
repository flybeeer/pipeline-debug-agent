"""
integrations/runner.py — Pipeline execution layer
=================================================
รัน pipeline/query จริงเพื่อทดสอบ fix

🚨 กฎเหล็ก #3: ต้องชี้ไปที่ staging/test เท่านั้น — ห้ามรันบน production
ตั้ง connection / target ให้แยกจาก prod อย่างชัดเจน

ตอนนี้ใช้ DuckDB เป็น engine (ตรงกับ validation layer): รัน SQL ของ fix
ลงบนไฟล์ DuckDB ของ staging ที่ชี้ด้วย env PIPELINE_DUCKDB
ถ้า stack จริงเป็น dbt/BigQuery/Airflow ก็แทน execute_pipeline ตัวนี้
"""

import os

# บังคับให้รู้ตัวว่ากำลังรันที่ไหน — กันพลาดไปแตะ prod
TARGET_ENV = os.environ.get("PIPELINE_TARGET_ENV", "staging")

# ไฟล์ DuckDB ของ staging ที่ fix จะเขียน output ลงไป (validation อ่านจากไฟล์เดียวกัน)
PIPELINE_DUCKDB = os.environ.get("PIPELINE_DUCKDB", "")


def execute_pipeline(code: str) -> None:
    """
    รันโค้ด pipeline (SQL) ที่แก้แล้วกับ staging
    ถ้า fail ให้ raise exception (run_test จะจับไปวิเคราะห์ต่อ)

    DuckDB mode: รัน SQL ลงบน PIPELINE_DUCKDB (staging) — fix มักเป็น
    CREATE OR REPLACE TABLE ... AS SELECT ... ที่ materialize output table ใหม่

    ถ้า stack จริงต่างออกไป แทน body นี้ เช่น:
      • dbt:     subprocess.run(["dbt", "build", "--target", "staging", ...])
      • Airflow: trigger DAG run บน test env แล้ว poll status
      • Spark:   submit job บน test cluster
    """
    if TARGET_ENV == "production":
        raise RuntimeError(
            "❌ ปฏิเสธการรัน: execute_pipeline ต้องไม่ชี้ไป production "
            "(ตั้ง PIPELINE_TARGET_ENV=staging)"
        )

    if not PIPELINE_DUCKDB:
        # ยังไม่ได้ชี้ staging DB — บังคับให้ตั้งก่อน ดีกว่าเผลอรันที่ผิดที่
        raise NotImplementedError(
            "ตั้ง env PIPELINE_DUCKDB ให้ชี้ไฟล์ DuckDB ของ staging ก่อน "
            "(หรือแทน execute_pipeline ด้วย runner ของ stack จริง)"
        )

    import duckdb  # lazy import

    con = duckdb.connect(database=PIPELINE_DUCKDB, read_only=False)
    try:
        con.execute(code)   # รัน SQL ของ fix — error ใดๆ จะ propagate ออกไปเอง
    finally:
        con.close()
