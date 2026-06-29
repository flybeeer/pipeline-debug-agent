"""
dev/iceberg/smoke_test.py — ยืนยันว่า IcebergAdapter อ่าน Iceberg จริงได้
========================================================================
ใช้กับ local Iceberg (docker-compose ในโฟลเดอร์นี้):

  docker compose -f dev/iceberg/docker-compose.yml up -d
  pip install -e ".[iceberg]" "pyiceberg[s3fs]"
  python dev/iceberg/smoke_test.py

ขั้นตอน: สร้าง namespace+table จริงบน Iceberg → เขียนข้อมูล (มี null user_id) →
อ่านสถิติด้วย IcebergAdapter ของเราจริง → เทียบค่าที่คาด
"""

import os
import sys
from pathlib import Path

# ชี้ PyIceberg มาที่ .pyiceberg.yaml ในโฟลเดอร์นี้ + ให้ import แพ็กเกจของโปรเจกต์ได้
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
os.environ.setdefault("PYICEBERG_HOME", str(HERE))
os.environ.setdefault("PIPELINE_TARGET_ENV", "staging")
sys.path.insert(0, str(ROOT))

import pyarrow as pa  # noqa: E402
from pyiceberg.catalog import load_catalog  # noqa: E402

from integrations.warehouse import get_adapter  # noqa: E402

IDENTIFIER = "staging.daily_sales"
DATA = pa.table({
    "user_id": pa.array([1, 2, None, 3], type=pa.int64()),
    "amount": pa.array([10.0, 20.0, 99.0, 30.0], type=pa.float64()),
})


def setup_table() -> None:
    """สร้าง namespace + table จริงบน Iceberg แล้วเขียนข้อมูลทดสอบ"""
    catalog = load_catalog("local")
    catalog.create_namespace_if_not_exists("staging")
    if catalog.table_exists(IDENTIFIER):
        catalog.drop_table(IDENTIFIER)          # idempotent: รันซ้ำได้
    table = catalog.create_table(IDENTIFIER, schema=DATA.schema)
    table.append(DATA)
    print(f"✅ เขียน {DATA.num_rows} แถวลง Iceberg table '{IDENTIFIER}'")


def main() -> None:
    setup_table()

    # ── อ่านสถิติด้วย adapter ของเราจริง (load_catalog("local") จาก .pyiceberg.yaml) ──
    stats = get_adapter("iceberg").fetch_stats({
        "iceberg_table": IDENTIFIER,
        "catalog": "local",
        "key_columns": ["user_id"],
        "sum_columns": ["amount"],
        "baseline_row_count": 4,
    })
    print(f"📊 stats: row_count={stats.row_count}, "
          f"null(user_id)={stats.null_counts['user_id']}, "
          f"sum(amount)={stats.column_sums['amount']}")

    assert stats.row_count == 4, stats
    assert stats.null_counts["user_id"] == 1, stats
    assert stats.column_sums["amount"] == 159.0, stats
    print("\n✅ SMOKE TEST ผ่าน — IcebergAdapter อ่าน Iceberg จริงได้ถูกต้อง")


if __name__ == "__main__":
    main()
