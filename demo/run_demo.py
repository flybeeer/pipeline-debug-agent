"""
demo/run_demo.py — รัน agent ทั้งวงจร "จริง" แบบ offline
======================================================
รันด้วย:  python demo/run_demo.py

ทำให้เห็น loop เต็ม Goal→Context→Action→Check→Repeat→Review โดย:
  • LLM        → fake offline (ไม่ต้องมี ANTHROPIC_API_KEY, ไม่ยิง network)
  • runner     → รัน SQL ของ fix ลง DuckDB staging จริง
  • validation → อ่าน output table จาก DuckDB เดียวกัน ตรวจ semantic จริง
  • git/PR     → stub (พิมพ์ว่าเปิด PR — ไม่แตะ repo จริง)

scenario: pipeline `daily_sales` เจอ null ใน user_id → fix เขียนใหม่ให้ idempotent
          + กรอง null ออก แล้วผ่าน semantic check (no null key + row count + non-empty)
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import duckdb
import yaml

# ── 0) ตั้ง path + env ให้ครบ "ก่อน" import โมดูลที่อ่าน env ตอน import ──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORK = Path(tempfile.mkdtemp(prefix="pda-demo-"))
STAGING_DB = WORK / "staging.duckdb"
CHECKS_DIR = WORK / "checks"
CHECKS_DIR.mkdir()
MANIFEST = WORK / "manifest.json"

ISSUE_ID = "null-userid-042"

os.environ["PIPELINE_TARGET_ENV"] = "staging"        # กฎเหล็ก #3: staging เท่านั้น
os.environ["PIPELINE_DUCKDB"] = str(STAGING_DB)      # ที่ runner เขียน output
os.environ["CHECKS_DIR"] = str(CHECKS_DIR)           # ที่ validation หา check spec
os.environ["DBT_MANIFEST_PATH"] = str(MANIFEST)      # ที่ lineage นับ blast radius


def build_staging() -> None:
    """สร้าง source table `raw_sales` บน staging — มี null user_id + แถวซ้ำปนมา"""
    con = duckdb.connect(str(STAGING_DB))
    con.execute("CREATE TABLE raw_sales (user_id INTEGER, amount DOUBLE)")
    con.executemany(
        "INSERT INTO raw_sales VALUES (?, ?)",
        [(1, 10.0), (1, 5.0), (2, 20.0), (3, 30.0), (None, 99.0)],  # NULL = ตัวปัญหา
    )
    con.close()


def write_manifest() -> None:
    """dbt manifest จำลอง: daily_sales มี downstream 1 ตัว (weekly_rollup) → blast เล็ก"""
    manifest = {
        "nodes": {
            "model.shop.daily_sales": {
                "original_file_path": "models/sales/daily_sales.sql",
                "resource_type": "model",
            },
            "model.shop.weekly_rollup": {
                "original_file_path": "models/sales/weekly_rollup.sql",
                "resource_type": "model",
            },
        },
        "child_map": {
            "model.shop.daily_sales": ["model.shop.weekly_rollup", "test.shop.not_null_user"],
            "model.shop.weekly_rollup": [],
            "test.shop.not_null_user": [],   # test ไม่นับเป็น blast
        },
    }
    MANIFEST.write_text(json.dumps(manifest))


def write_check_config() -> None:
    """check spec ต่อ issue — หลัง fix กรอง null แล้ว เหลือ user 1/2/3 = 3 แถว"""
    cfg = {
        "database": str(STAGING_DB),
        "target_table": "daily_sales",
        "key_columns": ["user_id"],          # ห้าม null
        "sum_columns": ["amount"],
        "baseline_row_count": 3,             # คาดว่าได้ 3 user หลังกรอง null
        "row_count_tolerance": 0.10,
    }
    (CHECKS_DIR / f"{ISSUE_ID}.yaml").write_text(yaml.safe_dump(cfg))


# ── fake LLM: ตอบแบบ deterministic ตาม prompt (ไม่ยิง network) ──
FIXED_SQL = (
    "CREATE OR REPLACE TABLE daily_sales AS\n"
    "SELECT user_id, SUM(amount) AS amount\n"
    "FROM raw_sales\n"
    "WHERE user_id IS NOT NULL\n"   # กรองตัวปัญหา + idempotent ด้วย CREATE OR REPLACE
    "GROUP BY user_id"
)


class FakeLLM:
    """แทน ChatAnthropic ตอน demo — analyze/fix เรียกผ่าน get_llm()"""

    def invoke(self, prompt: str):
        if "แก้โค้ดนี้" in prompt:                       # นี่คือ prompt ของ propose_fix
            return SimpleNamespace(content=FIXED_SQL)
        return SimpleNamespace(                          # ไม่งั้นคือ analyze
            content="user_id มี null ใน source ทำให้ groupby/agg พัง — "
                    "ต้องกรอง null และเขียน output แบบ idempotent"
        )


def main() -> None:
    build_staging()
    write_check_config()
    write_manifest()

    # inject fake LLM "ก่อน" invoke (get_llm อ่าน override ตอนเรียก)
    from integrations.llm import set_llm
    set_llm(FakeLLM())

    from graph import build_app
    from state import new_state

    app = build_app()
    initial = new_state(
        issue_id=ISSUE_ID,
        error_log="ValueError: null user_id ใน daily_sales aggregation",
        pipeline_code="INSERT INTO daily_sales SELECT user_id, SUM(amount) "
                      "FROM raw_sales GROUP BY user_id",
        file_path="models/sales/daily_sales.sql",
        schema_info="raw_sales(user_id, amount)",
        runbook="เคยเจอ null user_id จาก upstream — กรองออกก่อน aggregate",  # → มี runbook
    )

    print(f"\n📂 staging: {STAGING_DB}")
    print(f"📂 checks : {CHECKS_DIR}/{ISSUE_ID}.yaml\n")

    result = app.invoke(initial, config={"configurable": {"thread_id": ISSUE_ID}})

    print(f"\n🏁 สถานะสุดท้าย: {result['status']} "
          f"(tier={result.get('tier')}, ลองไป {result['attempts']} รอบ)")
    print(f"   test     : {result.get('test_result')}")
    print(f"   semantic : {result.get('semantic_result')}")
    if result.get("pr_url"):
        print(f"   PR       : {result['pr_url']}")

    # ยืนยันด้วยตาว่า output ใน staging ถูกต้องจริง
    con = duckdb.connect(str(STAGING_DB), read_only=True)
    rows = con.execute(
        "SELECT user_id, amount FROM daily_sales ORDER BY user_id"
    ).fetchall()
    con.close()
    print(f"\n🔎 daily_sales หลัง fix: {rows}")

    ok = (
        result["status"] == "auto_merged"
        and result["semantic_result"].startswith("PASS")
        and all(r[0] is not None for r in rows)
    )
    print("\n✅ DEMO ผ่าน" if ok else "\n❌ DEMO ไม่ผ่าน")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
