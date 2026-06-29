"""
integrations/runbook.py — Runbook loader (Context layer)
=======================================================
โหลด runbook ของ issue เข้า state.runbook อัตโนมัติ (แพตเทิร์นเดียวกับ load_check_config)

runbook ทำ 2 หน้าที่ (ดู runbooks/README.md):
  1. เป็น context ให้ agent — nodes/analyze.py อ่านตอนวิเคราะห์ → fix แม่นขึ้น
  2. ทำให้ has_runbook=True ใน nodes/tiering.py → ดัน T2 → T1 ได้

side-effect (อ่านไฟล์) อยู่ที่นี่ตามคอนเวนชัน — ไม่ปนใน node/state
"""

from __future__ import annotations

import os
from pathlib import Path

# ที่เก็บ runbook ต่อ issue (override ได้ตอน test) — ดู load_runbook()
RUNBOOKS_DIR = os.environ.get("RUNBOOKS_DIR", "runbooks")


def load_runbook(issue_id: str, runbooks_dir: str | None = None) -> str:
    """อ่าน runbook ของ issue จาก runbooks/<issue_id>.md

    คืน "" ถ้าไม่มีไฟล์ — ถือว่าเป็นปัญหาใหม่ (has_runbook=False → ปลอดภัยไว้ก่อน)
    """
    base = Path(runbooks_dir or RUNBOOKS_DIR)
    path = base / f"{issue_id}.md"
    if path.exists():
        return path.read_text()
    return ""
