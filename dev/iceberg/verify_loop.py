"""
dev/iceberg/verify_loop.py — full inner-loop บน Iceberg จริง (Trino execute + PyIceberg read)
============================================================================================
ต้องยก stack ก่อน:
  docker compose -f dev/iceberg/docker-compose.yml up -d
  pip install -e ".[iceberg,trino]" "pyiceberg[s3fs]"
  python dev/iceberg/verify_loop.py

ขั้นตอน (จำลอง inner loop ขั้น Action→Check บน warehouse จริง):
  1. TrinoRunner สร้าง schema + source raw_sales (มี null user_id)
  2. TrinoRunner รัน "fix" — CREATE OR REPLACE daily_sales กรอง null (idempotent)
  3. IcebergAdapter อ่านสถิติ daily_sales → ยืนยัน semantic ถูก (ไม่มี null, sum ถูก)
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
os.environ.setdefault("PYICEBERG_HOME", str(HERE))       # ให้ IcebergAdapter อ่าน catalog local
os.environ.setdefault("PIPELINE_TARGET_ENV", "staging")
os.environ.setdefault("RUNNER", "trino")                  # ให้ execute_pipeline ใช้ Trino
os.environ.setdefault("TRINO_CATALOG", "iceberg")
os.environ.setdefault("TRINO_SCHEMA", "staging")
sys.path.insert(0, str(ROOT))

from integrations.runner import execute_pipeline  # noqa: E402
from integrations.warehouse import get_adapter  # noqa: E402

SETUP = [
    "CREATE SCHEMA IF NOT EXISTS staging",
    # source ที่มี null user_id + แถวซ้ำ (เหมือน scenario ใน demo)
    "CREATE OR REPLACE TABLE staging.raw_sales AS "
    "SELECT * FROM (VALUES (1,10.0),(1,5.0),(2,20.0),(3,30.0),(CAST(NULL AS INTEGER),99.0)) "
    "AS t(user_id, amount)",
]

# "fix" ที่ agent เสนอ — idempotent + กรอง null
FIX = (
    "CREATE OR REPLACE TABLE staging.daily_sales AS "
    "SELECT user_id, SUM(amount) AS amount FROM staging.raw_sales "
    "WHERE user_id IS NOT NULL GROUP BY user_id"
)


def main() -> None:
    for stmt in SETUP:
        execute_pipeline(stmt)
    print("✅ Trino: setup raw_sales (มี null user_id)")

    execute_pipeline(FIX)   # ← รัน fix ผ่าน runner (Trino) เขียนลง Iceberg จริง
    print("✅ Trino: รัน fix → daily_sales (idempotent, กรอง null)")

    # ── Check: อ่านด้วย IcebergAdapter ของเรา ──
    stats = get_adapter("iceberg").fetch_stats({
        "iceberg_table": "staging.daily_sales",
        "catalog": "local",
        "key_columns": ["user_id"],
        "sum_columns": ["amount"],
        "baseline_row_count": 3,
    })
    print(f"📊 stats: row_count={stats.row_count}, "
          f"null(user_id)={stats.null_counts['user_id']}, "
          f"sum(amount)={stats.column_sums['amount']}")

    assert stats.row_count == 3, stats                 # user 1/2/3
    assert stats.null_counts["user_id"] == 0, stats    # null ถูกกรองแล้ว
    assert stats.column_sums["amount"] == 65.0, stats  # 15+20+30 (99 ของ null หลุดออก)
    print("\n✅ VERIFY ผ่าน — full loop บน Iceberg จริง (Trino execute + PyIceberg read)")


if __name__ == "__main__":
    main()
