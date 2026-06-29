"""
tests/test_airflow_trigger.py
=============================
ทดสอบสะพาน Airflow → agent โดย "ไม่ต้องติดตั้ง airflow":
  • build_agent_conf เป็น pure dict-in/dict-out — เทสตรงๆ ได้
  • trigger_debug_agent / on_pipeline_failure — monkeypatch ตัว trigger จริง
    เพื่อเช็คว่าเรียกถูก conf และ "ไม่ raise" เมื่อพัง
"""

from __future__ import annotations

from types import SimpleNamespace

import integrations.airflow_trigger as at


def _fake_context(**overrides):
    """จำลอง context dict ที่ Airflow ส่งให้ on_failure_callback"""
    ctx = {
        "dag": SimpleNamespace(dag_id="example_sales_pipeline"),
        "task": SimpleNamespace(task_id="build_daily_sales"),
        "task_instance": SimpleNamespace(
            run_id="manual__2024-01-01", log_url="http://airflow/log/123"
        ),
        "run_id": "manual__2024-01-01",
        "exception": ValueError("null user_id ใน daily_sales"),
        "params": {
            "fix_file_path": "models/sales/daily_sales.sql",
            "schema_info": "raw_sales(user_id, amount)",
        },
    }
    ctx.update(overrides)
    return ctx


def test_build_agent_conf_extracts_core_fields():
    conf = at.build_agent_conf(_fake_context())

    assert conf["issue_id"] == "example_sales_pipeline.build_daily_sales"
    assert "ValueError" in conf["error_log"]
    assert "null user_id" in conf["error_log"]
    assert "http://airflow/log/123" in conf["error_log"]
    assert conf["file_path"] == "models/sales/daily_sales.sql"
    assert conf["schema_info"] == "raw_sales(user_id, amount)"
    assert conf["source"] == "airflow"
    assert conf["airflow"]["dag_id"] == "example_sales_pipeline"
    assert conf["airflow"]["task_id"] == "build_daily_sales"


def test_build_agent_conf_honors_issue_id_override():
    # pipeline ประกาศ known issue ที่มี runbook ผ่าน params → ต้องชนะ "<dag>.<task>"
    ctx = _fake_context(params={"issue_id": "null-userid-042"})
    conf = at.build_agent_conf(ctx)
    assert conf["issue_id"] == "null-userid-042"


def test_build_agent_conf_reads_file_when_code_absent(tmp_path):
    f = tmp_path / "model.sql"
    f.write_text("SELECT 1")
    ctx = _fake_context(params={"fix_file_path": str(f)})

    conf = at.build_agent_conf(ctx)
    assert conf["pipeline_code"] == "SELECT 1"


def test_build_agent_conf_survives_missing_pieces():
    # context แทบว่าง — ต้องไม่พัง และ fallback เป็นค่า default ที่อ่านออก
    conf = at.build_agent_conf({})
    assert conf["issue_id"] == "unknown_dag.unknown_task"
    assert conf["error_log"]            # มีข้อความ fallback
    assert conf["file_path"] == ""


def test_trigger_debug_agent_calls_airflow(monkeypatch):
    calls = {}

    def fake_trigger_dag(*, dag_id, run_id, conf, replace_microseconds):
        calls.update(dag_id=dag_id, run_id=run_id, conf=conf)

    # inject module airflow ปลอมให้ lazy-import เจอ
    import sys

    fake_mod = SimpleNamespace(trigger_dag=fake_trigger_dag)
    monkeypatch.setitem(sys.modules, "airflow.api.common.trigger_dag", fake_mod)

    run_id = at.trigger_debug_agent({"issue_id": "dag.task"}, agent_dag_id="debug_agent")

    assert calls["dag_id"] == "debug_agent"
    assert calls["conf"]["issue_id"] == "dag.task"
    assert run_id.startswith("agent__dag.task__")
    assert calls["run_id"] == run_id


def test_on_pipeline_failure_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("airflow ล่ม")

    monkeypatch.setattr(at, "trigger_debug_agent", boom)
    # ต้องกลืน error เงียบ ไม่ throw (ไม่งั้นจะกลบ error เดิมของ task)
    at.on_pipeline_failure(_fake_context())


def test_on_pipeline_failure_respects_kill_switch(monkeypatch):
    called = {"n": 0}

    def spy(*_a, **_k):
        called["n"] += 1

    monkeypatch.setattr(at, "trigger_debug_agent", spy)
    monkeypatch.setenv("DEBUG_AGENT_TRIGGER_ENABLED", "false")

    at.on_pipeline_failure(_fake_context())
    assert called["n"] == 0      # ปิดสวิตช์แล้วต้องไม่ trigger


def test_on_pipeline_failure_triggers_when_enabled(monkeypatch):
    captured = {}

    def spy(conf, **_k):
        captured["conf"] = conf
        return "agent__x"

    monkeypatch.setattr(at, "trigger_debug_agent", spy)
    monkeypatch.delenv("DEBUG_AGENT_TRIGGER_ENABLED", raising=False)

    at.on_pipeline_failure(_fake_context())
    assert captured["conf"]["issue_id"] == "example_sales_pipeline.build_daily_sales"
