"""
ทดสอบ semantic validation layer — ส่วน Check ที่สำคัญที่สุด (กฎเหล็ก #4)
แบ่งเป็น: (1) check รายข้อ (pure) (2) validate รวม (mock) (3) DuckDB end-to-end จริง
รัน: pytest tests/ -v
"""

import duckdb
import pytest

import integrations.validation as v
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


# ── (3) DuckDB end-to-end จริง (สร้าง staging ชั่วคราว + config จริง) ──

def _make_staging(tmp_path, rows):
    """สร้างไฟล์ DuckDB staging ชั่วคราวพร้อมตาราง daily_sales"""
    db = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE daily_sales (user_id INTEGER, amount DOUBLE)")
    con.executemany("INSERT INTO daily_sales VALUES (?, ?)", rows)
    con.close()
    return db


def _write_config(tmp_path, db, **overrides):
    import yaml
    cfg = {
        "database": str(db),
        "target_table": "daily_sales",
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


def test_end_to_end_clean_data_passes(tmp_path):
    db = _make_staging(tmp_path, [(1, 10.0), (2, 20.0), (3, 30.0)])
    checks_dir = _write_config(tmp_path, db)

    ok, detail = validate_output_semantics(
        {"issue_id": "demo"}, checks_dir=str(checks_dir)
    )
    assert ok is True, detail


def test_end_to_end_null_key_fails(tmp_path):
    db = _make_staging(tmp_path, [(1, 10.0), (None, 20.0), (3, 30.0)])
    checks_dir = _write_config(tmp_path, db)

    ok, detail = validate_output_semantics(
        {"issue_id": "demo"}, checks_dir=str(checks_dir)
    )
    assert ok is False
    assert "no_nulls_in_keys" in detail


def test_end_to_end_row_drift_fails(tmp_path):
    # baseline=3 แต่มีจริงแค่ 1 แถว → drift หลุด 10% → fail
    db = _make_staging(tmp_path, [(1, 10.0)])
    checks_dir = _write_config(tmp_path, db)

    ok, detail = validate_output_semantics(
        {"issue_id": "demo"}, checks_dir=str(checks_dir)
    )
    assert ok is False
    assert "row_count_within" in detail


def test_fetch_refuses_production(tmp_path, monkeypatch):
    """guard: ตั้ง env=production แล้วต้องปฏิเสธการอ่าน"""
    db = _make_staging(tmp_path, [(1, 10.0)])
    monkeypatch.setattr(v, "TARGET_ENV", "production")
    with pytest.raises(RuntimeError, match="production"):
        v.fetch_output_stats({}, {"database": str(db), "target_table": "daily_sales"})


def test_unsafe_identifier_rejected():
    with pytest.raises(ValueError):
        v._safe_ident("daily_sales; DROP TABLE users")
