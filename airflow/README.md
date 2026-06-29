# Airflow integration — auto-trigger debug agent

เมื่อ task ใน pipeline จริง fail → ทริกเกอร์ debug agent ให้รันบน staging อัตโนมัติ
แล้วเปิด PR ให้คนรีวิว (agent ไม่แตะ prod, ไม่ merge เอง ตามกฎเหล็ก)

```
example_sales_pipeline.build_daily_sales   --fail-->  on_pipeline_failure(context)
                                                          │ build_agent_conf()
                                                          ▼ trigger_debug_agent()
                                                   debug_agent DAG  (staging)
                                                          │ app.invoke() inner loop
                                                          ▼
                                                       เปิด PR
```

> สำคัญ: agent **ไม่** รันคาบน worker ของ pipeline จริง — callback แค่ "เก็บ context +
> trigger DAG แยกตัว" เพื่อแยก inner loop (เร็ว, staging) ออกจาก prod ตามสถาปัตยกรรม

## ไฟล์

| ไฟล์ | หน้าที่ |
|------|--------|
| `dags/debug_agent_dag.py` | agent DAG — `schedule=None` (trigger-only), อ่าน `dag_run.conf`, รัน loop, เปิด PR |
| `dags/example_pipeline_dag.py` | ตัวอย่าง pipeline จริงที่ wire `on_failure_callback` |
| `../integrations/airflow_trigger.py` | สะพาน: `on_pipeline_failure` + `build_agent_conf` + `trigger_debug_agent` |

## วิธีต่อกับ pipeline DAG ของคุณ

```python
from integrations.airflow_trigger import on_pipeline_failure

PythonOperator(
    task_id="build_daily_sales",
    python_callable=...,
    on_failure_callback=on_pipeline_failure,
    params={
        "fix_file_path": "models/sales/daily_sales.sql",  # ไฟล์เดียวที่อนุญาตให้ agent แก้
        "schema_info": "raw_sales(user_id INTEGER, amount DOUBLE) ...",
    },
)
```

`params` คือทางที่เจ้าของ pipeline บอก agent ว่า "แก้ไฟล์ไหนได้" + ใบ้ schema —
agent ไม่เดาเอง ถ้าไม่ใส่ `fix_file_path` agent จะได้ context น้อยลง (แต่ยังรันได้)

## Deploy (บน Airflow worker)

```bash
pip install -e '.[airflow]'              # apache-airflow (เฉพาะบน worker)
```

env ที่ต้องตั้งบน worker:

| env | ค่า | ทำไม |
|-----|-----|------|
| `PIPELINE_AGENT_HOME` | path ของ repo นี้ (ที่มี `graph.py`) | ให้ worker import โมดูล agent เจอ |
| `PIPELINE_TARGET_ENV` | `staging` | กฎเหล็ก #3 — DAG จะ refuse ถ้าเป็น prod |
| `RUNNER` + `TRINO_*` / `PYICEBERG_HOME` | ตาม stack staging จริง | ที่ agent เขียน fix ลงทดสอบ |
| `CODESMART_API_KEY` (+ `CODESMART_BASE_URL`, `API_MODEL`) | ผ่าน secret backend | LLM (อย่า hardcode ใน DAG) |
| `DEBUG_AGENT_DAG_ID` | (ออปชัน) เปลี่ยนชื่อ agent DAG | ให้ callback กับ DAG ตรงกัน |
| `DEBUG_AGENT_TRIGGER_ENABLED` | `false` เพื่อปิดสวิตช์รวม | kill switch ตอน incident ใหญ่ |

> ถ้าโฟลเดอร์ `dags/` ของ Airflow อยู่คนละที่กับ repo ให้ symlink/คัดลอก 2 DAG เข้าไป
> แล้วตั้ง `PIPELINE_AGENT_HOME` ชี้กลับมาที่ repo
