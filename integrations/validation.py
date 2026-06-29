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
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# บังคับให้รู้ตัวว่ากำลังดึงสถิติจากที่ไหน — ห้ามไปอ่าน prod
TARGET_ENV = os.environ.get("PIPELINE_TARGET_ENV", "staging")

# ที่เก็บ check spec ต่อ issue (override ได้ตอน test) — ดู load_check_config()
CHECKS_DIR = os.environ.get("CHECKS_DIR", "config/checks")

# regex กัน SQL injection: ชื่อ table/column จาก config ต้องเป็น identifier ปกติเท่านั้น
# รองรับ schema-qualified เช่น "staging.daily_sales"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _safe_ident(name: str) -> str:
    """ตรวจว่าเป็น identifier ที่ปลอดภัยแล้ว quote ด้วย double-quote ต่อ segment

    ค่าพวกนี้มาจาก config ที่ DE เขียน (trusted) แต่ยัง validate ไว้กันพลาด/พิมพ์ผิด
    """
    if not _IDENT.match(name):
        raise ValueError(f"identifier ไม่ปลอดภัย/ผิดรูป: {name!r}")
    return ".".join(f'"{seg}"' for seg in name.split("."))


@dataclass
class OutputStats:
    """สถิติของ output ที่ fix สร้างบน staging — วัตถุดิบสำหรับ semantic check

    เก็บเฉพาะ aggregate ที่ตรวจ semantic ได้ ไม่ต้องดึงข้อมูลดิบทั้งก้อน
    """
    row_count: int = 0
    null_counts: dict[str, int] = field(default_factory=dict)   # คอลัมน์ -> จำนวน null
    column_sums: dict[str, float] = field(default_factory=dict)  # คอลัมน์ -> ผลรวม
    baseline_row_count: int | None = None   # row count ของ run ก่อนหน้า (ถ้ามี) ไว้เทียบ


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
    ดึงสถิติของ output ที่ fix สร้างขึ้นบน staging ด้วย DuckDB (read-only)

    🚨 ต้องอ่านจาก staging/test เท่านั้น — ห้ามแตะ prod
    config ต้องมี: database (path ไฟล์ DuckDB), target_table
    optional: key_columns, sum_columns, baseline_row_count

    รัน aggregate query เดียวจบ — ไม่ดึงข้อมูลดิบ:
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE <key> IS NULL) AS null__<key>, ...
               SUM(<col>) AS sum__<col>, ...
        FROM <target_table>
    """
    if TARGET_ENV == "production":
        raise RuntimeError(
            "❌ ปฏิเสธ: fetch_output_stats ต้องไม่อ่านจาก production "
            "(ตั้ง PIPELINE_TARGET_ENV=staging)"
        )

    import duckdb  # lazy import

    database = config["database"]
    table = _safe_ident(config["target_table"])
    key_columns: list[str] = list(config.get("key_columns", []))
    sum_columns: list[str] = list(config.get("sum_columns", []))

    # ── ประกอบ SELECT แบบ parametrize ไม่ได้ (เป็น identifier) → ใช้ _safe_ident กัน ──
    selects = ["COUNT(*) AS row_count"]
    for col in key_columns:
        selects.append(
            f'COUNT(*) FILTER (WHERE {_safe_ident(col)} IS NULL) AS "null__{col}"'
        )
    for col in sum_columns:
        selects.append(f'SUM({_safe_ident(col)}) AS "sum__{col}"')

    query = f"SELECT {', '.join(selects)} FROM {table}"

    # read_only=True: validation อ่านอย่างเดียว ไม่ควรเขียนอะไรลง staging
    con = duckdb.connect(database=database, read_only=True)
    try:
        row = con.execute(query).fetchone()
        cols = [d[0] for d in con.description]
    finally:
        con.close()

    data = dict(zip(cols, row, strict=True))
    return OutputStats(
        row_count=int(data["row_count"]),
        null_counts={col: int(data[f"null__{col}"]) for col in key_columns},
        # SUM บนตารางว่างคืน None → map เป็น 0.0
        column_sums={col: float(data[f"sum__{col}"] or 0.0) for col in sum_columns},
        baseline_row_count=config.get("baseline_row_count"),
    )


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
         • connect/query DuckDB พัง                  → error (ถือว่าไม่ผ่าน)
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
