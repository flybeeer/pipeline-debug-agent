"""
ทดสอบ logic การจัด tier — ส่วนที่ test ได้โดยไม่ต้องเรียก LLM จริง
รัน: pytest tests/ -v
"""

from nodes.tiering import _is_data_fix, _looks_idempotent


def test_insert_overwrite_is_idempotent():
    assert _looks_idempotent("INSERT OVERWRITE TABLE sales SELECT * FROM staging")


def test_merge_is_idempotent():
    assert _looks_idempotent("MERGE INTO sales USING updates ON ...")


def test_plain_insert_not_idempotent():
    assert not _looks_idempotent("INSERT INTO sales VALUES (1, 2, 3)")


def test_update_is_data_fix():
    assert _is_data_fix("UPDATE sales SET amount = 0 WHERE id = 5")


def test_select_not_data_fix():
    assert not _is_data_fix("SELECT user_id, SUM(amount) FROM sales GROUP BY 1")


def test_truncate_is_data_fix():
    assert _is_data_fix("TRUNCATE TABLE sales")
