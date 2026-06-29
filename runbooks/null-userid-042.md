# Runbook: null user_id ใน daily_sales (`null-userid-042`)

## 0. Metadata

| field | ค่า |
|-------|-----|
| Issue ID / pattern | `null-userid-042` · error: `null user_id ใน ... aggregation` |
| Pipeline / ไฟล์ | `models/sales/daily_sales.sql` |
| เจ้าของ (DE) | `@data-eng-sales` |
| Default tier | **T2** (ดูข้อ 6 — มี runbook นี้แล้วเข้าข่าย T1 ถ้า auto เปิด) |
| Check spec | `config/checks/null-userid-042.yaml` |
| ครั้งล่าสุดที่ verify | `2026-06-29` โดย `@on-call` |

---

## 1. อาการ (Symptoms)

- **Error signature:** `ValueError: null user_id ใน daily_sales aggregation`
- **Alert/where:** dbt test `not_null_user_id` บน `daily_sales` แดง / DAG `sales_daily` fail ที่ step aggregate
- **ตารางที่กระทบ:** `daily_sales` (และ downstream `weekly_rollup`)
- ✅ ใช่ปัญหานี้ถ้า: มีแถว `user_id IS NULL` หลุดมาจาก `raw_sales`
- ❌ ไม่ใช่ถ้า: `user_id` ครบแต่ `amount` เพี้ยน → คนละ runbook (ปัญหา business logic)

## 2. สาเหตุที่พบบ่อย

1. Upstream `raw_sales` มีแถว `user_id = NULL` (เช่น event ก่อน login / backfill ไม่ครบ)
   หลุดเข้า aggregate → `GROUP BY user_id` ได้ bucket null
2. (พบน้อย) join กับ dim ที่ key หาย → ดู runbook อื่นถ้าใช่

## 3. การวินิจฉัย — staging เท่านั้น 🚨

```sql
-- ยืนยันว่ามี null user_id จริงใน source
SELECT COUNT(*) FILTER (WHERE user_id IS NULL) AS bad_rows,
       COUNT(*) AS total
FROM raw_sales;
```

- ใช่ปัญหานี้ถ้า `bad_rows > 0`

## 4. วิธีแก้ (Fix procedure)

> 🚨 staging เท่านั้น · idempotent ด้วย `CREATE OR REPLACE`

1. ตรวจว่า null มาจาก source จริง (ข้อ 3) ไม่ใช่ bug ใน join
2. เขียน output ใหม่แบบกรอง null + idempotent:
   ```sql
   CREATE OR REPLACE TABLE daily_sales AS
   SELECT user_id, SUM(amount) AS amount
   FROM raw_sales
   WHERE user_id IS NOT NULL     -- กรองตัวปัญหา
   GROUP BY user_id;
   ```
3. รัน semantic check (ข้อ 5)

> หมายเหตุ: นี่เป็น **code fix** (เปลี่ยน query) ไม่ใช่ data fix → ไม่ต้องทำ migration
> ถ้าต้อง backfill ค่าที่หายไปย้อนหลังด้วย → กลายเป็น data fix → ดันขึ้น **T3**

## 5. การตรวจสอบ (Verification)

- Check spec `config/checks/null-userid-042.yaml`:
  - `no_nulls_in_keys(user_id)` — ต้องไม่มี null ใน `user_id`
  - `row_count_within(10%)` เทียบ `baseline_row_count: 3`
  - `non_empty_output`
- คาดหวัง: 3 แถว (user 1/2/3), ไม่มี null, ยอด `amount` รวมเท่าเดิมหลังหัก null bucket
- ✅ ผ่านเมื่อ `semantic_result = PASS`

## 6. ประเมิน Tier

| เกณฑ์ | ค่า |
|-------|-----|
| Idempotent? | ☑ ใช่ (`CREATE OR REPLACE`) |
| มี runbook (อันนี้)? | ☑ |
| Blast radius | `1` (`weekly_rollup`) → เล็ก |
| เป็น data fix? | ☐ ไม่ |
| Backward-compatible? | ☑ (แค่กรอง null ที่ไม่ควรอยู่แต่แรก) |

**→ Tier: T2** (ops อนุมัติได้) — และถ้าเปิด auto สำหรับ class นี้แล้ว เข้าข่าย **T1**

## 7. ขอบเขต T2 vs T3

**T2 (on-call อนุมัติเองได้):** fix ตรง runbook นี้ + semantic ผ่าน + blast = 1

**ดันขึ้น T3 (DE) ถ้า:**
- null โผล่เพราะ schema upstream เปลี่ยน (ไม่ใช่แค่ข้อมูลสกปรก)
- ต้อง backfill ย้อนหลัง (data fix)
- จำนวน null สูงผิดปกติ (เช่น > 30% ของแถว) → อาจเป็นปัญหา pipeline ต้นน้ำ ไม่ใช่แค่กรองทิ้ง

## 8. Rollback plan

- revert PR → `daily_sales` กลับเป็น query เดิม
- แจ้งทีม downstream ของ `weekly_rollup` ถ้าตัวเลขขยับ

## 9. Audit

- Agent-Run-Id: `<run_id>` · Trace: `<langfuse_url>` · PR: `<pr_url>`
