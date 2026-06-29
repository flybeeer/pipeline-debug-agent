"""
integrations/llm.py — LLM factory (ให้ inject ได้)
=================================================
รวมการสร้าง LLM ไว้ที่เดียว + เปิดทางให้ "เปลี่ยนตัว" ได้ (test / demo offline)

ทำไมต้องมีชั้นนี้:
  • prod: คืน ChatAnthropic จริง (อ่าน ANTHROPIC_API_KEY จาก env)
  • test/demo: เรียก set_llm(fake) เพื่อรัน graph ทั้งวงได้โดยไม่ยิง network / ไม่ต้องมี key

import ChatAnthropic แบบ lazy — ถ้าไม่ได้ใช้ LLM จริง (เช่น demo inject fake)
ก็ไม่ต้องติดตั้ง langchain-anthropic ให้ครบ
"""

from __future__ import annotations

from typing import Any

from config.goal import MAX_TOKENS, MODEL

# ตัว LLM ที่ override ไว้ (None = ใช้ ChatAnthropic จริง)
_override: Any = None


def set_llm(llm: Any) -> None:
    """แทนที่ LLM ด้วยตัวอื่น (ใช้ตอน test/demo) — ส่ง None เพื่อกลับไปใช้ของจริง"""
    global _override
    _override = llm


def get_llm() -> Any:
    """คืน LLM ที่ใช้งานอยู่ — node ทุกตัวเรียกผ่านนี้ ไม่สร้าง ChatAnthropic เอง"""
    if _override is not None:
        return _override
    from langchain_anthropic import ChatAnthropic  # lazy: สร้างจริงเฉพาะตอนใช้
    return ChatAnthropic(model=MODEL, max_tokens=MAX_TOKENS)
