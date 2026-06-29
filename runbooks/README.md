# Runbooks — คู่มือแก้ปัญหาต่อ issue class

Runbook = คู่มือ step-by-step ที่ทำตามได้โดยไม่ต้องเข้าใจระบบลึก
= ปัญหานี้ "เคยเจอและเข้าใจดีแล้ว" → เป็น 1 ใน 3 เกณฑ์ tiering

## Runbook ทำหน้าที่ 2 อย่าง

1. **เป็น context ให้ agent** — ป้อนเข้า `new_state(runbook=...)` แล้ว `nodes/analyze.py`
   อ่านตอนวิเคราะห์ → fix แม่นขึ้น และทำให้ `has_runbook=True` ใน `nodes/tiering.py`
2. **เป็นคู่มือให้คน approve** — T2 (Data Ops/on-call) หรือ T3 (DE) ทำตามเพื่อ
   review/อนุมัติ PR ของ agent หรือสานต่อเมื่อ agent ยอมแพ้

## ผลต่อ tier (ดู `nodes/tiering.py`)

| สถานการณ์ | tier | ใคร approve |
|-----------|------|-------------|
| idempotent + **มี runbook** + blast เล็ก (+ backward-compatible) | **T1** | auto-merge เมื่อ CI เขียว |
| idempotent + blast เล็ก แต่ **ยังไม่มี runbook** | **T2** | Data Ops / on-call |
| data fix / blast ใหญ่ / ขาดเกณฑ์ idempotent | **T3** | Data Engineer (เจ้าของ pipeline) |

> 📈 การเขียน runbook ที่ครบ = เครื่องมือดันงาน **T2 → T1** (ลด toil ของ DE)
> ส่วน T3 runbook = บันทึกความเข้าใจไว้ เพื่อรอบหน้าดันลงมา T2/T1 ได้

## Convention

- **ชื่อไฟล์:** `runbooks/<issue_id>.md` เช่น `runbooks/null-userid-042.md`
  (ให้ตรงกับ `issue_id` + ชื่อ check spec `config/checks/<issue_id>.yaml`)
- เริ่มจาก [`TEMPLATE.md`](./TEMPLATE.md) — copy แล้วเติม
- ดูตัวอย่างจริงที่ [`null-userid-042.md`](./null-userid-042.md)

## วิธีป้อนให้ agent (ตอนนี้)

```python
from pathlib import Path
runbook = Path(f"runbooks/{issue_id}.md").read_text() if ... else ""
state = new_state(issue_id=issue_id, ..., runbook=runbook)
```

> 💡 อยากให้ auto-load จาก `runbooks/<issue_id>.md` เหมือน check spec? เพิ่ม
> `load_runbook(issue_id)` ใน integrations ได้ (แพตเทิร์นเดียวกับ `load_check_config`)
