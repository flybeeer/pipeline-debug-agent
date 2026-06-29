"""
nodes/submit_pr.py — Review (Outer gate)
========================================
เปิด Pull Request แทนการแก้ production สดๆ แล้ว route ตาม tier

🚨 กฎเหล็ก:
  - agent มีสิทธิ์แค่ "สร้าง branch + เปิด PR" เท่านั้น
  - T1 + CI เขียว เท่านั้นที่ตั้ง auto-merge ได้
  - data fix ห้ามมาถึงตรงนี้ในรูปคำสั่งสด — ต้องเป็น migration script ที่ reproducible
"""

from integrations.git_client import create_branch_and_pr, enable_auto_merge
from state import DebugState


def submit_fix(state: DebugState) -> dict:
    """เปิด PR + route ตาม tier"""
    if state.get("is_data_fix"):
        # กันพลาด: data fix ต้องเป็น migration script ที่ commit เข้า git
        # ไม่ใช่ปล่อยให้ agent รัน UPDATE/DELETE สดบน prod
        print("⛔ เป็น data fix — ต้องทำผ่าน migration script ที่ review โดย DE")

    pr = create_branch_and_pr(
        issue_id=state["issue_id"],
        file_path=state["file_path"],
        new_code=state["proposed_fix"],
        diagnosis=state["diagnosis"],
        error_log=state["error_log"],
        test_result=state["test_result"],
        run_id=state["run_id"],
        trace_url=state.get("trace_url", ""),
    )

    # routing ตาม tier
    if state["tier"] == "T1":
        enable_auto_merge(pr)   # merge อัตโนมัติเมื่อ CI ผ่านครบ
        print(f"🤖 T1 → auto-merge เมื่อ CI เขียว: {pr.url}")
        return {"pr_url": pr.url, "status": "auto_merged"}

    approver = "Data Ops / on-call" if state["tier"] == "T2" else "Data Engineer (เจ้าของ pipeline)"
    print(f"👀 {state['tier']} → รอ {approver} review: {pr.url}")
    return {"pr_url": pr.url, "status": "awaiting_review"}


def escalate(state: DebugState) -> dict:
    """เรียกเมื่อ agent ยอมแพ้ (ครบ MAX_ATTEMPTS) — เปิด PR/issue ให้คนช่วยต่อ"""
    pr = create_branch_and_pr(
        issue_id=state["issue_id"],
        file_path=state["file_path"],
        new_code=state.get("proposed_fix", state["pipeline_code"]),
        diagnosis=state.get("diagnosis", ""),
        error_log=state["error_log"],
        test_result=state.get("test_result", "ยังแก้ไม่ได้"),
        run_id=state["run_id"],
        trace_url=state.get("trace_url", ""),
        draft=True,   # เปิดเป็น draft — agent แก้ไม่สำเร็จ ต้องให้คนสานต่อ
    )
    print(f"🛑 ยอมแพ้หลัง {state['attempts']} รอบ → เปิด draft PR ให้ DE: {pr.url}")
    return {"pr_url": pr.url, "status": "gave_up", "tier": "T3"}
