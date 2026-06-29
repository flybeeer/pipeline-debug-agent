"""
__main__.py — CLI entry point
=============================
รัน agent:      python -m pipeline_debug_agent --issue-id <id>
ดู state history: python -m pipeline_debug_agent --inspect --thread-id <id>
"""

import argparse

from graph import app
from observability.trace import build_invoke_config
from state import new_state


def run(issue_id: str):
    # ── ตัวอย่าง input (แทนด้วยการดึงจาก alert/log จริง) ──
    initial = new_state(
        issue_id=issue_id,
        error_log="KeyError: 'user_id' not found in DataFrame",
        pipeline_code="df.groupby('user_id').agg({'amount': 'sum'})",
        file_path="models/sales/daily_sales.sql",
        schema_info="sales(order_id, customer_id, amount, created_at)",
        # ไม่ส่ง runbook → auto-load จาก runbooks/<issue_id>.md ถ้ามี
    )

    # config: checkpointer thread + LangFuse trace (ถ้าตั้ง key) group ด้วย run_id
    config = build_invoke_config(
        thread_id=issue_id, run_id=initial["run_id"], issue_id=issue_id
    )
    result = app.invoke(initial, config=config)
    print(f"\n🏁 จบที่สถานะ: {result['status']} "
          f"(tier={result.get('tier')}, ลองไป {result['attempts']} รอบ)")
    if result.get("pr_url"):
        print(f"   PR: {result['pr_url']}")


def inspect(thread_id: str):
    """ย้อนดู state ของทุก step — ใช้ debug ว่ารอบไหนเริ่มผิด"""
    config = {"configurable": {"thread_id": thread_id}}
    print(f"📜 ประวัติทุก step ของ thread '{thread_id}':")
    for snap in app.get_state_history(config):
        nxt = snap.next or ("END",)
        print(f"   next={nxt} | attempts={snap.values.get('attempts')} "
              f"| status={snap.values.get('status')}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Pipeline Debugging Agent")
    p.add_argument("--issue-id", default="demo-001")
    p.add_argument("--inspect", action="store_true")
    p.add_argument("--thread-id")
    args = p.parse_args()

    if args.inspect:
        inspect(args.thread_id or args.issue_id)
    else:
        run(args.issue_id)
