"""
ทดสอบ observability/trace.py — LangFuse callbacks + invoke config
เน้น degrade gracefully: ไม่ตั้ง key / ไม่ได้ติดตั้ง langfuse ต้องไม่ทำ loop พัง
"""

import observability.trace as trace


def _enable(monkeypatch):
    monkeypatch.setattr(trace, "LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setattr(trace, "LANGFUSE_SECRET_KEY", "sk")


# ── is_enabled / get_trace_callbacks ──

def test_disabled_when_no_keys(monkeypatch):
    monkeypatch.setattr(trace, "LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setattr(trace, "LANGFUSE_SECRET_KEY", "")
    assert trace.is_enabled() is False
    assert trace.get_trace_callbacks("run1", "issue1") == []


def test_enabled_but_langfuse_not_installed(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(trace, "_load_handler_cls", lambda: None)
    # ตั้ง key แล้วแต่ไม่มี lib → คืน [] ไม่ raise
    assert trace.get_trace_callbacks("run1", "issue1") == []


def test_returns_handler_when_enabled(monkeypatch):
    _enable(monkeypatch)

    class _FakeHandler:
        pass

    monkeypatch.setattr(trace, "_load_handler_cls", lambda: _FakeHandler)
    cbs = trace.get_trace_callbacks("run1", "issue1")
    assert len(cbs) == 1 and isinstance(cbs[0], _FakeHandler)


def test_ctor_failure_is_swallowed(monkeypatch):
    _enable(monkeypatch)

    class _Boom:
        def __init__(self):
            raise RuntimeError("bad key")

    monkeypatch.setattr(trace, "_load_handler_cls", lambda: _Boom)
    assert trace.get_trace_callbacks("run1", "issue1") == []   # ไม่ทำ loop ล้ม


# ── build_invoke_config ──

def test_config_minimal_when_disabled(monkeypatch):
    monkeypatch.setattr(trace, "LANGFUSE_PUBLIC_KEY", "")
    cfg = trace.build_invoke_config("th1", run_id="r1", issue_id="i1")
    assert cfg == {"configurable": {"thread_id": "th1"}}   # ไม่มี callbacks/metadata


def test_config_includes_callbacks_and_session_when_enabled(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(trace, "_load_handler_cls", lambda: type("H", (), {}))
    cfg = trace.build_invoke_config("th1", run_id="r1", issue_id="i1")
    assert cfg["configurable"]["thread_id"] == "th1"
    assert len(cfg["callbacks"]) == 1
    assert cfg["metadata"]["langfuse_session_id"] == "r1"   # group ด้วย run_id
    assert cfg["metadata"]["issue_id"] == "i1"
