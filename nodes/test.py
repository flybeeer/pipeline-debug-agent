"""
nodes/test.py — Check
=====================
รันโค้ดที่แก้แล้วกับ test/staging data

🚨 กฎเหล็ก: ขั้นนี้ห้ามแตะ production data เด็ดขาด — staging/test เท่านั้น
"""

from config.goal import is_success
from integrations.runner import execute_pipeline
from integrations.validation import validate_output_semantics
from state import DebugState


def run_test(state: DebugState) -> dict:
    """ทดสอบ fix บน test data — Check 2 ชั้น: (1) รันผ่านไหม (2) semantic ถูกไหม

    🚨 กฎเหล็ก #4: "รันผ่าน" ไม่พอ ต้อง "ข้อมูลถูกต้องตาม semantic" ด้วย
    """
    try:
        # ── ชั้นที่ 1: รันได้ไหม (execute_pipeline ต้องชี้ staging เท่านั้น) ──
        execute_pipeline(state["proposed_fix"])

    except Exception as e:
        print(f"❌ Test fail (รันไม่ผ่าน): {str(e)[:60]}")
        # ส่ง error ใหม่ + โค้ดล่าสุดกลับเข้า Context เพื่อให้รอบหน้าวิเคราะห์ต่อ
        return {
            "test_result": f"FAIL: {e}",
            "error_log": str(e),
            "pipeline_code": state["proposed_fix"],
        }

    # ── ชั้นที่ 2: รันผ่านแล้ว — ตรวจ semantic ของ output บน staging ──
    semantic_ok, detail = validate_output_semantics({**state, "test_result": "PASS"})
    result = {
        "test_result": "PASS",
        "semantic_result": ("PASS: " if semantic_ok else "FAIL: ") + detail,
    }

    # นิยาม success รวม (test ผ่าน + semantic ผ่าน) อยู่ที่ config/goal.py
    if is_success({**state, **result}):
        print(f"✅ Test ผ่าน + ข้อมูลถูกต้อง ({detail})")
        return {**result, "status": "fixed"}

    # รันได้แต่ตัวเลข/ข้อมูลไม่ถูก — feed กลับเข้า loop ให้วิเคราะห์รอบใหม่
    print(f"⚠️ Test รันได้แต่ semantic ไม่ผ่าน: {detail}")
    return {
        **result,
        "error_log": f"semantic validation ไม่ผ่าน: {detail}",
    }
