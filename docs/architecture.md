# Architecture — Pipeline Debugging Agent

เอกสารนี้สรุป **เทคโนโลยีที่เลือกใช้แยกตามชั้น** + เหตุผลเบื้องหลัง
สำหรับภาพรวมกฎเหล็ก / 7-step framework / tiering ดู [`../CLAUDE.md`](../CLAUDE.md)

---

## สถาปัตยกรรม 2 ชั้น

```
┌─ Inner loop — agent ทำเอง อัตโนมัติ บน test/staging ──┐
│   Action → Check → Fix → Repeat                        │
│   LangGraph + Trino/Iceberg/DuckDB + Claude            │
│   เร็ว วนหลายรอบได้ ปลอดภัยเพราะไม่แตะ prod              │
└───────────────────────────┬───────────────────────────┘
                            │ เมื่อ agent คิดว่าแก้สำเร็จ
                            ▼
        ┌─ Outer gate — คน + GitOps สำหรับ production ─┐
        │   Review → PR → CI → merge → deploy           │
        │   Git/PR + Tiering + Airflow trigger          │
        │   ช้ากว่าโดยตั้งใจ มี discipline ของ version    │
        └───────────────────────────────────────────────┘
```

หลักคิด: **ห้าม inner loop ทะลุไปแตะ production** — agent วนเร็วได้แค่ในกรอบ test เท่านั้น
การออก production ต้องผ่าน Git/PR + คน (หรือ T1 auto-merge เมื่อ CI เขียว) เสมอ

---

## Technology Choices per Layer

| Layer | เลือกใช้ | ทำไม / หมายเหตุ | อยู่ที่ |
|------|---------|----------------|--------|
| **Orchestration (loop)** | **LangGraph** (core MIT, self-host) | StateGraph + conditional edges คุม Action→Check→Fix→Repeat ตรงกับ 7-step; ไม่ผูก LangGraph Platform | `graph.py` |
| **LLM / reasoning** | **Claude** ผ่าน `langchain-anthropic` | ต่อผ่าน **gateway ภายใน (SCB TechX codesmart)** ด้วย `CODESMART_*` + `base_url`; มี **injection seam** (`set_llm`/`get_llm`) สลับเป็น FakeLLM/อื่นได้ | `integrations/llm.py` |
| **State / Context** | **TypedDict (`DebugState`)** | Context layer — error log + code + schema + lineage + runbook ส่งต่อทุก node; node return dict เฉพาะ field ที่อัปเดต | `state.py` |
| **Checkpointer (memory)** | **MemorySaver** (dev) → **Postgres** (prod) | เก็บ state ต่อ thread, `get_state_history()` ย้อน debug ได้ | `graph.py` |
| **Lineage / blast radius** | **dbt `manifest.json`** (`child_map`) | คำนวณ downstream เพื่อประเมิน blast radius ใน tiering | `integrations/lineage.py` |
| **Check / validation** | **semantic validation (fail-closed)** | non_empty + no_nulls_in_keys + row_count_within — "ผ่าน" = test + ข้อมูลถูก semantic ไม่ใช่แค่ไม่ error; รันบน **staging เท่านั้น** | `integrations/validation.py`, `nodes/test.py` |
| **Data execution (runner)** | **Trino** (write) + **PyIceberg/PyArrow** (read) | เขียน fix ลง Iceberg จริงผ่าน Trino, อ่าน output กลับมาตรวจด้วย PyIceberg; สลับด้วย env `RUNNER` | `integrations/runner.py`, `integrations/warehouse.py` |
| **Warehouse / storage** | **Apache Iceberg** (REST catalog + MinIO/S3) | local stack ผ่าน docker (Trino :8080, REST :8181, MinIO :9000) | `dev/iceberg/docker-compose.yml` |
| **Offline harness** | **DuckDB** | รันลูปครบวงโดยไม่ต้องมี key/docker (demo/test) — *ไม่ใช่* warehouse จริง | `dashboard_backends.py` (`prepare_offline`) |
| **Review / GitOps** | **Git branch + Pull Request** | agent มีสิทธิ์แค่สร้าง branch + เปิด PR (service account แยก), ไม่ merge เอง ยกเว้น T1 auto | `integrations/git_client.py`, `nodes/submit_pr.py` |
| **Routing / safety** | **Tiering (T1/T2/T3)** | idempotent + runbook + blast เล็ก → route ให้ถูกคน; circuit breaker `MAX_ATTEMPTS` | `nodes/tiering.py`, `config/goal.py` |
| **Auto-trigger / scheduling** | **Apache Airflow** | `on_failure_callback` → trigger **agent DAG แยกตัว** (รัน inner loop บน staging) — แยก agent ออกจาก worker ของ prod pipeline | `integrations/airflow_trigger.py`, `airflow/dags/` |
| **Observability** | **LangFuse** (open source) หรือ LangSmith | trace ทุก node ผ่าน callback handler, group ด้วย `run_id` เพื่อ audit (กฎเหล็ก #6); ปิดเงียบถ้าไม่ตั้ง key | `observability/trace.py` |
| **Dashboard / UI** | **Streamlit** | กดรัน + live monitor ทีละ node + ประวัติ run; เลือก backend (offline/real) ใน UI | `dashboard.py` |
| **Language / runtime** | **Python ≥ 3.10** | main env = **3.14**; แต่ **Airflow ต้องแยก venv 3.12** (airflow ยังไม่รองรับ 3.14) | `pyproject.toml` |
| **Dev tooling** | **ruff** + **pytest** + **uv** + **pre-commit** | lint/test/venv เร็ว; uv ใช้สร้าง venv 3.12 ของ Airflow | `pyproject.toml`, `.pre-commit-config.yaml` |

---

## 7-Step Framework → ชั้นที่เกี่ยวข้อง

| Step | ชั้น / เทคโนโลยี | ไฟล์ / node |
|------|----------------|------------|
| **Goal** | success criteria + `MAX_ATTEMPTS` | `config/goal.py` |
| **Context** | `DebugState` + lineage + runbook | `state.py`, `integrations/lineage.py` |
| **Action** | Claude (LangGraph node) | `nodes/analyze.py`, `nodes/fix.py` |
| **Check** | semantic validation บน Trino/Iceberg (หรือ DuckDB offline) | `nodes/test.py`, `integrations/validation.py` |
| **Fix** | conditional edge `"retry"` | `graph.py` |
| **Repeat** | loop + circuit breaker | `graph.py` |
| **Review** | Git/PR + tiering (+ Airflow trigger) | `nodes/submit_pr.py`, `nodes/tiering.py` |

---

## หลักการเลือกเทคโนโลยี (cross-cutting)

1. **2 ชั้นแยกขาด** — inner loop (เร็ว, staging) ↔ outer gate (Git/PR + คน + Airflow)
   ห้าม inner loop แตะ production
2. **ทุกอย่างมี seam ให้สลับ** — LLM (`set_llm`), runner (`RUNNER` env),
   warehouse (offline DuckDB shim) → รันได้ทั้งแบบ demo (ไม่มี key) และ real (ครบ stack)
3. **open-source / self-host เป็นหลัก** — LangGraph core (MIT), LangFuse, Iceberg, Airflow
   ไม่ผูก managed platform
4. **Audit ได้เสมอ** — ทุก commit/PR แนบ `run_id` + trace link, agent ใช้ service account แยก
   (กฎเหล็ก #6)
5. **fail-closed** — Check เข้มกว่าผ่อน: ถ้า validate ไม่ได้ถือว่า fail ไว้ก่อน

---

## ข้อจำกัด / สิ่งที่ต้องรู้ตอน deploy

- **Python version**: agent หลักรันบน 3.14 ได้ แต่ **Airflow worker ต้องเป็น 3.12**
  (apache-airflow ยังไม่รองรับ 3.14) — แยก venv/อิมเมจ
- **macOS + Airflow scheduler**: `serve_logs` (gunicorn) fork ชนกับ native libs (pyarrow)
  → SIGSEGV; บน dev mac ใช้ `airflow dags test` แทน scheduler ได้
- **gateway env collision**: อย่าใช้ชื่อ `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY`
  เพราะชนกับ proxy ของ Claude Code — ใช้ `CODESMART_*` แล้วส่ง `base_url`/`api_key`
  เข้า `ChatAnthropic(...)` ตรงๆ
