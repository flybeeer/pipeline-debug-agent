"""
integrations/airflow_trigger.py — สะพานจาก Airflow → debug agent
================================================================
หน้าที่: เมื่อ task ใน "pipeline DAG จริง" fail ให้ "เก็บ context + ทริกเกอร์
agent DAG แยกตัว" ออกไปรันบน staging — ไม่รัน agent loop คาบน worker ของ
prod pipeline (กฎเหล็ก: แยก inner loop ออกจาก prod ให้เด็ดขาด)

flow:
    prod_pipeline_dag.task  --fail-->  on_pipeline_failure(context)
                                          │ build_agent_conf()
                                          ▼ trigger_debug_agent()
                                   debug_agent_dag (staging)  →  เปิด PR

ออกแบบให้:
  • import airflow แบบ lazy (เฉพาะตอนจะ trigger จริง) → โมดูล agent/tests รันได้
    แม้เครื่องนั้นไม่มี airflow ติดตั้ง
  • on_pipeline_failure ห้าม raise เด็ดขาด — callback ที่พังจะกลบ error เดิม
    ของ task ทำให้ debug ยากขึ้น (เรา log แล้วเงียบแทน)
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# DAG id ของ agent ที่จะถูกทริกเกอร์ ( override ได้ด้วย env ตอน deploy)
AGENT_DAG_ID = os.environ.get("DEBUG_AGENT_DAG_ID", "debug_agent")


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_trigger_enabled() -> bool:
    """ปิดสวิตช์รวมได้ด้วย env (เช่นตอน incident ใหญ่ที่ไม่อยากให้ agent วุ่น)

    default = เปิด ถ้าไม่ได้ตั้ง env (DEBUG_AGENT_TRIGGER_ENABLED ไม่มี = เปิด)
    """
    raw = os.environ.get("DEBUG_AGENT_TRIGGER_ENABLED")
    return True if raw is None else _truthy(raw)


def _attr(obj: Any, name: str, default: str = "") -> str:
    """ดึง attribute จาก object ของ airflow (dag/task/ti) แบบไม่พังถ้าไม่มี"""
    return str(getattr(obj, name, default) or default)


def build_agent_conf(context: dict) -> dict:
    """แปลง Airflow task `context` → conf dict สำหรับส่งเข้า agent DAG (dag_run.conf)

    context คือ dict ที่ Airflow ส่งให้ callback (มี dag/task/ti/exception/params ...)
    เราดึงเฉพาะที่ agent ต้องใช้เป็น Context layer แล้วทำให้เป็น dict ล้วน
    (serializable — เพราะต้องเดินทางข้าม process ไปอีก DAG)

    issue_id ตั้งเป็น "<dag_id>.<task_id>" แบบ stable เพื่อให้ runbook/checks
    lookup เจอข้ามรอบ (ปัญหาเดิมที่ task เดิม = เคสเดียวกัน)
    """
    dag = context.get("dag")
    task = context.get("task")
    ti = context.get("task_instance") or context.get("ti")
    params = context.get("params") or {}

    dag_id = _attr(dag, "dag_id") or str(context.get("dag_id", "")) or "unknown_dag"
    task_id = _attr(task, "task_id") or str(context.get("task_id", "")) or "unknown_task"
    run_id = str(context.get("run_id", "") or _attr(ti, "run_id"))
    log_url = _attr(ti, "log_url")
    exception = context.get("exception")

    # default issue_id = "<dag>.<task>" แต่ pipeline ประกาศ known issue ที่มี runbook
    # ได้เองผ่าน params["issue_id"] (เช่น "null-userid-042") เพื่อให้ runbook/checks lookup เจอ
    issue_id = str(params.get("issue_id") or f"{dag_id}.{task_id}")

    # ── error_log: exception จริง + ลิงก์ log ของ Airflow (ให้คน/agent ตามต่อได้) ──
    error_lines = []
    if exception is not None:
        error_lines.append(f"{type(exception).__name__}: {exception}")
    if log_url:
        error_lines.append(f"Airflow log: {log_url}")
    error_log = "\n".join(error_lines) or "Airflow task failed (ไม่มีรายละเอียด exception)"

    # ── โค้ดที่ agent ได้รับสิทธิ์แตะ: pipeline author ต้องประกาศ fix_file_path เอง ──
    #    (ไม่เดาเอง — agent แก้ได้เฉพาะไฟล์ที่เจ้าของ pipeline อนุญาตไว้ใน params)
    file_path = str(params.get("fix_file_path") or "")
    pipeline_code = str(params.get("pipeline_code") or "")
    if not pipeline_code and file_path:
        p = Path(file_path)
        if p.is_file():
            pipeline_code = p.read_text()

    return {
        "issue_id": issue_id,
        "error_log": error_log,
        "pipeline_code": pipeline_code,
        "file_path": file_path,
        "schema_info": str(params.get("schema_info") or ""),
        # metadata เพื่อ audit ว่ามาจาก airflow ตัวไหน (กฎเหล็ก #6)
        "source": "airflow",
        "airflow": {
            "dag_id": dag_id,
            "task_id": task_id,
            "run_id": run_id,
            "log_url": log_url,
        },
    }


def trigger_debug_agent(conf: dict, *, agent_dag_id: str | None = None) -> str | None:
    """ทริกเกอร์ agent DAG ด้วย conf ที่เตรียมไว้ — คืน run_id ที่สร้าง (None ถ้าไม่ได้ trig)

    lazy-import airflow ตรงนี้: เครื่องที่ไม่มี airflow (เช่น CI ของ agent เอง)
    ก็ยัง import โมดูลนี้ได้ตามปกติ
    """
    dag_id = agent_dag_id or AGENT_DAG_ID
    # run_id ไม่ซ้ำต่อการ trig แต่ละครั้ง (ปัญหาเดิม trig ซ้ำได้หลายรอบ)
    trigger_run_id = f"agent__{conf.get('issue_id', 'unknown')}__{uuid.uuid4().hex[:8]}"

    from airflow.api.common.trigger_dag import trigger_dag

    trigger_dag(
        dag_id=dag_id,
        run_id=trigger_run_id,
        conf=conf,
        replace_microseconds=False,
    )
    log.info("triggered debug agent DAG '%s' run_id=%s", dag_id, trigger_run_id)
    return trigger_run_id


def on_pipeline_failure(context: dict) -> None:
    """on_failure_callback สำหรับแปะที่ task ของ pipeline DAG จริง

    ใช้:  PythonOperator(..., on_failure_callback=on_pipeline_failure,
                         params={"fix_file_path": "...", "schema_info": "..."})

    ⚠️ ฟังก์ชันนี้ห้าม raise — ถ้าพังต้องเงียบ (log) เพื่อไม่ให้กลบ error เดิมของ task
    """
    try:
        if not _is_trigger_enabled():
            log.info("debug agent trigger ปิดอยู่ (DEBUG_AGENT_TRIGGER_ENABLED) — ข้าม")
            return
        conf = build_agent_conf(context)
        run_id = trigger_debug_agent(conf)
        log.info("debug agent ถูกทริกเกอร์สำหรับ issue=%s run=%s",
                 conf.get("issue_id"), run_id)
    except Exception as e:   # noqa: BLE001 — ตั้งใจกลืนทุก error เพื่อไม่ให้กระทบ task เดิม
        log.warning("ทริกเกอร์ debug agent ไม่สำเร็จ (ข้าม ไม่กระทบ task เดิม): %s", e)
