"""
integrations/validation.py — Semantic validation layer (หัวใจของ Check)
======================================================================
🚨 กฎเหล็ก #4: "สำเร็จ" = test ผ่าน + ข้อมูลถูกต้องตาม semantic
   ไม่ใช่แค่ "ไม่มี error" — fix ที่รันผ่านแต่ทำตัวเลขเพี้ยนเงียบๆ คืออันตรายที่สุด

ชั้นนี้คือสิ่งที่ทำให้ inner loop "เชื่อถือได้" — ถ้า check อ่อน ทั้ง loop ไร้ค่า
แนวคิด: หลัง fix รันผ่านบน staging แล้ว เราดึง "สถิติของ output" ออกมา
แล้วเอาไปผ่านชุด assertion (row count, null ใน key, ยอดรวม reconcile ฯลฯ)

side-effect (query staging) อยู่ที่นี่ตามคอนเวนชัน — node เรียกใช้ ไม่ทำเอง
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# OutputStats + การต่อ warehouse ย้ายไป integrations/warehouse.py (รองรับหลาย engine)
# re-export OutputStats/_safe_ident ไว้เพื่อ backward-compat ของโค้ด/test เดิม
from integrations.warehouse import OutputStats, _safe_ident, get_adapter

# บังคับให้รู้ตัวว่ากำลังดึงสถิติจากที่ไหน — ห้ามไปอ่าน prod (ด่านนี้ + ใน adapter)
TARGET_ENV = os.environ.get("PIPELINE_TARGET_ENV", "staging")

# ที่เก็บ check spec ต่อ issue (override ได้ตอน test) — ดู load_check_config()
CHECKS_DIR = os.environ.get("CHECKS_DIR", "config/checks")

# ใช้ภายในไฟล์นี้ (ทำให้ ruff ไม่ฟ้องว่า import มาไม่ใช้ + ให้ test เรียก v._safe_ident ได้)
__all__ = ["OutputStats", "_safe_ident", "validate_output_semantics"]


@dataclass
class SemanticCheck:
    """assertion หนึ่งข้อ — ตั้งชื่อได้ เพื่อให้ report บอกชัดว่าข้อไหน fail

    predicate คืน True = ผ่าน, False = ไม่ผ่าน
    """
    name: str
    predicate: Callable[[OutputStats], bool]
    detail: Callable[[OutputStats], str] = lambda s: ""


# ── ชุด check มาตรฐานที่ pipeline ส่วนใหญ่ใช้ร่วมกันได้ ──
# (เป็นตัวอย่างที่ "ทำงานจริง" บน OutputStats — ปรับ/เพิ่มต่อ tier ของแต่ละ pipeline)

def no_nulls_in_keys(*key_columns: str) -> SemanticCheck:
    """key column สำคัญต้องไม่มี null — null ใน key มักลามไป join/dedup ผิดทั้งสาย"""
    keys = set(key_columns)
    return SemanticCheck(
        name=f"no_nulls_in_keys({', '.join(keys)})",
        predicate=lambda s: all(s.null_counts.get(k, 0) == 0 for k in keys),
        detail=lambda s: ", ".join(
            f"{k}={s.null_counts.get(k, 0)} nulls" for k in keys if s.null_counts.get(k, 0)
        ),
    )


def row_count_within(tolerance: float = 0.10) -> SemanticCheck:
    """row count ต้องไม่ผิดจาก baseline เกิน tolerance (กันข้อมูลหาย/ซ้ำเงียบๆ)

    ถ้าไม่มี baseline ให้ผ่าน (เทียบไม่ได้) แต่ตัว validate รวมจะ flag ว่า UNVERIFIED
    """
    def ok(s: OutputStats) -> bool:
        if s.baseline_row_count is None or s.baseline_row_count == 0:
            return True
        drift = abs(s.row_count - s.baseline_row_count) / s.baseline_row_count
        return drift <= tolerance

    return SemanticCheck(
        name=f"row_count_within({tolerance:.0%})",
        predicate=ok,
        detail=lambda s: (
            f"now={s.row_count}, baseline={s.baseline_row_count}"
            if s.baseline_row_count else "no baseline"
        ),
    )


def non_empty_output() -> SemanticCheck:
    """output ต้องไม่ว่าง — pipeline ที่ 'รันผ่าน' แต่ออก 0 แถวคือ fail เงียบ"""
    return SemanticCheck(
        name="non_empty_output",
        predicate=lambda s: s.row_count > 0,
        detail=lambda s: f"row_count={s.row_count}",
    )


def load_check_config(issue_id: str, checks_dir: str | None = None) -> dict | None:
    """โหลด check spec ของ issue จาก config/checks/<issue_id>.yaml (หรือ .yml)

    คืน None ถ้าไม่มีไฟล์ — validate จะถือเป็น UNVERIFIED (fail-closed)
    รูปแบบไฟล์ดูตัวอย่างที่ config/checks/example.yaml
    """
    import yaml  # lazy import: ต้องใช้ตอน runtime เท่านั้น

    base = Path(checks_dir or CHECKS_DIR)
    for ext in (".yaml", ".yml"):
        path = base / f"{issue_id}{ext}"
        if path.exists():
            return yaml.safe_load(path.read_text()) or {}
    return None


def build_checks(config: dict) -> list[SemanticCheck]:
    """แปลง config → ชุด SemanticCheck ที่จะรันกับ OutputStats"""
    checks: list[SemanticCheck] = [non_empty_output()]
    if keys := config.get("key_columns"):
        checks.append(no_nulls_in_keys(*keys))
    checks.append(row_count_within(float(config.get("row_count_tolerance", 0.10))))
    return checks


def fetch_output_stats(state: dict, config: dict) -> OutputStats:
    """
    ดึงสถิติของ output ที่ fix สร้างขึ้นบน staging ผ่าน warehouse adapter

    🚨 ต้องอ่านจาก staging/test เท่านั้น — ห้ามแตะ prod (ด่านนี้ + ในตัว adapter)
    warehouse = Apache Iceberg (config: iceberg_table + catalog) — ดู integrations/warehouse.py
      credential ผ่าน ~/.pyiceberg.yaml หรือ env PYICEBERG_*
    optional: key_columns, sum_columns, baseline_row_count
    """
    if TARGET_ENV == "production":
        raise RuntimeError(
            "❌ ปฏิเสธ: fetch_output_stats ต้องไม่อ่านจาก production "
            "(ตั้ง PIPELINE_TARGET_ENV=staging)"
        )

    adapter = get_adapter(config.get("warehouse"))
    return adapter.fetch_stats(config)


def validate_output_semantics(
    state: dict,
    *,
    config: dict | None = None,
    checks: list[SemanticCheck] | None = None,
    checks_dir: str | None = None,
) -> tuple[bool, str]:
    """
    รันชุด semantic check กับ output ของ fix แล้วสรุปว่า "ข้อมูลถูกต้องตาม semantic" ไหม

    คืน (ok, detail):
      • ok=True  → ผ่านทุก check
      • ok=False → มีอย่างน้อย 1 check ไม่ผ่าน / ยืนยันไม่ได้ (detail บอกสาเหตุ)

    ⚠️ fail-closed ทุกกรณีที่ "ยืนยันไม่ได้" — ห้ามเคลม success ทั้งที่ยังตรวจ semantic
       ไม่สำเร็จ (ตรงกับกฎเหล็ก #4 — ปลอดภัยกว่าปล่อยผ่านแล้วตัวเลขเพี้ยนเงียบ):
         • ไม่มี check config ของ issue นี้        → UNVERIFIED
         • fetch_output_stats ยังเป็น stub          → UNVERIFIED
         • connect/อ่าน warehouse พัง                → error (ถือว่าไม่ผ่าน)
    """
    # หา config: รับมาตรงๆ (ตอน test) หรือโหลดจาก config/checks/<issue_id>.yaml
    if config is None:
        config = load_check_config(state.get("issue_id", ""), checks_dir)
    if config is None:
        return False, "UNVERIFIED: ไม่มี check config ของ issue นี้ (fail-closed)"

    try:
        stats = fetch_output_stats(state, config)
    except NotImplementedError:
        return False, "UNVERIFIED: ยังไม่ได้ต่อ semantic validation จริง (fail-closed)"
    except Exception as e:
        # connection/query พัง = ตรวจไม่ได้ = ไม่ผ่าน (ไม่ยอมปล่อยผ่านเงียบ)
        return False, f"semantic validation error: {str(e)[:80]}"

    checks = checks if checks is not None else build_checks(config)

    failures = [c for c in checks if not c.predicate(stats)]
    if failures:
        report = "; ".join(
            f"{c.name}" + (f" [{d}]" if (d := c.detail(stats)) else "")
            for c in failures
        )
        return False, f"semantic check ไม่ผ่าน: {report}"

    return True, f"semantic ok ({len(checks)} checks ผ่านหมด)"
