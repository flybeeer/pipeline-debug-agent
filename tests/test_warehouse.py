"""
ทดสอบ warehouse adapter layer — Iceberg (read path จริง) ด้วย mock catalog
ไม่ยิง catalog จริง: monkeypatch _load_iceberg_catalog ให้คืน fake ที่ scan().to_arrow()
คืน pyarrow.Table → exercise PyArrow compute path เดียวกับของจริง (ไม่มี duckdb)
"""

import pyarrow as pa
import pytest

import integrations.warehouse as wh
from integrations.warehouse import OutputStats, get_adapter

# ── get_adapter ──

def test_get_adapter_default_iceberg():
    assert get_adapter().name == "iceberg"
    assert get_adapter("iceberg").name == "iceberg"


def test_get_adapter_duckdb_no_longer_supported():
    # duckdb ไม่ใช่ warehouse option แล้ว (เหลือเป็น runner harness เท่านั้น)
    with pytest.raises(ValueError, match="ไม่รู้จัก"):
        get_adapter("duckdb")


def test_get_adapter_unknown_raises():
    with pytest.raises(ValueError, match="ไม่รู้จัก"):
        get_adapter("redshift")


# ── IcebergAdapter (mock catalog → pyarrow compute) ──

class _FakeScan:
    def __init__(self, table):
        self._table = table

    def to_arrow(self):
        return self._table


class _FakeTable:
    def __init__(self, table):
        self._table = table

    def scan(self):
        return _FakeScan(self._table)


class _FakeCatalog:
    def __init__(self, table):
        self._table = table
        self.loaded = None

    def load_table(self, identifier):
        self.loaded = identifier
        return _FakeTable(self._table)


def _patch_catalog(monkeypatch, arrow_table):
    cat = _FakeCatalog(arrow_table)
    monkeypatch.setattr(wh, "_load_iceberg_catalog", lambda name: cat)
    return cat


def test_iceberg_adapter_stats(monkeypatch):
    arrow = pa.table({"user_id": [1, 2, None], "amount": [10.0, 20.0, 99.0]})
    cat = _patch_catalog(monkeypatch, arrow)

    stats = get_adapter("iceberg").fetch_stats({
        "iceberg_table": "staging.daily_sales",
        "key_columns": ["user_id"], "sum_columns": ["amount"],
        "baseline_row_count": 3,
    })
    assert cat.loaded == "staging.daily_sales"     # ส่ง identifier ถูก
    assert stats.row_count == 3
    assert stats.null_counts["user_id"] == 1       # null ใน key นับถูก
    assert stats.column_sums["amount"] == 129.0
    assert stats.baseline_row_count == 3
    assert isinstance(stats, OutputStats)


def test_iceberg_empty_table_sum_is_zero(monkeypatch):
    arrow = pa.table({"user_id": pa.array([], type=pa.int64()),
                      "amount": pa.array([], type=pa.float64())})
    _patch_catalog(monkeypatch, arrow)
    stats = get_adapter("iceberg").fetch_stats({
        "iceberg_table": "s.t", "key_columns": ["user_id"], "sum_columns": ["amount"],
    })
    assert stats.row_count == 0
    assert stats.column_sums["amount"] == 0.0      # sum ของว่าง → 0.0 ไม่ใช่ None


def test_iceberg_adapter_refuses_production(monkeypatch):
    monkeypatch.setattr(wh, "TARGET_ENV", "production")
    _patch_catalog(monkeypatch, pa.table({"user_id": [1]}))
    with pytest.raises(RuntimeError, match="production"):
        get_adapter("iceberg").fetch_stats({"iceberg_table": "s.t"})
