"""
airflow/dags/example_pipeline_dag.py — ตัวอย่าง "pipeline จริง" ที่ต่อ debug agent
================================================================================
DAG นี้แทน data pipeline ปกติของทีม จุดสำคัญคือการ wire `on_failure_callback`:

  PythonOperator(
      ...,
      on_failure_callback=on_pipeline_failure,   # ← พอ task นี้ fail agent จะถูกทริกเกอร์
      params={
          "fix_file_path": "models/sales/daily_sales.sql",  # ไฟล์ที่อนุญาตให้ agent แก้
          "schema_info": "...",                              # hint schema ให้ agent
      },
  )

agent จะ "ไม่" รันคาบน worker ตัวนี้ — callback แค่เก็บ context แล้ว trigger
debug_agent DAG แยกตัวไปรันบน staging (ดู integrations/airflow_trigger.py)

หมายเหตุ: task นี้จงใจ fail เพื่อสาธิต flow — ใน pipeline จริงแทนด้วย logic จริง
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from airflow.operators.python import PythonOperator

from airflow import DAG

_AGENT_HOME = os.environ.get(
    "PIPELINE_AGENT_HOME", str(Path(__file__).resolve().parents[2])
)
if _AGENT_HOME not in sys.path:
    sys.path.insert(0, _AGENT_HOME)

from integrations.airflow_trigger import on_pipeline_failure  # noqa: E402


def _build_daily_sales(**_context) -> None:
    """สาธิต: pipeline ที่พังเพราะ null user_id ปนเข้ามาใน aggregation

    ใน pipeline จริงนี่คือ logic รัน dbt / SQL / transform — ตรงนี้แค่ raise
    ให้เห็น flow ของ on_failure_callback
    """
    raise ValueError(
        "null user_id ใน daily_sales aggregation "
        "(upstream raw_sales มีแถว user_id เป็น NULL)"
    )


with DAG(
    dag_id="example_sales_pipeline",
    description="ตัวอย่าง pipeline ที่ต่อ debug agent ผ่าน on_failure_callback",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["example", "sales"],
) as dag:
    PythonOperator(
        task_id="build_daily_sales",
        python_callable=_build_daily_sales,
        on_failure_callback=on_pipeline_failure,
        params={
            # known issue ที่มี runbook อยู่แล้ว (runbooks/null-userid-042.md)
            # → tiering ได้ runbook=True, lookup check spec เจอ
            "issue_id": "null-userid-042",
            # ไฟล์เดียวที่อนุญาตให้ agent เสนอแก้ (agent ไม่เดาเอง)
            "fix_file_path": "models/sales/daily_sales.sql",
            "schema_info": (
                "warehouse: Trino + Apache Iceberg (catalog=iceberg, schema=staging)\n"
                "raw_sales(user_id INTEGER, amount DOUBLE) — มีแถว user_id NULL ปนมา\n"
                "target table: daily_sales(user_id, amount)"
            ),
        },
    )
