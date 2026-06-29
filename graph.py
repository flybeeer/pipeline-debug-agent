"""
graph.py — ประกอบ Graph (Repeat layer)
=====================================
เชื่อมทุก node เข้าด้วยกัน + กำหนด conditional edges สำหรับ loop

โครงสร้าง:
    START → analyze → fix → test ─┬─(retry)→ analyze   [inner loop]
                                  ├─(fixed)→ tiering → submit_pr → END
                                  └─(give_up)→ escalate → END   [outer gate]
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from config.goal import MAX_ATTEMPTS
from nodes.analyze import analyze_error
from nodes.fix import propose_fix
from nodes.submit_pr import escalate, submit_fix
from nodes.test import run_test
from nodes.tiering import assess_tier
from state import DebugState


def route_after_test(state: DebugState) -> str:
    """Conditional edge: จะวน loop, ไปเปิด PR, หรือยอมแพ้"""
    if state.get("status") == "fixed":
        return "fixed"
    if state["attempts"] >= MAX_ATTEMPTS:   # circuit breaker
        return "give_up"
    return "retry"                          # 🔁 วนกลับไปวิเคราะห์ใหม่


def build_app(checkpointer=None):
    """สร้าง compiled LangGraph app

    เพิ่ม human-in-the-loop ได้ด้วย interrupt_before=["test"]
    เพื่อให้คน approve ก่อนรัน test (มีประโยชน์ตอนงานเสี่ยง)
    """
    g = StateGraph(DebugState)

    # nodes
    g.add_node("analyze", analyze_error)
    g.add_node("fix", propose_fix)
    g.add_node("test", run_test)
    g.add_node("tiering", assess_tier)
    g.add_node("submit_pr", submit_fix)
    g.add_node("escalate", escalate)

    # inner loop: analyze → fix → test
    g.add_edge(START, "analyze")
    g.add_edge("analyze", "fix")
    g.add_edge("fix", "test")

    # routing หลัง test
    g.add_conditional_edges(
        "test",
        route_after_test,
        {
            "retry": "analyze",      # 🔁 loop
            "fixed": "tiering",      # ✅ ไปประเมิน tier แล้วเปิด PR
            "give_up": "escalate",   # 🛑 ให้คนสานต่อ
        },
    )

    # outer gate
    g.add_edge("tiering", "submit_pr")
    g.add_edge("submit_pr", END)
    g.add_edge("escalate", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


# instance พร้อมใช้ (dev ใช้ MemorySaver, prod เปลี่ยนเป็น PostgresSaver)
app = build_app()
