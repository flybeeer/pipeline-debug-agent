# Deployment Checklist — Pipeline Debugging Agent

คู่มือเอา agent ขึ้นใช้จริงแบบปลอดภัย เรียงตามลำดับ ทำทีละ phase อย่าข้าม
ทุกข้อยึด **กฎเหล็กใน [CLAUDE.md](./CLAUDE.md)** — ถ้าข้อไหนขัดกับกฎเหล็ก ห้ามทำ

> หลักการรวม: **ตั้ง env ครบ → ใช้ของจริง / ไม่ตั้ง → fallback offline**
> ทุก integration ออกแบบให้ fail ไปทาง "ปลอดภัย" (fail-closed / fail-safe) เสมอ

---

## Phase 0 — ก่อนเริ่ม (Prerequisites)

- [ ] Python >= 3.10
- [ ] ติดตั้งครบ: `pip install -e ".[github,observability,dev]"`
- [ ] รัน test ผ่านหมด: `pytest tests/ -v` (ควรเขียวทั้งหมดก่อนไปต่อ)
- [ ] รัน demo offline ผ่าน: `python demo/run_demo.py` → เห็น `✅ DEMO ผ่าน`
- [ ] อ่าน `CLAUDE.md` ส่วน "กฎเหล็ก" และ "Tiering" ให้เข้าใจตรงกันทั้งทีม

---

## Phase 1 — Context layer (ลงทุนตรงนี้คุ้มสุด)

- [ ] **dbt manifest** — ตั้ง `DBT_MANIFEST_PATH` ชี้ `target/manifest.json` ที่ build แล้ว
  - [ ] ยืนยันว่า `count_downstream()` คืนค่าถูก (ลองกับ model ที่รู้ downstream จริง)
  - [ ] ⚠️ ถ้าไม่มี manifest → lineage คืน `UNKNOWN_BLAST` → ทุก fix ตก **T3** (ตั้งใจให้ปลอดภัย)
- [ ] **Check spec ต่อ issue** — เขียน `config/checks/<issue_id>.yaml` (ดู `config/checks/example.yaml`)
  - [ ] ระบุ `key_columns` (ห้าม null), `sum_columns`, `baseline_row_count`, `row_count_tolerance`
  - [ ] ⚠️ ไม่มี config ของ issue → semantic check เป็น **UNVERIFIED** → ไม่ผ่าน (fail-closed)
- [ ] ตรวจว่า error log / schema ที่ป้อนเข้า `new_state()` ดึงจาก alert/log จริง ไม่ใช่ค่า demo

---

## Phase 2 — Check layer (staging เท่านั้น — กฎเหล็ก #3)

- [ ] ตั้ง `PIPELINE_TARGET_ENV=staging` (ค่า `production` จะถูก **ปฏิเสธ** ทั้งใน runner และ warehouse adapter)
- [ ] **อ่าน (validation) = Apache Iceberg:** `pip install -e .[iceberg]` + ตั้ง `~/.pyiceberg.yaml`
      (catalog/creds ของ staging) — ชี้ `iceberg_table` ใน check spec ไป namespace ของ staging
- [ ] **รัน fix (runner):** ตั้ง `PIPELINE_DUCKDB` (engine harness) หรือแทน `execute_pipeline()`
      ด้วย engine จริง (dbt/Trino/Spark) ที่เขียนลง Iceberg staging
- [ ] ยืนยันว่า staging **แยกขาดจาก production** จริง — catalog / namespace / warehouse คนละตัว
- [ ] ทดสอบ kill switch: ลองตั้ง `PIPELINE_TARGET_ENV=production` แล้วต้องโดน block ทันที
- [ ] ตรวจว่า adapter อ่าน Iceberg แบบ **read-only** (validation ไม่เขียน staging)

---

## Phase 3 — GitOps / Review layer (กฎเหล็ก #1, #2, #6)

- [ ] สร้าง **service account แยก** (ห้ามใช้ identity ของคน) ตั้ง `AGENT_GIT_NAME` / `AGENT_GIT_EMAIL`
- [ ] ออก `GITHUB_TOKEN` ของ service account ด้วย **least privilege**:
  - [ ] ให้สิทธิ์แค่ **create branch + open PR** ในเป้าหมาย repo
  - [ ] ❌ ห้ามให้สิทธิ์ push `main` / merge ตรง / admin
- [ ] ตั้ง `GITHUB_REPO=org/repo` และ `GITHUB_BASE_BRANCH` (default `main`)
- [ ] เปิด **branch protection** บน base: require PR review + required status checks (CI)
- [ ] ตั้ง **required CI checks** ให้ครบ — auto-merge ของ T1 จะรอ check พวกนี้เขียวก่อนเท่านั้น
- [ ] ทดสอบยิง PR จริงกับ **repo ทดสอบ** ก่อน (ดู branch `ai-fix/<id>` + PR body มี ⚠️ AI + run id + trace)
- [ ] ยืนยัน commit trailer มี `Agent-Run-Id` + `Trace` ครบ (audit ได้)

---

## Phase 4 — Observability (กฎเหล็ก #6 — ต้อง audit ได้)

- [ ] ตั้ง `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (+ `LANGFUSE_HOST` ถ้า self-host)
- [ ] ยิง run ทดสอบ แล้วเปิด LangFuse เห็น trace ครบทุก node (analyze→fix→test→tiering→submit)
- [ ] ยืนยัน trace **group ด้วย run_id** — เอา run id จาก PR ไปหา trace ใน LangFuse เจอ
- [ ] ตั้ง alert/retention ของ LangFuse ตาม data governance ของทีม

---

## Phase 5 — Circuit breaker & cost (กฎเหล็ก #7)

- [ ] ทบทวน `MAX_ATTEMPTS` ใน `config/goal.py` (default 3) — กันวนแก้ไม่จบ = เปลือง token
- [ ] ทบทวน `BLAST_RADIUS_SMALL` (default 3) — เกณฑ์ว่า blast เท่าไรถึง "เล็ก"
- [ ] ทบทวน `MODEL` + `MAX_TOKENS` — เลือกโมเดล/งบ token ต่อ run ให้เหมาะ
- [ ] ตั้งงบ/rate limit ที่ฝั่ง Anthropic API (ANTHROPIC_API_KEY ของ service account)
- [ ] เปลี่ยน checkpointer จาก `MemorySaver` → **PostgresSaver** ใน `graph.build_app()` (state ต้องคงอยู่ข้าม process ใน prod)

---

## Phase 6 — Staged rollout (ค่อยๆ ปล่อย อย่าเปิด auto ทันที)

ดันงานจาก **T3 → T2 → T1** เมื่อมั่นใจ เพื่อไม่ให้ DE จมกับ ops toil:

- [ ] **Stage A — เสนอเฉยๆ:** ทุก fix เปิด PR รอคนรีวิว 100% (ยังไม่เปิด auto-merge)
  - [ ] รันคู่ขนานกับ on-call เดิม สัก 1–2 สัปดาห์ เก็บสถิติ false positive
- [ ] **Stage B — T2 ให้ ops:** ปัญหาที่มี runbook + idempotent + blast เล็ก → route ให้ Data Ops
- [ ] **Stage C — T1 auto:** เปิด auto-merge เฉพาะ class ที่พิสูจน์แล้วว่านิ่ง + backward-compatible
  - [ ] เริ่มจาก pipeline ที่ blast เล็กสุด + มี CI/semantic check แน่นสุดก่อน
- [ ] กำหนด **rollback plan**: ปิด auto-merge ได้ทันที (revert PR / ลบ branch protection rule)

---

## Phase 7 — Pre-flight ก่อนเปิดใช้แต่ละ pipeline

ทำซ้ำต่อ pipeline ที่จะให้ agent ดูแล:

- [ ] มี `config/checks/<issue_id>.yaml` ครอบคลุม semantic ที่ "เพี้ยนเงียบ" ได้
- [ ] pipeline อยู่ใน dbt manifest → lineage นับ blast ได้ (ไม่ตก UNKNOWN)
- [ ] มี CI ที่รัน test จริงบน PR (auto-merge T1 พึ่งตรงนี้)
- [ ] ผ่าน **Definition of Done** ใน CLAUDE.md ครบทุกข้อ

---

## Quick reference — Environment variables

| Env | จำเป็น | หน้าที่ | ถ้าไม่ตั้ง |
|-----|--------|--------|-----------|
| `ANTHROPIC_API_KEY` | ✅ | เรียก Claude (analyze/fix) | LLM ใช้ไม่ได้ |
| `PIPELINE_TARGET_ENV` | ✅ | บังคับ staging | default `staging` (ค่า `production` ถูก block) |
| `WAREHOUSE` | — | engine ของ Check (validation) | default `iceberg` |
| `ICEBERG_CATALOG` | ✅* | ชื่อ catalog ใน `~/.pyiceberg.yaml` | default `default` |
| `PIPELINE_DUCKDB` | ✅* | DuckDB harness ที่ runner รัน fix SQL | runner raise (กันเผลอรันผิดที่) |
| `CHECKS_DIR` | — | ที่เก็บ check spec | default `config/checks` |
| `DBT_MANIFEST_PATH` | ✅* | lineage / blast radius | คืน UNKNOWN → ทุก fix ตก T3 |
| `GITHUB_TOKEN` | ✅* | service account เปิด PR | git_client เป็น stub (พิมพ์เฉยๆ) |
| `GITHUB_REPO` | ✅* | repo เป้าหมาย `org/repo` | git_client เป็น stub |
| `GITHUB_BASE_BRANCH` | — | base ของ PR | default `main` |
| `AGENT_GIT_NAME` / `AGENT_GIT_EMAIL` | — | identity ของ agent | default `debug-agent` |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | ✅* | trace / audit | observability ปิดเงียบ |
| `LANGFUSE_HOST` | — | endpoint LangFuse | default cloud |

`✅*` = จำเป็นสำหรับ **production จริง** แต่ระบบรัน offline ได้โดยไม่ตั้ง (fallback ปลอดภัย)

---

## ❌ ห้ามเด็ดขาด (กฎเหล็ก — ถึงจะถูกสั่งก็ห้าม)

- ห้าม agent แก้ production ตรงๆ — ทุกอย่างผ่าน branch + PR เท่านั้น
- ห้ามให้ service account มีสิทธิ์ merge เอง (ยกเว้น auto-merge rule ที่ผูก CI)
- ห้ามให้ Check รันบน production data — staging/test เท่านั้น
- ห้ามถือว่า "ไม่มี error = สำเร็จ" — ต้องผ่าน semantic check ด้วย
- ห้ามรัน `UPDATE`/`DELETE` สดบน prod — data fix ต้องเป็น migration script ใน git
- ห้าม commit ปลอมเป็นมนุษย์ — ต้องใช้ identity ของ agent + แนบ run id/trace
