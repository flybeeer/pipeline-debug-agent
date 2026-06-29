"""
integrations/lineage.py — Blast radius layer
============================================
นับ downstream ที่ขึ้นกับไฟล์/model ที่จะแก้ → ใช้ประเมิน blast radius (ดู nodes/tiering.py)

แหล่ง lineage: dbt manifest.json (target/manifest.json) — มี `child_map` ที่ map
unique_id → ลูกโดยตรง เดินตามนี้แบบ transitive ได้ทุก descendant
(stack อื่นแทนได้: OpenLineage / Marquez / DataHub / column-level lineage)

🛡️ fail-safe: ถ้าหา lineage ไม่ได้ (ไม่มี manifest / หา node ไม่เจอ) → คืนค่า "ใหญ่"
   เพื่อดันขึ้น T3 ไว้ก่อน — ไม่รู้ว่ากระทบแค่ไหน ห้ามเดาว่าเล็กแล้วปล่อย auto
   (หลักเดียวกับ fail-closed ของ semantic validation — กฎเหล็ก #4/#7)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DBT_MANIFEST_PATH = os.environ.get("DBT_MANIFEST_PATH", "target/manifest.json")

# ค่าที่ใช้เมื่อ "ไม่รู้" blast radius — ต้องมากกว่า BLAST_RADIUS_SMALL เสมอ → บังคับ T3
UNKNOWN_BLAST = 9999


def _resolve_node(manifest: dict, file_path: str) -> str | None:
    """หา unique_id ของ node จาก path ของไฟล์ (เทียบ original_file_path / path)"""
    for section in ("nodes", "sources"):
        for uid, node in manifest.get(section, {}).items():
            if file_path in (node.get("original_file_path"), node.get("path")):
                return uid
    return None


def _descendants(child_map: dict, start: str) -> set[str]:
    """เดิน child_map แบบ BFS เก็บ descendant ทั้งหมด (ไม่รวมตัวเอง)"""
    seen: set[str] = set()
    queue = list(child_map.get(start, []))
    while queue:
        uid = queue.pop()
        if uid in seen:
            continue
        seen.add(uid)
        queue.extend(child_map.get(uid, []))
    return seen


def count_downstream(file_path: str, manifest_path: str | None = None) -> int:
    """
    คืนจำนวน asset ที่อยู่ downstream ของไฟล์นี้ — ยิ่งมาก = blast radius ยิ่งใหญ่

    นับเฉพาะ asset จริงที่ผู้บริโภคเห็น (model/snapshot/seed/exposure/metric)
    ตัด test/unit_test ออก เพราะ test พังไม่ได้ "ลาม" ไปกระทบ consumer
    """
    path = Path(manifest_path or DBT_MANIFEST_PATH)
    if not path.exists():
        return UNKNOWN_BLAST   # ไม่มี manifest → ไม่รู้ → เสี่ยงสูงไว้ก่อน

    try:
        manifest = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return UNKNOWN_BLAST

    child_map = manifest.get("child_map")
    if child_map is None:
        return UNKNOWN_BLAST   # manifest ไม่มี lineage → คำนวณไม่ได้

    uid = _resolve_node(manifest, file_path)
    if uid is None:
        return UNKNOWN_BLAST   # หา node ไม่เจอ → ไม่รู้ downstream

    descendants = _descendants(child_map, uid)
    # ตัด test ออกจากการนับ blast (test พังไม่ลามไป consumer)
    real = [d for d in descendants if not d.startswith(("test.", "unit_test."))]
    return len(real)
