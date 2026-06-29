"""
dashboard_backends.py — เตรียม backend ให้ dashboard เรียกรัน agent
====================================================================
แยก logic ของ 2 โหมดออกจาก UI (dashboard.py) — โมดูลนี้ไม่ run อะไรตอน import:

  • offline — fake LLM + DuckDB (เหมือน demo/run_demo.py) ไม่ต้องมี key/docker
  • real    — Claude จริง (gateway) + Trino→Iceberg (เหมือน dev/run_real.py) ต้องมี docker + .env

⚠️ integration modules (runner/warehouse/validation/lineage) อ่าน env "ตอน import"
   ครั้งเดียว — พอ dashboard อยู่ process เดียวแล้วสลับโหมด ต้อง sync ค่ากลับเข้า module
   ด้วย _sync_module_env() ทุกครั้งก่อนรัน (ไม่งั้นค่าเก่าค้าง)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

ISSUE_ID = "null-userid-042"          # มี runbook อยู่แล้ว → tiering ได้ runbook=True
FILE_PATH = "models/sales/daily_sales.sql"

# ── scenario ตั้งต้น (แก้ได้ในฟอร์มของ dashboard) ──
DEFAULT_INPUTS: dict[str, str] = {
    "issue_id": ISSUE_ID,
    "error_log": (
        "ValueError: null user_id ใน daily_sales aggregation "
        "(upstream raw_sales มีแถว user_id เป็น NULL)"
    ),
    "pipeline_code": (
        "INSERT INTO daily_sales\n"
        "SELECT user_id, SUM(amount)\n"
        "FROM raw_sales\n"
        "GROUP BY user_id"
    ),
    "file_path": FILE_PATH,
    "schema_info": (
        "raw_sales(user_id INTEGER, amount DOUBLE) — มีแถว user_id NULL ปนมา\n"
        "target table: daily_sales(user_id, amount)\n"
        "เขียน SQL ให้ idempotent (CREATE OR REPLACE TABLE ... AS SELECT) "
        "และต้องมีคอลัมน์ผลรวมชื่อ amount"
    ),
}

# fix สำเร็จรูปของ FakeLLM (offline) — idempotent + กรอง null
_FIXED_SQL = (
    "CREATE OR REPLACE TABLE daily_sales AS\n"
    "SELECT user_id, SUM(amount) AS amount\n"
    "FROM raw_sales\n"
    "WHERE user_id IS NOT NULL\n"
    "GROUP BY user_id"
)


class FakeLLM:
    """LLM offline — ตอบ deterministic ตาม prompt (ไม่ยิง network, ไม่ต้องมี key)"""

    def invoke(self, prompt: str):
        if "แก้โค้ดนี้" in prompt:                       # prompt ของ propose_fix
            return SimpleNamespace(content=_FIXED_SQL)
        return SimpleNamespace(
            content="user_id มี null ใน source ทำให้ groupby/agg พัง — "
                    "ต้องกรอง null และเขียน output แบบ idempotent"
        )


def _strip_code_fences(text: str) -> str:
    """ลอก ```sql ... ``` ที่ chat model ชอบห่อ SQL มาให้ (runner รับ SQL ดิบ)"""
    t = text.strip()
    if not t.startswith("```"):
        return text
    lines = t.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


class FenceStrippingLLM:
    """ห่อ Claude จริง — ลอก code fence + รวม content ที่เป็น list block เป็น str"""

    def __init__(self, inner) -> None:
        self._inner = inner

    def invoke(self, prompt: str):
        content = self._inner.invoke(prompt).content
        if isinstance(content, list):
            content = "".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in content
            )
        return SimpleNamespace(content=_strip_code_fences(content))


def _build_real_llm():
    """สร้าง Claude จริงจาก .env — ใช้ชื่อ CODESMART_* กันชน proxy ของ Claude Code
    (ANTHROPIC_BASE_URL ในเครื่อง dev มักเป็น proxy ของ Claude Code อยู่แล้ว)"""
    from langchain_anthropic import ChatAnthropic

    from config.goal import MAX_TOKENS

    model = os.environ.get("API_MODEL") or os.environ.get("AGENT_MODEL") or "claude-sonnet-4-6"
    api_key = os.environ.get("CODESMART_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    base_url = os.environ.get("CODESMART_BASE_URL")
    kwargs = {"model": model, "max_tokens": MAX_TOKENS, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return ChatAnthropic(**kwargs), model, base_url


def _sync_module_env() -> None:
    """re-read env เข้า integration modules ที่ freeze ค่าไว้ตอน import (จำเป็นเมื่อสลับโหมด)"""
    import integrations.lineage as lineage
    import integrations.runner as runner
    import integrations.validation as validation
    import integrations.warehouse as warehouse

    runner.TARGET_ENV = os.environ.get("PIPELINE_TARGET_ENV", "staging")
    runner.RUNNER = os.environ.get("RUNNER", "duckdb")
    runner.PIPELINE_DUCKDB = os.environ.get("PIPELINE_DUCKDB", "")
    runner.TRINO_CATALOG = os.environ.get("TRINO_CATALOG", "iceberg")
    runner.TRINO_SCHEMA = os.environ.get("TRINO_SCHEMA", "staging")
    warehouse.TARGET_ENV = runner.TARGET_ENV
    warehouse.WAREHOUSE = os.environ.get("WAREHOUSE", "iceberg")
    validation.TARGET_ENV = runner.TARGET_ENV
    validation.CHECKS_DIR = os.environ.get("CHECKS_DIR", "config/checks")
    lineage.DBT_MANIFEST_PATH = os.environ.get("DBT_MANIFEST_PATH", "target/manifest.json")


def _write_fixtures(checks_dir: Path, manifest: Path, iceberg_table: str,
                    catalog: str | None) -> None:
    """check spec (semantic) + dbt manifest (blast เล็ก) — เหมือนทั้ง demo และ run_real"""
    cfg = {
        "warehouse": "iceberg",
        "iceberg_table": iceberg_table,
        "key_columns": ["user_id"],
        "sum_columns": ["amount"],
        "baseline_row_count": 3,
        "row_count_tolerance": 0.10,
    }
    if catalog:
        cfg["catalog"] = catalog
    (checks_dir / f"{ISSUE_ID}.yaml").write_text(yaml.safe_dump(cfg))
    manifest.write_text(json.dumps({
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
            "test.shop.not_null_user": [],
        },
    }))


def _duckdb_catalog(staging_db: Path):
    """fake Iceberg catalog (offline) — อ่าน output ที่ runner เขียนลง duckdb → Arrow"""
    import duckdb

    class _Scan:
        def __init__(self, ident): self._ident = ident
        def to_arrow(self):
            con = duckdb.connect(str(staging_db), read_only=True)
            try:
                return con.execute(f'SELECT * FROM "{self._ident}"').to_arrow_table()
            finally:
                con.close()

    class _Table:
        def __init__(self, ident): self._ident = ident
        def scan(self): return _Scan(self._ident)

    class _Catalog:
        def load_table(self, identifier): return _Table(identifier)

    return _Catalog()


# ── prepare: ตั้ง env + fixtures + LLM + source data ให้พร้อมรัน 1 รอบ ──

def prepare_offline(work_dir: Path) -> dict:
    """โหมด offline — fake LLM + DuckDB. คืน {inputs, read_output, info}"""
    staging_db = work_dir / "staging.duckdb"
    checks = work_dir / "checks"
    checks.mkdir(exist_ok=True)
    manifest = work_dir / "manifest.json"

    os.environ.update({
        "PIPELINE_TARGET_ENV": "staging",
        "RUNNER": "duckdb",
        "PIPELINE_DUCKDB": str(staging_db),
        "WAREHOUSE": "iceberg",
        "CHECKS_DIR": str(checks),
        "DBT_MANIFEST_PATH": str(manifest),
    })
    _sync_module_env()

    # source raw_sales (มี null user_id) — สร้างใหม่ทุกรอบ + ล้าง output เดิม
    import duckdb
    con = duckdb.connect(str(staging_db))
    con.execute("DROP TABLE IF EXISTS daily_sales")
    con.execute(
        "CREATE OR REPLACE TABLE raw_sales AS SELECT * FROM (VALUES "
        "(1,10.0),(1,5.0),(2,20.0),(3,30.0),(CAST(NULL AS INTEGER),99.0)) "
        "AS t(user_id, amount)"
    )
    con.close()

    _write_fixtures(checks, manifest, iceberg_table="daily_sales", catalog=None)

    from integrations.llm import set_llm
    set_llm(FakeLLM())
    import integrations.warehouse as warehouse
    warehouse._load_iceberg_catalog = lambda name: _duckdb_catalog(staging_db)

    def read_output():
        con = duckdb.connect(str(staging_db), read_only=True)
        try:
            rows = con.execute(
                "SELECT user_id, amount FROM daily_sales ORDER BY user_id"
            ).fetchall()
        except Exception:
            rows = []
        finally:
            con.close()
        return rows

    return {
        "inputs": dict(DEFAULT_INPUTS),
        "read_output": read_output,
        "info": "fake LLM + DuckDB (offline) — ไม่ต้องมี key/docker",
    }


def prepare_real(work_dir: Path) -> dict:
    """โหมด real — Claude จริง (gateway) + Trino→Iceberg. ต้องมี docker stack + .env"""
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("PYICEBERG_HOME", str(ROOT / "dev" / "iceberg"))
    os.environ.update({
        "PIPELINE_TARGET_ENV": os.environ.get("PIPELINE_TARGET_ENV", "staging"),
        "RUNNER": "trino",
        "WAREHOUSE": "iceberg",
        "TRINO_CATALOG": os.environ.get("TRINO_CATALOG", "iceberg"),
        "TRINO_SCHEMA": os.environ.get("TRINO_SCHEMA", "staging"),
    })
    checks = work_dir / "checks"
    checks.mkdir(exist_ok=True)
    manifest = work_dir / "manifest.json"
    os.environ["CHECKS_DIR"] = str(checks)
    os.environ["DBT_MANIFEST_PATH"] = str(manifest)
    _sync_module_env()

    if not (os.environ.get("CODESMART_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        raise RuntimeError("ไม่มี CODESMART_API_KEY ใน .env — เติมค่าก่อนรันโหมด real")

    # คืน _load_iceberg_catalog เป็นตัวจริง (เผื่อโหมด offline เคย monkeypatch ทับไว้)
    from pyiceberg.catalog import load_catalog

    import integrations.warehouse as warehouse
    warehouse._load_iceberg_catalog = lambda name: load_catalog(name)

    # setup source raw_sales บน Iceberg จริง (Trino) + ล้าง daily_sales เดิม
    from integrations.runner import execute_pipeline
    execute_pipeline("CREATE SCHEMA IF NOT EXISTS staging")
    execute_pipeline("DROP TABLE IF EXISTS staging.daily_sales")
    execute_pipeline(
        "CREATE OR REPLACE TABLE staging.raw_sales AS SELECT * FROM (VALUES "
        "(1,10.0),(1,5.0),(2,20.0),(3,30.0),(CAST(NULL AS INTEGER),99.0)) "
        "AS t(user_id, amount)"
    )

    _write_fixtures(checks, manifest, iceberg_table="staging.daily_sales", catalog="local")

    from integrations.llm import set_llm
    llm, model, base_url = _build_real_llm()
    set_llm(FenceStrippingLLM(llm))

    def read_output():
        from integrations.warehouse import get_adapter
        try:
            stats = get_adapter("iceberg").fetch_stats({
                "iceberg_table": "staging.daily_sales", "catalog": "local",
                "key_columns": ["user_id"], "sum_columns": ["amount"],
            })
            return [(None, stats.row_count, stats.null_counts["user_id"],
                     stats.column_sums["amount"])]
        except Exception:
            return []

    return {
        "inputs": dict(DEFAULT_INPUTS),
        "read_output": read_output,
        "info": f"Claude จริง: {model}" + (f" @ {base_url}" if base_url else ""),
    }


def prepare(mode: str, work_dir: Path) -> dict:
    """เลือก backend ตามโหมด — เรียกก่อน build_app()/invoke ทุกครั้ง"""
    if mode == "real":
        return prepare_real(work_dir)
    return prepare_offline(work_dir)
