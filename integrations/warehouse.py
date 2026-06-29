"""
integrations/warehouse.py — Warehouse adapter layer (Iceberg)
=============================================================
แยกการอ่านสถิติ output ออกจาก engine → validation (Check) ไม่ผูกกับ engine ใด

warehouse จริง = **Apache Iceberg** ผ่าน PyIceberg + PyArrow (ไม่มี DuckDB ใน read path)
  catalog.load_table(identifier).scan().to_arrow() → คำนวณสถิติด้วย pyarrow.compute

🚨 กฎเหล็ก #3: adapter อ่าน staging/test เท่านั้น — บล็อก production
side-effect (ต่อ warehouse) อยู่ที่นี่ตามคอนเวนชัน — node/validation แค่เรียกใช้

> หมายเหตุ: DuckDB ไม่ใช่ warehouse option แล้ว — เหลือเป็นแค่ engine รัน fix SQL
>           แบบ offline ของ runner + demo/test (ดู integrations/runner.py)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# บังคับให้รู้ตัวว่ากำลังอ่านจากที่ไหน — ห้ามไป prod
TARGET_ENV = os.environ.get("PIPELINE_TARGET_ENV", "staging")
# engine ของ Check — ตอนนี้รองรับ iceberg (default) เท่านั้น
WAREHOUSE = os.environ.get("WAREHOUSE", "iceberg")

# regex กัน SQL injection: ชื่อ table/column จาก config ต้องเป็น identifier ปกติ
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _safe_ident(name: str) -> str:
    """ตรวจว่าเป็น identifier ปลอดภัยแล้ว quote ด้วย double-quote ต่อ segment

    Iceberg read path เข้าถึงคอลัมน์ผ่าน Arrow ไม่ประกอบ SQL ดิบ แต่ยังเก็บ helper นี้
    ไว้ validate ชื่อ identifier จาก config (กันพิมพ์ผิด/ค่าแปลก)
    """
    if not _IDENT.match(name):
        raise ValueError(f"identifier ไม่ปลอดภัย/ผิดรูป: {name!r}")
    return ".".join(f'"{seg}"' for seg in name.split("."))


@dataclass
class OutputStats:
    """สถิติ aggregate ของ output ที่ fix สร้างบน staging — วัตถุดิบของ semantic check"""
    row_count: int = 0
    null_counts: dict[str, int] = field(default_factory=dict)    # คอลัมน์ -> จำนวน null
    column_sums: dict[str, float] = field(default_factory=dict)  # คอลัมน์ -> ผลรวม
    baseline_row_count: int | None = None


def _guard_not_production() -> None:
    if TARGET_ENV == "production":
        raise RuntimeError(
            "❌ ปฏิเสธ: warehouse adapter ต้องไม่อ่าน production "
            "(ตั้ง PIPELINE_TARGET_ENV=staging)"
        )


# indirection เพื่อให้ test/demo monkeypatch ได้ (และ lazy import PyIceberg)
def _load_iceberg_catalog(name: str):
    from pyiceberg.catalog import load_catalog
    return load_catalog(name)   # อ่าน props จาก ~/.pyiceberg.yaml หรือ env PYICEBERG_*


class IcebergAdapter:
    """อ่านสถิติจาก Apache Iceberg ผ่าน PyIceberg + PyArrow

    config: iceberg_table ("namespace.table"), catalog (optional ชื่อ catalog)
            key_columns, sum_columns, baseline_row_count (optional)
    credential/props ตั้งผ่าน ~/.pyiceberg.yaml หรือ env (มาตรฐาน PyIceberg)
    """
    name = "iceberg"

    def fetch_stats(self, config: dict) -> OutputStats:
        _guard_not_production()
        import pyarrow.compute as pc  # lazy

        key_columns = list(config.get("key_columns", []))
        sum_columns = list(config.get("sum_columns", []))

        catalog_name = config.get("catalog") or os.environ.get("ICEBERG_CATALOG", "default")
        table = _load_iceberg_catalog(catalog_name).load_table(config["iceberg_table"])

        # อ่านเป็น Arrow แล้วคำนวณ aggregate ด้วย pyarrow (ไม่ดึงข้อมูลออกมาประมวลผลเอง)
        arrow = table.scan().to_arrow()

        return OutputStats(
            row_count=arrow.num_rows,
            null_counts={c: int(arrow.column(c).null_count) for c in key_columns},
            # sum บนตารางว่าง/ทั้ง null → None → map เป็น 0.0
            column_sums={
                c: float(pc.sum(arrow.column(c)).as_py() or 0.0) for c in sum_columns
            },
            baseline_row_count=config.get("baseline_row_count"),
        )


_ADAPTERS = {"iceberg": IcebergAdapter}


def get_adapter(name: str | None = None):
    """เลือก adapter จากชื่อ (หรือ env WAREHOUSE) — รองรับ iceberg เท่านั้น"""
    key = (name or WAREHOUSE or "iceberg").lower()
    if key not in _ADAPTERS:
        raise ValueError(
            f"ไม่รู้จัก/ไม่รองรับ warehouse: {key!r} (รองรับ: {', '.join(_ADAPTERS)})"
        )
    return _ADAPTERS[key]()
