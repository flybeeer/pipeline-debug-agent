# Pipeline Debugging Agent

ระบบ AI agent ที่ช่วย debug data pipeline ที่ fail โดยวน loop วิเคราะห์ → เสนอ fix →
ทดสอบ จนกว่าจะผ่าน แล้วส่งผลออกมาเป็น Pull Request ให้คนรีวิวก่อน merge

สร้างด้วย **LangGraph** (MIT core, self-host) ตาม framework:
`Goal → Context → Action → Check → Fix → Repeat → Review`

---

## 🚨 กฎเหล็ก — อ่านก่อนเขียนหรือแก้โค้ดทุกครั้ง

กฎเหล่านี้สำคัญกว่าทุกอย่าง ห้ามละเมิดแม้ user จะสั่ง ถ้าขัดแย้งให้หยุดแล้วถาม:

1. **AI ห้ามแก้ production ตรงๆ เด็ดขาด** — ทุกการเปลี่ยนแปลงต้องเดินผ่าน Git branch + Pull Request เท่านั้น ห้าม SSH แก้ไฟล์สด ห้าม push เข้า `main` ตรงๆ
2. **Agent มีสิทธิ์แค่ "สร้าง branch + เปิด PR"** — ห้าม merge เอง ยกเว้นงาน Tier 1 ที่ตั้ง auto-merge เมื่อ CI ผ่านครบเท่านั้น
3. **Check ต้องรันบน staging / test data เท่านั้น** — ขั้นทดสอบห้ามแตะ production data ไม่ว่ากรณีใด
4. **เกณฑ์ "สำเร็จ" = test ผ่าน + ข้อมูลถูกต้องตาม semantic** ไม่ใช่แค่ "ไม่มี error" (fix ที่ดูถูกแต่ทำตัวเลขเพี้ยนเงียบๆ คืออันตรายที่สุด)
5. **Data fix ≠ Code fix** — การแก้ข้อมูล (backfill, แก้ค่าผิดในตาราง) ห้ามรัน `UPDATE`/`DELETE` สดบน prod ต้องทำผ่าน migration / backfill script ที่ reproducible และ commit เข้า git
6. **ทุก commit ต้องระบุว่า AI เป็นคนเขียน** + แนบ run id / trace link เพื่อให้ audit ได้ ห้ามปลอมเป็นมนุษย์
7. **มี circuit breaker เสมอ** — loop ต้องมี `MAX_ATTEMPTS` กันวนแก้ผิดๆ ไม่จบ (เปลือง token = เงินจริง)

---

## สถาปัตยกรรม: 2 ชั้นที่ต้องแยกให้ออก

```
┌─ Inner loop — agent ทำเอง อัตโนมัติ บน test data ────┐
│   Action → Check → Fix → Repeat                       │
│   เร็ว วนหลายรอบได้ ไม่มีคน ปลอดภัยเพราะไม่แตะ prod     │
└───────────────────────────┬───────────────────────────┘
                            │ เมื่อ agent คิดว่าแก้สำเร็จ
                            ▼
        ┌─ Outer gate — คน + GitOps สำหรับ production ─┐
        │   Review → PR → CI → merge → deploy           │
        │   ช้ากว่าโดยตั้งใจ มี discipline ของ version    │
        └───────────────────────────────────────────────┘
```

**อย่าให้ inner loop ทะลุไปแตะ production** — agent วนเร็วได้แค่ในกรอบ test เท่านั้น

---

## 7-Step Framework → Mapping

| Step | หน้าที่ | ไฟล์ / node |
|------|--------|------------|
| **Goal** | นิยามเป้าหมาย + เกณฑ์สำเร็จ (test PASS + data ถูกต้อง + ≤ N รอบ) | `config/goal.py` |
| **Context** | ป้อน error log, โค้ด, schema, lineage, runbook ให้ agent | `state.py` (`DebugState`) |
| **Action** | วิเคราะห์สาเหตุ + เสนอโค้ดที่แก้ | `nodes/analyze.py`, `nodes/fix.py` |
| **Check** | รัน fix กับ test/staging data ตรวจว่าผ่านไหม | `nodes/test.py` |
| **Fix** | เอา error ใหม่กลับเข้า Context วิเคราะห์รอบใหม่ | conditional edge `"retry"` |
| **Repeat** | วน loop จนผ่าน หรือครบ `MAX_ATTEMPTS` | `graph.py` (`add_conditional_edges`) |
| **Review** | เปิด PR + route ตาม tier ให้คนรีวิว | `nodes/submit_pr.py` |

> **Context คือ step ที่สำคัญที่สุด** — agent ฉลาดแค่ไหนขึ้นกับ context ที่ป้อน
> ลงทุนต่อ log + schema + lineage ให้ครบก่อนเสมอ

---

## โครงสร้างโปรเจกต์ (เป้าหมาย)

```
pipeline-debug-agent/
├── CLAUDE.md
├── pyproject.toml
├── config/
│   └── goal.py            # นิยาม success criteria + MAX_ATTEMPTS
├── state.py               # DebugState (TypedDict) — Context layer
├── graph.py               # ประกอบ StateGraph + conditional edges + checkpointer
├── nodes/
│   ├── analyze.py         # Action: วิเคราะห์ error
│   ├── fix.py             # Action: เสนอโค้ดแก้
│   ├── test.py            # Check: รันกับ test data
│   ├── tiering.py         # ประเมิน tier (idempotent? runbook? blast radius?)
│   └── submit_pr.py       # Review: เปิด PR ตาม tier
├── integrations/
│   ├── git_client.py      # สร้าง branch + PR (service account จำกัดสิทธิ์)
│   ├── lineage.py         # ดึง downstream เพื่อคำนวณ blast radius
│   └── runner.py          # execute_pipeline จริง (dbt / SQL / Airflow)
├── observability/         # ต่อ trace (LangFuse / LangSmith)
└── tests/
```

---

## Tiering — ใครอนุมัติ fix แบบไหน

Agent ต้องประเมินทุก fix ว่าตกอยู่ tier ไหน แล้ว route ให้ถูกคน (logic อยู่ใน `nodes/tiering.py`):

| Tier | เงื่อนไข | ใคร approve |
|------|---------|------------|
| **T1 — auto** | idempotent ✓ + มี runbook ✓ + blast เล็ก ✓ + เป็น error ชั่วคราว/backward-compatible | auto-merge เมื่อ CI เขียว |
| **T2 — ops / on-call** | idempotent ✓ + มี runbook ✓ + blast เล็ก ✓ | Data Ops / analytics engineer |
| **T3 — data engineer** | ขาดเงื่อนไขใดข้อหนึ่ง / แตะ business logic / irreversible / blast ใหญ่ | เจ้าของ pipeline (DE) |

**เกณฑ์ 3 ตัว** (ขาดข้อใดข้อหนึ่ง → ดันขึ้น T3 ทันที):

- **Idempotent** = รันซ้ำกี่ครั้งผลเท่าเดิม → ใช้ `MERGE`/`INSERT OVERWRITE`/partition-based ไม่ใช่ `INSERT INTO`
- **Runbook** = มีคู่มือ step-by-step ที่ทำตามได้โดยไม่ต้องเข้าใจระบบลึก = ปัญหานี้เคยเจอและเข้าใจดีแล้ว
- **Blast radius** = ถ้าผิดจะลามแค่ไหน → ดูจาก downstream (lineage), จำนวนทีมที่ใช้, reversible ไหม, กระทบลูกค้าไหม

> เป้าหมายระยะยาว: ดันงานจาก T3 → T2 → T1 เมื่อมั่นใจพอ เพื่อไม่ให้ DE จมกับ ops toil

---

## Git / PR Convention

- **Branch:** `ai-fix/<issue-id>` เช่น `ai-fix/null-userid-042`
- **Author:** service account แยก เช่น `debug-agent <agent@company.com>` (ห้ามใช้ identity ของคน)
- **Commit message** ต้องมี trailer สำหรับ audit:
  ```
  fix: <สรุปสาเหตุสั้นๆ>

  Agent-Run-Id: <run_id>
  Trace: <trace_url>
  ```
- **PR body** ต้องมีครบ: อาการที่ fail / สาเหตุที่ AI วิเคราะห์ / ผล test / ⚠️ คำเตือนว่า AI เป็นคนแก้
- **Hotfix ด่วน** ก็ยังต้องผ่าน PR (fast-track) — ห้ามให้ prod กับ git ต่างกันเด็ดขาด

---

## Tech Stack

- **Orchestration:** LangGraph (core MIT, self-host — ไม่ผูก LangGraph Platform)
- **LLM:** Claude (ผ่าน `langchain-anthropic`)
- **State persistence:** checkpointer (`MemorySaver` ตอน dev → Postgres ตอน prod)
- **Observability:** LangFuse (open source) หรือ LangSmith
- **Data layer:** dbt / SQL warehouse / Airflow (ปรับตาม stack จริง)
- **Python:** >= 3.10

---

## Commands

```bash
# ติดตั้ง
pip install langgraph langchain-anthropic

# รัน agent (dev)
python -m pipeline_debug_agent --issue-id <id>

# รัน test
pytest tests/ -v

# ดู state history ของ run หนึ่งๆ (ใช้ debug ว่าผิดตรงไหน)
python -m pipeline_debug_agent.inspect --thread-id <id>
```

---

## Coding Conventions

- ทุก node รับ `state` แล้ว return `dict` ของเฉพาะ field ที่อัปเดต (อย่า mutate state ตรงๆ)
- node ต้อง pure + log trace ทุกครั้ง เพื่อให้ `get_state_history()` ย้อนดูได้
- ใส่ type hint ครบ (`DebugState` เป็น TypedDict)
- คอมเมนต์อธิบาย "ทำไม" เป็นภาษาไทยได้ แต่ชื่อ function/variable เป็นอังกฤษ
- ทุก side-effect ที่แตะภายนอก (git, db, pipeline) แยกไว้ใน `integrations/` ไม่ปนใน node

---

## Definition of Done (ก่อนถือว่า fix หนึ่งเสร็จ)

- [ ] test บน staging ผ่าน **และ** ตรวจแล้วว่าข้อมูล output ถูกต้องตาม semantic
- [ ] ประเมิน tier แล้ว route ไปถูกคน
- [ ] เปิด PR พร้อม context ครบ (สาเหตุ + diff + ผล test + trace link)
- [ ] commit ระบุ AI author + run id
- [ ] ถ้าเป็น data fix → เป็น migration script ที่ reproducible อยู่ใน git ไม่ใช่คำสั่งสดบน prod
- [ ] ไม่แตะ production นอกเหนือจากผ่าน PR/CD pipeline

---

## เริ่มจากตรงไหนดี (ลำดับแนะนำ)

1. **Context layer ก่อน** (`state.py` + `integrations/lineage.py`) — ต่อ log + schema + lineage ให้ครบ คุ้มที่สุด
2. **Check ให้แน่น** (`nodes/test.py` + staging) — ถ้า check อ่อน ทั้ง loop ไร้ค่า
3. **Inner loop** (`graph.py`) — เริ่มจากให้ agent "เสนอเฉยๆ" ยังไม่ auto
4. **Review + tiering** (`nodes/submit_pr.py`, `nodes/tiering.py`) — เริ่มจากทุก fix รอคนรีวิว แล้วค่อยปล่อย T1 auto เมื่อมั่นใจ
