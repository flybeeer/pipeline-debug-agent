"""
nodes/tiering.py — ประเมิน Tier
==============================
ตัดสินว่า fix นี้ปลอดภัยพอให้ใคร approve โดยเช็ค 3 เกณฑ์:
  idempotent? + มี runbook? + blast radius เล็ก?

ขาดข้อใดข้อหนึ่ง / เป็น data fix / blast ใหญ่  → ดันขึ้น T3 (ต้อง DE review)
"""

from config.goal import BLAST_RADIUS_SMALL
from integrations.lineage import count_downstream
from state import DebugState


def _looks_idempotent(code: str) -> bool:
    """heuristic ง่ายๆ: มี pattern ที่ทำให้ rerun ปลอดภัยไหม
    (ในงานจริงควรเช็คให้ละเอียดกว่านี้ หรือให้ LLM ช่วยประเมิน)"""
    safe = ("merge", "insert overwrite", "create or replace", "delete where")
    danger = ("insert into",)
    low = code.lower()
    has_safe = any(s in low for s in safe)
    has_danger = any(d in low for d in danger) and not has_safe
    return has_safe and not has_danger


def _is_data_fix(code: str) -> bool:
    """เป็นการแก้ 'ข้อมูล' (อันตราย/ย้อนยาก) ไม่ใช่แค่ 'โค้ด' ไหม"""
    low = code.lower()
    return any(k in low for k in ("update ", "delete ", "truncate", "drop "))


def assess_tier(state: DebugState) -> dict:
    """เช็ค 3 เกณฑ์แล้วกำหนด tier"""
    code = state["proposed_fix"]

    is_idempotent = _looks_idempotent(code)
    is_data_fix = _is_data_fix(code)
    has_runbook = bool(state.get("runbook"))
    blast = count_downstream(state["file_path"])   # นับ downstream จาก lineage

    blast_small = blast <= BLAST_RADIUS_SMALL

    # ── ตรรกะการตัดสิน ──
    if is_data_fix or not blast_small:
        tier = "T3"                                  # แตะข้อมูล / กระทบวงกว้าง → DE
    elif is_idempotent and has_runbook and blast_small:
        # ปลอดภัยทุกด้าน — ถ้าเป็น error ชั่วคราว/backward-compatible อาจ auto ได้
        tier = "T1"
    elif is_idempotent and blast_small:
        tier = "T2"                                  # ops/on-call ทำได้
    else:
        tier = "T3"                                  # ขาดเงื่อนไข → ปลอดภัยไว้ก่อน

    print(f"🏷️  Tier={tier} (idempotent={is_idempotent}, "
          f"runbook={has_runbook}, blast={blast}, data_fix={is_data_fix})")

    return {
        "is_idempotent": is_idempotent,
        "is_data_fix": is_data_fix,
        "has_runbook": has_runbook,
        "blast_radius": blast,
        "tier": tier,
    }
