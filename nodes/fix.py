"""
nodes/fix.py — Action (ครึ่งหลัง)
================================
เสนอโค้ดที่แก้แล้ว จากสาเหตุที่วิเคราะห์ได้
⚠️ ขั้นนี้แค่ "เสนอ" — ยังไม่แตะ production
"""

from integrations.llm import get_llm
from state import DebugState


def propose_fix(state: DebugState) -> dict:
    """เสนอโค้ดที่แก้แล้ว + เพิ่มตัวนับ attempts"""
    prompt = f"""สาเหตุที่ fail: {state['diagnosis']}

แก้โค้ดนี้ให้ถูกต้อง — ตอบกลับมาเฉพาะโค้ดที่แก้แล้วเท่านั้น ไม่ต้องอธิบาย:
{state['pipeline_code']}

ข้อกำหนดสำคัญ:
- ถ้าเป็น query ที่เขียนข้อมูล ให้ทำให้ idempotent (MERGE / INSERT OVERWRITE / partition-based)
  ไม่ใช่ INSERT INTO ที่ทำให้ข้อมูลซ้ำเมื่อ rerun
"""
    response = get_llm().invoke(prompt)
    return {
        "proposed_fix": response.content,
        "attempts": state["attempts"] + 1,
    }
