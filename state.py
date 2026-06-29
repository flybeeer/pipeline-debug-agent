"""
state.py — Context layer
========================
DebugState คือ "ความจำ" ที่ส่งต่อระหว่างทุก node ใน loop
แต่ละ node จะ return dict ของเฉพาะ field ที่อยากอัปเดต LangGraph จะ merge ให้เอง

หลักการ: อย่า mutate state ตรงๆ — return dict ใหม่เสมอ
"""

from __future__ import annotations

from typing import Literal, TypedDict

# tier ของ fix — ใช้ตัดสินว่าใคร approve (ดู nodes/tiering.py)
Tier = Literal["T1", "T2", "T3"]

# สถานะของ loop
Status = Literal["running", "fixed", "gave_up", "awaiting_review", "auto_merged"]


class DebugState(TypedDict, total=False):
    # ── identity ของ run นี้ (ใช้ audit / trace) ──
    issue_id: str          # id ของปัญหา เช่น "null-userid-042"
    run_id: str            # id ของ agent run นี้ (สำหรับ audit trailer)
    trace_url: str         # ลิงก์ trace ของ observability

    # ── Context: ป้อนเข้ามาตอนเริ่ม ──
    error_log: str         # error + stack trace ล่าสุด
    pipeline_code: str     # โค้ด pipeline ที่กำลังแก้
    file_path: str         # path ของไฟล์ที่แก้ (สำหรับเปิด PR)
    schema_info: str       # schema ของ table ที่เกี่ยวข้อง
    runbook: str           # runbook เดิม (ถ้าเคยเจอปัญหานี้) "" ถ้าไม่มี

    # ── ผลลัพธ์ระหว่างทาง ──
    diagnosis: str         # สาเหตุที่ LLM วิเคราะห์ได้
    proposed_fix: str      # โค้ดเวอร์ชันที่แก้แล้ว
    test_result: str       # ชั้น 1 — รันผ่านไหม: "PASS" / "FAIL: ..."
    semantic_result: str   # ชั้น 2 — ข้อมูลถูก semantic ไหม: "PASS: ..." / "FAIL: ..."
    attempts: int          # นับจำนวนรอบที่ลองแก้

    # ── tiering + routing ──
    is_idempotent: bool    # rerun ซ้ำแล้วผลเท่าเดิมไหม
    has_runbook: bool      # มี runbook ครอบคลุมไหม
    blast_radius: int      # จำนวน downstream ที่กระทบ (จาก lineage)
    is_data_fix: bool      # เป็นการแก้ "ข้อมูล" (อันตราย) ไม่ใช่แค่ "โค้ด" ไหม
    tier: Tier             # T1 / T2 / T3

    # ── output ──
    pr_url: str            # ลิงก์ PR ที่เปิด
    status: Status


def new_state(
    issue_id: str,
    error_log: str,
    pipeline_code: str,
    file_path: str,
    schema_info: str = "",
    runbook: str | None = None,
) -> DebugState:
    """สร้าง state เริ่มต้น พร้อม field ที่จำเป็นครบ

    runbook: ถ้าไม่ส่งมา (None) จะ auto-load จาก runbooks/<issue_id>.md
             ส่ง "" มาตรงๆ = บังคับไม่มี runbook (override การ auto-load)
    """
    import uuid

    # auto-load runbook เข้า Context (side-effect อ่านไฟล์อยู่ใน integrations)
    if runbook is None:
        from integrations.runbook import load_runbook
        runbook = load_runbook(issue_id)

    return DebugState(
        issue_id=issue_id,
        run_id=str(uuid.uuid4())[:8],
        trace_url="",
        error_log=error_log,
        pipeline_code=pipeline_code,
        file_path=file_path,
        schema_info=schema_info,
        runbook=runbook,
        diagnosis="",
        proposed_fix="",
        test_result="",
        semantic_result="",
        attempts=0,
        is_idempotent=False,
        has_runbook=bool(runbook),
        blast_radius=0,
        is_data_fix=False,
        tier="T3",  # default ปลอดภัยสุด: ต้องให้ DE review จนกว่าจะพิสูจน์ว่าเสี่ยงต่ำ
        pr_url="",
        status="running",
    )
