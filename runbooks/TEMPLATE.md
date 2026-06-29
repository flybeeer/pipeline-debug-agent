<!--
TEMPLATE สำหรับ runbook ต่อ issue class — copy ไฟล์นี้เป็น runbooks/<issue_id>.md แล้วเติม
ลบคอมเมนต์ <!-- ... --> ออกเมื่อเขียนเสร็จ
เขียนให้ "คนที่ไม่รู้ระบบลึก" ทำตามได้ = นั่นแหละถึงเรียกว่า runbook
-->

# Runbook: <ชื่อปัญหาแบบสั้น> (`<issue_id>`)

## 0. Metadata

| field | ค่า |
|-------|-----|
| Issue ID / pattern | `<issue_id>` หรือ regex ของ error |
| Pipeline / ไฟล์ | `models/.../<file>.sql` |
| เจ้าของ (DE) | `@<owner>` |
| Default tier | **T2** / **T3** (ดูข้อ 6) |
| Check spec | `config/checks/<issue_id>.yaml` |
| ครั้งล่าสุดที่ verify | `YYYY-MM-DD` โดย `@<who>` |

---

## 1. อาการ (Symptoms) — รู้ได้ไงว่าเจอปัญหานี้

- **Error signature:** `<ข้อความ error / exception ที่ชี้เฉพาะ>`
- **Alert/where:** มาจาก `<monitor/DAG/test ไหน>`
- **ตารางที่กระทบ:** `<schema.table>`
- ✅ ใช่ปัญหานี้ถ้า: `<เงื่อนไขยืนยัน>`
- ❌ ไม่ใช่ (ดู runbook อื่น) ถ้า: `<อาการคล้ายแต่คนละสาเหตุ>`

## 2. สาเหตุที่พบบ่อย (Known root causes)

1. `<สาเหตุหลัก — เช่น upstream ส่ง null มา>`
2. `<สาเหตุรอง>`

## 3. การวินิจฉัย (Diagnosis) — query บน staging เท่านั้น 🚨

```sql
-- ยืนยันสาเหตุ เช่น นับ null ใน key
SELECT COUNT(*) FILTER (WHERE <key> IS NULL) AS bad_rows
FROM <staging.table>;
```

- ผลที่บอกว่า "ใช่ปัญหานี้": `<เกณฑ์>`

## 4. วิธีแก้ (Fix procedure) — step by step

> 🚨 ทดสอบบน staging เท่านั้น · ต้อง **idempotent** (rerun แล้วผลเท่าเดิม)
> ใช้ `MERGE` / `INSERT OVERWRITE` / `CREATE OR REPLACE` — ห้าม `INSERT INTO` ดิบ

1. `<ขั้นที่ 1>`
2. `<ขั้นที่ 2 — โค้ด/SQL ที่แก้>`
   ```sql
   CREATE OR REPLACE TABLE <table> AS
   SELECT ... -- fix ที่ idempotent
   ```
3. `<ขั้นตรวจ>`

> ถ้าเป็น **data fix** (backfill/แก้ค่าผิด) → ต้องเป็น migration script ที่ commit เข้า git
> ห้ามรัน `UPDATE`/`DELETE` สดบน prod (กฎเหล็ก #5) → กรณีนี้บังคับ **T3**

## 5. การตรวจสอบว่าแก้ถูก (Verification) — semantic ไม่ใช่แค่ "ไม่ error"

- Check spec: `config/checks/<issue_id>.yaml` (`non_empty`, `no_nulls_in_keys`, `row_count_within`)
- คาดหวัง: `<row count ≈ baseline / no null in <key> / ยอดรวม reconcile>`
- ✅ ผ่านเมื่อ: `semantic_result` ขึ้น `PASS`

## 6. ประเมิน Tier (อิง `nodes/tiering.py`)

| เกณฑ์ | ค่า | หมายเหตุ |
|-------|-----|----------|
| Idempotent? | ☐ ใช่ / ☐ ไม่ | rerun ผลเท่าเดิมไหม |
| มี runbook (อันนี้)? | ☑ | |
| Blast radius (lineage) | `<n>` | เล็กถ้า ≤ `BLAST_RADIUS_SMALL` |
| เป็น data fix? | ☐ ใช่ → **T3** | |
| Backward-compatible / transient? | ☐ ใช่ → เข้าข่าย T1 | |

**→ Tier ที่ได้: `<T1/T2/T3>`**

## 7. ขอบเขต T2 (ops ทำเองได้) vs ดันขึ้น T3 (DE)

**T2 — Data Ops / on-call อนุมัติได้เอง เมื่อ:**
- fix ตรงกับ runbook นี้เป๊ะ + semantic check ผ่าน + blast เล็ก
- ไม่แตะ business logic / ไม่เปลี่ยนความหมายของตัวเลข

**ดันขึ้น T3 — ส่ง DE เจ้าของ pipeline เมื่อ:**
- อาการไม่ตรง runbook / เจอ root cause ใหม่
- ต้องแก้ business logic, schema, หรือ irreversible
- blast ใหญ่ / กระทบหลายทีม / กระทบลูกค้า
- เป็น data fix (backfill/แก้ค่า)

## 8. Rollback plan

- `<ย้อนยังไง — revert PR / restore partition / re-run upstream>`
- ใครต้องแจ้ง: `<ทีม downstream จาก lineage>`

## 9. Audit (เติมหลัง agent รัน)

- Agent-Run-Id: `<run_id>` · Trace: `<langfuse_url>` · PR: `<pr_url>`
