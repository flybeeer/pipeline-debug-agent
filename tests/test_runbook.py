"""
ทดสอบ runbook loader + การ auto-load เข้า state.runbook
"""

from integrations.runbook import load_runbook
from state import new_state

_BASE = dict(
    error_log="err",
    pipeline_code="SELECT 1",
    file_path="models/x.sql",
)


# ── load_runbook ──

def test_load_runbook_found(tmp_path):
    (tmp_path / "iss-1.md").write_text("# Runbook\nกรอง null ก่อน aggregate")
    assert "กรอง null" in load_runbook("iss-1", str(tmp_path))


def test_load_runbook_missing_returns_empty(tmp_path):
    assert load_runbook("no-such", str(tmp_path)) == ""


# ── auto-load ผ่าน new_state ──

def test_new_state_autoloads_runbook(tmp_path, monkeypatch):
    (tmp_path / "iss-2.md").write_text("runbook content")
    monkeypatch.setenv("RUNBOOKS_DIR", str(tmp_path))
    # reload ค่า RUNBOOKS_DIR (อ่านตอน import) ผ่าน monkeypatch ที่ตัว module
    import integrations.runbook as rb
    monkeypatch.setattr(rb, "RUNBOOKS_DIR", str(tmp_path))

    state = new_state(issue_id="iss-2", **_BASE)
    assert state["runbook"] == "runbook content"
    assert state["has_runbook"] is True


def test_new_state_no_runbook_file(tmp_path, monkeypatch):
    import integrations.runbook as rb
    monkeypatch.setattr(rb, "RUNBOOKS_DIR", str(tmp_path))
    state = new_state(issue_id="ghost", **_BASE)
    assert state["runbook"] == ""
    assert state["has_runbook"] is False


def test_new_state_explicit_empty_overrides_autoload(tmp_path, monkeypatch):
    # มีไฟล์อยู่ แต่ส่ง runbook="" → บังคับไม่ใช้ (override)
    (tmp_path / "iss-3.md").write_text("should be ignored")
    import integrations.runbook as rb
    monkeypatch.setattr(rb, "RUNBOOKS_DIR", str(tmp_path))

    state = new_state(issue_id="iss-3", runbook="", **_BASE)
    assert state["runbook"] == ""
    assert state["has_runbook"] is False
