"""
ทดสอบ semantic validation layer — ส่วน Check ที่สำคัญที่สุด (กฎเหล็ก #4)
แบ่งเป็น: (1) check รายข้อ (pure) (2) validate รวม (mock) (3) Iceberg end-to-end (mock catalog)
รัน: pytest tests/ -v
"""

import pyarrow as pa
import pytest

import integrations.validation as v
import integrations.warehouse as wh
from integrations.validation import (
    OutputStats,
    no_nulls_in_keys,
    non_empty_output,
    row_count_within,
    validate_output_semantics,
)

# ── (1) check รายข้อ (logic ล้วน ไม่แตะ data) ──

def test_non_empty_output_fails_on_zero_rows():
    assert not non_empty_output().predicate(OutputStats(row_count=0))
    assert non_empty_output().predicate(OutputStats(row_count=5))


def test_no_nulls_in_keys():
    check = no_nulls_in_keys("user_id")
    assert check.predicate(OutputStats(row_count=3, null_counts={"user_id": 0}))
    assert not check.predicate(OutputStats(row_count=3, null_counts={"user_id": 2}))


def test_row_count_within_tolerance():
    check = row_count_within(0.10)
    assert check.predicate(OutputStats(row_count=100))                       # ไม่มี baseline → ผ่าน
    assert check.predicate(OutputStats(row_count=105, baseline_row_count=100))   # ในกรอบ
    assert not check.predicate(OutputStats(row_count=50, baseline_row_count=100))  # หลุดกรอบ


# ── (2) validate_output_semantics รวม (mock fetch_output_stats) ──

_CFG = {"target_table": "t", "database": ":memory:", "key_columns": ["user_id"]}


def test_validate_unverified_when_no_config(tmp_path):
    """ไม่มี config ของ issue → ต้อง fail-closed UNVERIFIED"""
    ok, detail = validate_output_semantics(
        {"issue_id": "no-such-issue"}, checks_dir=str(tmp_path)
    )
    assert ok is False
    assert "UNVERIFIED" in detail


def test_validate_unverified_when_stub(monkeypatch):
    """ถ้า fetch_output_stats ยังเป็น stub → UNVERIFIED ไม่เคลม success"""
    def not_impl(_state, _config):
        raise NotImplementedError
    monkeypatch.setattr(v, "fetch_output_stats", not_impl)

    ok, detail = validate_output_semantics({}, config=_CFG)
    assert ok is False
    assert "UNVERIFIED" in detail


def test_validate_error_is_fail_closed(monkeypatch):
    """query/connect พัง → ถือว่าไม่ผ่าน (ไม่ปล่อยผ่านเงียบ)"""
    def boom(_state, _config):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(v, "fetch_output_stats", boom)

    ok, detail = validate_output_semantics({}, config=_CFG)
    assert ok is False
    assert "error" in detail


def test_validate_passes_when_stats_clean(monkeypatch):
    monkeypatch.setattr(
        v, "fetch_output_stats",
        lambda _state, _config: OutputStats(
            row_count=100, baseline_row_count=100, null_counts={"user_id": 0}
        ),
    )
    ok, detail = validate_output_semantics({}, config=_CFG)
    assert ok is True
    assert "ผ่านหมด" in detail


def test_validate_fails_on_null_keys(monkeypatch):
    monkeypatch.setattr(
        v, "fetch_output_stats",
        lambda _state, _config: OutputStats(
            row_count=100, baseline_row_count=100, null_counts={"user_id": 7}
        ),
    )
    ok, detail = validate_output_semantics({}, config=_CFG)
    assert ok is False
    assert "no_nulls_in_keys" in detail


# ── (3) Iceberg end-to-end (mock catalog → validate ผ่าน adapter จริง) ──

def _patch_iceberg(monkeypatch, **columns):
    """ให้ catalog คืน table ที่ scan().to_arrow() = pyarrow.Table จาก columns"""
    arrow = pa.table(columns)

    class _Scan:
        def to_arrow(self):
            return arrow

    class _Table:
        def scan(self):
            return _Scan()

    class _Catalog:
        def load_table(self, identifier):
            return _Table()

    monkeypatch.setattr(wh, "_load_iceberg_catalog", lambda name: _Catalog())


def _write_config(tmp_path, **overrides):
    import yaml
    cfg = {
        "warehouse": "iceberg",
        "iceberg_table": "staging.daily_sales",
        "key_columns": ["user_id"],
        "sum_columns": ["amount"],
        "baseline_row_count": 3,
        "row_count_tolerance": 0.10,
        **overrides,
    }
    checks_dir = tmp_path / "checks"
    checks_dir.mkdir(exist_ok=True)
    (checks_dir / "demo.yaml").write_text(yaml.safe_dump(cfg))
    return checks_dir


def test_end_to_end_clean_data_passes(tmp_path, monkeypatch):
    _patch_iceberg(monkeypatch, user_id=[1, 2, 3], amount=[10.0, 20.0, 30.0])
    checks_dir = _write_config(tmp_path)

    ok, detail = validate_output_semantics(
        {"issue_id": "demo"}, checks_dir=str(checks_dir)
    )
    assert ok is True, detail


def test_end_to_end_null_key_fails(tmp_path, monkeypatch):
    _patch_iceberg(monkeypatch, user_id=[1, None, 3], amount=[10.0, 20.0, 30.0])
    checks_dir = _write_config(tmp_path)

    ok, detail = validate_output_semantics(
        {"issue_id": "demo"}, checks_dir=str(checks_dir)
    )
    assert ok is False
    assert "no_nulls_in_keys" in detail


def test_end_to_end_row_drift_fails(tmp_path, monkeypatch):
    # baseline=3 แต่มีจริงแค่ 1 แถว → drift หลุด 10% → fail
    _patch_iceberg(monkeypatch, user_id=[1], amount=[10.0])
    checks_dir = _write_config(tmp_path)

    ok, detail = validate_output_semantics(
        {"issue_id": "demo"}, checks_dir=str(checks_dir)
    )
    assert ok is False
    assert "row_count_within" in detail


def test_fetch_refuses_production(monkeypatch):
    """guard: ตั้ง env=production แล้วต้องปฏิเสธการอ่าน (ที่ชั้น fetch_output_stats)"""
    monkeypatch.setattr(v, "TARGET_ENV", "production")
    with pytest.raises(RuntimeError, match="production"):
        v.fetch_output_stats({}, {"warehouse": "iceberg", "iceberg_table": "s.t"})


def test_unsafe_identifier_rejected():
    with pytest.raises(ValueError):
        v._safe_ident("daily_sales; DROP TABLE users")
