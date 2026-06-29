"""
dev/run_real.py — รัน agent เต็มวงด้วย Claude จริง บน Iceberg stack จริง
======================================================================
ต่างจาก demo/run_demo.py ตรงที่:
  • LLM     → Claude จริง (อ่าน key/base_url/model จาก .env — รองรับ gateway)
  • runner  → RUNNER=trino เขียน fix ลง Iceberg จริง (ไม่ใช่ DuckDB harness)
  • Check   → IcebergAdapter อ่าน output จาก Iceberg จริง (PyIceberg + PyArrow)
  • git/PR  → stub offline (ไม่ตั้ง GITHUB_TOKEN) — แค่พิมพ์ว่าจะเปิด PR

ต้องยก stack ก่อน:
  docker compose -f dev/iceberg/docker-compose.yml up -d
  # เติม ANTHROPIC_API_KEY ใน .env แล้ว:
  python dev/run_real.py

scenario: daily_sales เจอ null user_id → agent วิเคราะห์ → เสนอ fix idempotent
          กรอง null → รันลง Iceberg → ตรวจ semantic → tiering → PR
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import yaml
from dotenv import load_dotenv

# ── 0) path + env ให้ครบ "ก่อน" import โมดูลที่อ่าน env ตอน import ──
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")   # โหลด ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / API_MODEL / RUNNER ฯลฯ

# IcebergAdapter หา catalog "local" จาก dev/iceberg/.pyiceberg.yaml
os.environ.setdefault("PYICEBERG_HOME", str(HERE / "iceberg"))
os.environ.setdefault("PIPELINE_TARGET_ENV", "staging")
os.environ.setdefault("RUNNER", "trino")
os.environ.setdefault("TRINO_CATALOG", "iceberg")
os.environ.setdefault("TRINO_SCHEMA", "staging")

# fixture ชั่วคราว (check spec + dbt manifest) — ไม่ปนกับ config จริงของ repo
WORK = Path(tempfile.mkdtemp(prefix="pda-real-"))
CHECKS_DIR = WORK / "checks"
CHECKS_DIR.mkdir()
MANIFEST = WORK / "manifest.json"
os.environ["CHECKS_DIR"] = str(CHECKS_DIR)
os.environ["DBT_MANIFEST_PATH"] = str(MANIFEST)

ISSUE_ID = "null-userid-042"           # มี runbook อยู่แล้วที่ runbooks/null-userid-042.md
FILE_PATH = "models/sales/daily_sales.sql"

# import "หลัง" ตั้ง env เสร็จ (runner/validation/lineage อ่าน env ตอน import)
from graph import build_app  # noqa: E402
from integrations.llm import set_llm  # noqa: E402
from integrations.runner import execute_pipeline  # noqa: E402
from state import new_state  # noqa: E402


def setup_iceberg() -> None:
    """สร้าง source raw_sales (มี null user_id) บน Iceberg จริง + ล้าง daily_sales เดิม"""
    execute_pipeline("CREATE SCHEMA IF NOT EXISTS staging")
    execute_pipeline("DROP TABLE IF EXISTS staging.daily_sales")   # clean slate
    execute_pipeline(
        "CREATE OR REPLACE TABLE staging.raw_sales AS "
        "SELECT * FROM (VALUES (1,10.0),(1,5.0),(2,20.0),(3,30.0),"
        "(CAST(NULL AS INTEGER),99.0)) AS t(user_id, amount)"
    )
    print("✅ Iceberg: setup staging.raw_sales (มี null user_id, 5 แถว)")


def write_fixtures() -> None:
    """check spec (semantic) + dbt manifest (blast radius เล็ก → tiering เอื้อ T1/T2)"""
    (CHECKS_DIR / f"{ISSUE_ID}.yaml").write_text(yaml.safe_dump({
        "warehouse": "iceberg",
        "iceberg_table": "staging.daily_sales",
        "catalog": "local",
        "key_columns": ["user_id"],          # ห้าม null
        "sum_columns": ["amount"],
        "baseline_row_count": 3,             # user 1/2/3 หลังกรอง null
        "row_count_tolerance": 0.10,
    }))
    MANIFEST.write_text(json.dumps({
        "nodes": {
            "model.shop.daily_sales": {
                "original_file_path": FILE_PATH, "resource_type": "model",
            },
            "model.shop.weekly_rollup": {
                "original_file_path": "models/sales/weekly_rollup.sql",
                "resource_type": "model",
            },
        },
        "child_map": {
            "model.shop.daily_sales": ["model.shop.weekly_rollup", "test.shop.not_null_user"],
            "model.shop.weekly_rollup": [],
            "test.shop.not_null_user": [],    # test ไม่นับเป็น blast
        },
    }))


def _strip_code_fences(text: str) -> str:
    """ลอก ```sql ... ``` ที่ chat model ชอบห่อ SQL มาให้ (runner รับ SQL ดิบ)

    ทำไม: prompt สั่ง 'ตอบเฉพาะโค้ด' แต่โมเดลจริงมักห่อ fence อยู่ดี ถ้าไม่ลอก
    runner จะ execute ' ```sql ' แล้ว syntax error ตั้งแต่รอบแรก
    """
    t = text.strip()
    if not t.startswith("```"):
        return text
    lines = t.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]                     # ตัดบรรทัด ``` หรือ ```sql
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]                    # ตัด ``` ปิดท้าย
    return "\n".join(lines).strip()


class FenceStrippingLLM:
    """ห่อ Claude จริง — ลอก code fence ออกจาก response ก่อนส่งให้ node

    interface ตรงกับที่ analyze/fix เรียก: .invoke(prompt) → obj ที่มี .content
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def invoke(self, prompt: str):
        resp = self._inner.invoke(prompt)
        content = resp.content
        # ChatAnthropic อาจคืน content เป็น list ของ block — รวมเป็น str ก่อน
        if isinstance(content, list):
            content = "".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in content
            )
        return SimpleNamespace(content=_strip_code_fences(content))


def build_llm():
    """สร้าง Claude จริงจากค่าใน .env — รองรับ gateway (base_url + ชื่อ model เฉพาะ)

    ⚠️ ใช้ชื่อ env เฉพาะ (CODESMART_*) ไม่ใช่ ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY
    มาตรฐาน เพราะเครื่อง dev อาจมี ANTHROPIC_BASE_URL ของ proxy Claude Code อยู่แล้ว
    (load_dotenv ไม่ override env เดิม → ชนกัน) จึงส่ง api_key/base_url เข้า ChatAnthropic
    ตรงๆ ให้ชนะค่า env ที่ถูกจับจอง
    """
    from langchain_anthropic import ChatAnthropic

    from config.goal import MAX_TOKENS

    model = os.environ.get("API_MODEL") or os.environ.get("AGENT_MODEL") or "claude-sonnet-4-6"
    api_key = os.environ.get("CODESMART_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    base_url = os.environ.get("CODESMART_BASE_URL")   # gateway — ไม่ fallback ไป ANTHROPIC_BASE_URL
    kwargs = {"model": model, "max_tokens": MAX_TOKENS, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    print(f"🧠 LLM: {model}" + (f" @ {base_url}" if base_url else " (Anthropic default)"))
    return ChatAnthropic(**kwargs)


def main() -> None:
    if not (os.environ.get("CODESMART_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        sys.exit("❌ ไม่มี CODESMART_API_KEY — เติมค่าใน .env ก่อน")

    setup_iceberg()
    write_fixtures()
    set_llm(FenceStrippingLLM(build_llm()))   # inject Claude จริง (กัน fence)

    app = build_app()
    initial = new_state(
        issue_id=ISSUE_ID,
        error_log="ValueError: null user_id ใน daily_sales aggregation "
                  "(upstream raw_sales มีแถว user_id เป็น NULL)",
        pipeline_code=(
            "INSERT INTO daily_sales\n"
            "SELECT user_id, SUM(amount)\n"
            "FROM raw_sales\n"
            "GROUP BY user_id"
        ),
        file_path=FILE_PATH,
        schema_info=(
            "warehouse: Trino + Apache Iceberg (catalog=iceberg, schema=staging)\n"
            "raw_sales(user_id INTEGER, amount DOUBLE) — มีแถว user_id NULL ปนมา\n"
            "target table: daily_sales(user_id, amount)\n"
            "เขียน SQL ให้รันบน Trino ได้ (ใช้ CREATE OR REPLACE TABLE ... AS SELECT) "
            "และต้องมีคอลัมน์ผลรวมชื่อ amount"
        ),
        # ไม่ส่ง runbook → auto-load จาก runbooks/null-userid-042.md
    )

    print(f"\n📂 checks : {CHECKS_DIR}/{ISSUE_ID}.yaml")
    print(f"🆔 run_id : {initial['run_id']}\n")

    result = app.invoke(initial, config={"configurable": {"thread_id": ISSUE_ID}})

    print(f"\n🏁 สถานะสุดท้าย: {result['status']} "
          f"(tier={result.get('tier')}, ลองไป {result['attempts']} รอบ)")
    print(f"   diagnosis: {result.get('diagnosis', '')[:120]}")
    print(f"   test     : {result.get('test_result')}")
    print(f"   semantic : {result.get('semantic_result')}")
    if result.get("pr_url"):
        print(f"   PR       : {result['pr_url']}")

    print("\n── โค้ดที่ agent เสนอ ──")
    print(result.get("proposed_fix", "(ไม่มี)"))

    # ยืนยันด้วยตา: อ่าน daily_sales จาก Iceberg จริงกลับมาดู
    from integrations.warehouse import get_adapter
    if result.get("status") in ("fixed", "auto_merged", "awaiting_review"):
        stats = get_adapter("iceberg").fetch_stats({
            "iceberg_table": "staging.daily_sales", "catalog": "local",
            "key_columns": ["user_id"], "sum_columns": ["amount"],
        })
        print(f"\n🔎 daily_sales บน Iceberg: row_count={stats.row_count}, "
              f"null(user_id)={stats.null_counts['user_id']}, "
              f"sum(amount)={stats.column_sums['amount']}")


if __name__ == "__main__":
    main()
