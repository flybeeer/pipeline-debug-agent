"""
airflow/dags/debug_agent_dag.py — agent DAG (ถูกทริกเกอร์ ไม่ schedule เอง)
=========================================================================
DAG นี้คือ "ตัวรัน inner loop" ที่แยกออกมาจาก pipeline จริง:
  • schedule=None → รันเฉพาะตอนถูก trigger (จาก on_pipeline_failure ของ pipeline DAG)
  • อ่านปัญหาจาก dag_run.conf (issue_id / error_log / file_path / schema ...)
  • รัน app.invoke() วน analyze→fix→test→tiering→PR แล้วจบ
  • เปิด PR ให้คนรีวิว (agent ไม่ merge เอง ยกเว้น T1 auto)

🚨 กฎเหล็ก #3: DAG นี้ต้องรันบน staging เท่านั้น — บังคับ PIPELINE_TARGET_ENV
   และ refuse ทันทีถ้าใครเผลอชี้ไป production

deploy: วาง repo ของ agent ให้ worker import ได้ แล้วตั้ง env:
  PIPELINE_AGENT_HOME=/path/to/pipeline-debug-agent   (root ที่มี graph.py/state.py)
  PIPELINE_TARGET_ENV=staging
  RUNNER=trino  (+ TRINO_* / PYICEBERG_HOME ตาม stack staging จริง)
  CODESMART_API_KEY=...  (ผ่าน Airflow Variable/Connection หรือ secret backend)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow.operators.python import PythonOperator

from airflow import DAG

# ── ทำให้ worker import โมดูลของ agent (graph/state/...) เจอ ──
# default: เดินขึ้นจาก airflow/dags/ ไป repo root; deploy จริง override ด้วย env
_AGENT_HOME = os.environ.get(
    "PIPELINE_AGENT_HOME", str(Path(__file__).resolve().parents[2])
)
if _AGENT_HOME not in sys.path:
    sys.path.insert(0, _AGENT_HOME)


def _run_agent(**context) -> dict:
    """อ่าน conf → สร้าง state → รัน loop → push ผลเข้า XCom

    import โมดูล agent "ข้างใน" function (ไม่ใช่ top-level) เพื่อให้ตอน Airflow
    parse DAG ไม่ต้องโหลด langgraph/llm ทั้งก้อน (parse เร็ว + แยก failure)
    """
    # กฎเหล็ก #3: บังคับ staging — ถ้าชี้ prod ให้ตายตั้งแต่ก่อนแตะอะไร
    target_env = os.environ.setdefault("PIPELINE_TARGET_ENV", "staging")
    if target_env.lower() in {"prod", "production"}:
        raise RuntimeError(
            "debug_agent_dag ห้ามรันบน production (กฎเหล็ก #3) — "
            "ตั้ง PIPELINE_TARGET_ENV=staging"
        )

    dag_run = context.get("dag_run")
    conf = dict(getattr(dag_run, "conf", None) or {})
    issue_id = conf.get("issue_id") or "manual-trigger"

    # backend: "real" (default, gateway+Trino) หรือ "offline" (FakeLLM+DuckDB, สำหรับ demo/local)
    # offline ใช้ bootstrap เดียวกับ dashboard (เทสแล้ว) — inject LLM/warehouse + fixtures ให้พร้อม
    backend = (conf.get("backend") or os.environ.get("DEBUG_AGENT_BACKEND") or "real").lower()
    read_output = None
    if backend == "offline":
        import tempfile

        import dashboard_backends as backends
        prep = backends.prepare("offline", Path(tempfile.mkdtemp(prefix="pda-airflow-")))
        read_output = prep["read_output"]
        print(f"⚙️  backend=offline ({prep['info']})")

    from graph import build_app
    from observability.trace import build_invoke_config
    from state import new_state

    initial = new_state(
        issue_id=issue_id,
        error_log=conf.get("error_log", ""),
        pipeline_code=conf.get("pipeline_code", ""),
        file_path=conf.get("file_path", ""),
        schema_info=conf.get("schema_info", ""),
        # ไม่ส่ง runbook → auto-load จาก runbooks/<issue_id>.md ถ้ามี
    )

    invoke_config = build_invoke_config(
        thread_id=issue_id, run_id=initial["run_id"], issue_id=issue_id
    )
    # build_app() คืน graph ที่ compile พร้อม checkpointer (เหมือน CLI/dashboard)
    result = build_app().invoke(initial, config=invoke_config)

    summary = {
        "status": result.get("status"),
        "tier": result.get("tier"),
        "attempts": result.get("attempts"),
        "run_id": result.get("run_id"),
        "pr_url": result.get("pr_url", ""),
        "semantic_result": result.get("semantic_result", ""),
        "source_airflow": conf.get("airflow", {}),
    }
    print(
        f"🏁 agent จบ: status={summary['status']} tier={summary['tier']} "
        f"attempts={summary['attempts']} pr={summary['pr_url'] or '(none)'}"
    )
    if read_output is not None:
        print(f"🔎 output หลัง fix: {read_output()}")
    # ดันขึ้น XCom เพื่อให้ task ถัดไป / UI เห็นผล (และ alert ได้)
    return summary


default_args = {
    "owner": "debug-agent",
    # circuit breaker ชั้น DAG: ห้าม retry agent ทั้งดุ้น — MAX_ATTEMPTS คุม inner loop พอ
    # (retry ที่นี่ = เผา token ซ้ำโดยเปล่าประโยชน์ กฎเหล็ก #7)
    "retries": 0,
    "execution_timeout": timedelta(minutes=20),
}

with DAG(
    dag_id=os.environ.get("DEBUG_AGENT_DAG_ID", "debug_agent"),
    description="AI debug agent — ถูกทริกเกอร์เมื่อ pipeline fail, รันบน staging, เปิด PR",
    default_args=default_args,
    schedule=None,            # trigger-only (ไม่มี schedule)
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=3,        # กันรัน agent ถล่มพร้อมกันตอน fail เป็นพวง
    tags=["ai", "debug-agent", "staging"],
) as dag:
    PythonOperator(
        task_id="run_agent",
        python_callable=_run_agent,
    )
