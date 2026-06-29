"""
observability/trace.py — Observability layer (LangFuse)
======================================================
ต่อ trace เพื่อ audit + debug ว่าแต่ละรอบ agent คิด/ทำอะไร (กฎเหล็ก #6: ต้อง audit ได้)

LangFuse ทำงานผ่าน LangChain callback handler — ส่งเข้า config ตอน app.invoke
แล้วทุก node (analyze/fix/test/...) จะถูก trace อัตโนมัติ

โหมดการทำงาน (เลือกจาก env):
  • ตั้ง LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY → ส่ง trace จริง
  • ไม่ตั้ง                                        → ปิดเงียบ (offline/demo/test รันได้)

ติดตั้ง:  pip install -e .[observability]
group trace ด้วย run_id (session) → PR แนบ run_id อยู่แล้ว ตามไปดู trace ใน LangFuse ได้
"""

from __future__ import annotations

import os
from typing import Any

LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")


def is_enabled() -> bool:
    """ตั้ง key ครบไหมว่าจะส่ง trace จริง — ไม่ครบ = ปิดเงียบ (กันพังตอน offline)"""
    return bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)


def _load_handler_cls() -> Any:
    """หา CallbackHandler ของ langfuse — รองรับทั้ง v3 (langfuse.langchain) และ v2"""
    try:
        from langfuse.langchain import CallbackHandler  # langfuse v3
        return CallbackHandler
    except ImportError:
        pass
    try:
        from langfuse.callback import CallbackHandler  # langfuse v2
        return CallbackHandler
    except ImportError:
        return None


def get_trace_callbacks(run_id: str = "", issue_id: str = "") -> list:
    """
    คืน list ของ callback handler สำหรับส่งเข้า config ตอน invoke
    คืน [] ถ้ายังไม่ได้ตั้ง key หรือยังไม่ได้ติดตั้ง langfuse → ไม่กระทบการรัน
    """
    if not is_enabled():
        return []

    cls = _load_handler_cls()
    if cls is None:
        print("⚠️  ตั้ง LANGFUSE_* แล้วแต่ยังไม่ได้ติดตั้ง langfuse: "
              "pip install -e .[observability]")
        return []

    try:
        handler = cls()   # อ่าน key/host จาก env เอง
    except Exception as e:   # ctor พัง (key ผิด ฯลฯ) — อย่าให้ observability ล้ม loop หลัก
        print(f"⚠️  สร้าง LangFuse handler ไม่สำเร็จ: {str(e)[:80]}")
        return []

    return [handler]


def build_invoke_config(thread_id: str, run_id: str = "", issue_id: str = "") -> dict:
    """
    ประกอบ config สำหรับ app.invoke ให้ครบ: checkpointer thread + trace callbacks

    group trace ด้วย run_id (langfuse session) เพื่อให้ย้อนหา run จาก PR ได้
    ถ้า observability ปิดอยู่ ก็คืนแค่ configurable เฉยๆ (ไม่มี side-effect)
    """
    config: dict = {"configurable": {"thread_id": thread_id}}

    callbacks = get_trace_callbacks(run_id, issue_id)
    if callbacks:
        config["callbacks"] = callbacks
        config["run_name"] = f"debug-{issue_id or thread_id}"
        # v2 handler อ่าน session/metadata จากตรงนี้ได้ — group ด้วย run_id
        config["metadata"] = {
            "langfuse_session_id": run_id or thread_id,
            "issue_id": issue_id,
            "run_id": run_id,
        }

    return config
