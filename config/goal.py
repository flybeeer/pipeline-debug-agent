"""
config/goal.py — Goal layer
===========================
นิยามเป้าหมาย + เกณฑ์ว่า "สำเร็จ" คืออะไร + ขอบเขตของ loop

⚠️ จำไว้: เกณฑ์สำเร็จไม่ใช่แค่ "ไม่มี error" แต่ต้อง "ข้อมูลถูกต้องตาม semantic" ด้วย
"""

# ── ขอบเขตของ inner loop ──
MAX_ATTEMPTS = 3          # circuit breaker: ลองแก้เกินนี้ → ยอมแพ้ escalate ขึ้นคน

# ── เกณฑ์ tiering (ดู nodes/tiering.py) ──
# blast radius ที่ยังถือว่า "เล็ก" — เกินนี้บังคับขึ้น T3
BLAST_RADIUS_SMALL = 3

# ── โมเดล ──
MODEL = "claude-opus-4-8"
MAX_TOKENS = 2000


# ── นิยาม "สำเร็จ" แบบเป็นโค้ด ──
def is_success(state: dict) -> bool:
    """
    fix ถือว่าสำเร็จก็ต่อเมื่อครบ "ทั้งสอง" เงื่อนไข (กฎเหล็ก #4):
      1. test ผ่าน (รันแล้วไม่มี error)         → state["test_result"] = "PASS"
      2. ข้อมูล output ถูกต้องตาม semantic       → state["semantic_result"] = "PASS"

    ⚠️ semantic_result มาจาก integrations.validation.validate_output_semantics
       ที่ node `test` เป็นคนรัน (side-effect แตะ staging แยกไว้ใน integrations)
       goal.py เป็น pure predicate เท่านั้น — แค่ "รวมเงื่อนไข" ไม่ไป query เอง

       ถ้ายังไม่ได้ตั้ง semantic_result (เช่นยังไม่ผ่าน Check) → ถือว่ายังไม่สำเร็จ
       fail-closed: ห้ามเคลม success ทั้งที่ semantic ยังไม่ผ่าน/ยังไม่ตรวจ
    """
    test_passed = state.get("test_result", "").startswith("PASS")
    semantic_passed = state.get("semantic_result", "").startswith("PASS")
    return test_passed and semantic_passed
