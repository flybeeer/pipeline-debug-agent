# Pipeline Debugging Agent

AI agent ที่ช่วย debug data pipeline ที่ fail โดยวน loop วิเคราะห์ → เสนอ fix → ทดสอบ
จนผ่าน แล้วเปิด Pull Request ให้คนรีวิวก่อน merge สร้างด้วย **LangGraph**

> 📌 อ่าน [`CLAUDE.md`](./CLAUDE.md) ก่อน — เป็นกฎ + สถาปัตยกรรมฉบับเต็มที่ Claude Code ใช้ทำงานต่อ
> 🚀 จะเอาขึ้น production → ทำตาม [`DEPLOYMENT.md`](./DEPLOYMENT.md) ทีละ phase

## Framework

`Goal → Context → Action → Check → Fix → Repeat → Review`

```
START → analyze → fix → test ─┬─(retry)→ analyze        [inner loop: agent วนเอง]
                              ├─(fixed)→ tiering → submit_pr → END
                              └─(give_up)→ escalate → END  [outer gate: คน + GitOps]
```

## เริ่มใช้งาน

```bash
# ติดตั้ง
pip install -e .

# ตั้งค่า key + บังคับให้รันที่ staging เท่านั้น
export ANTHROPIC_API_KEY=sk-...
export PIPELINE_TARGET_ENV=staging

# เปิด PR จริงผ่าน GitHub (service account จำกัดสิทธิ์: create branch + open PR เท่านั้น)
#   ไม่ตั้ง → git_client เป็น stub พิมพ์เฉยๆ (offline). ติดตั้ง: pip install -e .[github]
export GITHUB_TOKEN=ghp_...           # token ของ service account (ห้ามใช้ของคน)
export GITHUB_REPO=your-org/your-repo
export GITHUB_BASE_BRANCH=main        # optional (default: main)

# observability: ส่ง trace ไป LangFuse (ไม่ตั้ง = ปิดเงียบ). ติดตั้ง: pip install -e .[observability]
export LANGFUSE_PUBLIC_KEY=pk-...
export LANGFUSE_SECRET_KEY=sk-...
export LANGFUSE_HOST=https://cloud.langfuse.com   # optional (self-host ได้)

# รัน agent
python -m pipeline_debug_agent --issue-id demo-001

# ย้อนดู state ทุก step (debug)
python -m pipeline_debug_agent --inspect --thread-id demo-001

# รัน test + lint (เหมือนที่ CI รัน — ดู .github/workflows/ci.yml)
pytest tests/ -v
ruff check .

# (แนะนำ) ตั้ง pre-commit hook ให้รัน ruff + pytest อัตโนมัติก่อน commit
pip install -e ".[dev]"
pre-commit install
pre-commit run --all-files   # ลองรันมือทั้งหมดครั้งแรก
```

> CI (GitHub Actions) รัน `ruff` + `pytest` (Python 3.10/3.11/3.12) ทุก push/PR เข้า main
> — test รัน offline ล้วน ไม่ต้องมี secret. ตั้งเป็น required check ให้ auto-merge T1 พึ่งได้

## Demo (รันจริงแบบ offline — ไม่ต้องมี API key)

เห็น loop เต็มวง `Action→Check→Repeat→Review` โดยใช้ fake LLM + DuckDB staging จริง:
runner รัน SQL ของ fix ลง DuckDB → validation อ่าน output table เดียวกันตรวจ semantic จริง
→ tiering ได้ T1 → เปิด PR (stub) auto-merge

```bash
python demo/run_demo.py
```

scenario: `daily_sales` เจอ null ใน `user_id` → fix เขียนใหม่ให้ idempotent (`CREATE OR
REPLACE`) + กรอง null → ผ่าน semantic check (non-empty + no-null-key + row-count drift)

## โครงสร้าง

| ไฟล์ | step | หน้าที่ |
|------|------|--------|
| `config/goal.py` | Goal | success criteria + MAX_ATTEMPTS |
| `state.py` | Context | `DebugState` — ความจำที่ส่งต่อระหว่าง node |
| `nodes/analyze.py`, `nodes/fix.py` | Action | วิเคราะห์ + เสนอ fix |
| `nodes/test.py` | Check | รันกับ staging data |
| `graph.py` | Repeat | ประกอบ loop + conditional edges |
| `nodes/tiering.py` | — | ประเมิน tier (idempotent/runbook/blast) |
| `nodes/submit_pr.py` | Review | เปิด PR ตาม tier |
| `integrations/` | — | git, lineage, runner (side-effects แยกไว้ที่นี่) |

## ต้องแก้ตรงไหนก่อนใช้จริง (มองหา `TODO`)

1. `integrations/runner.py` — รัน SQL บน DuckDB staging แล้ว (แทนด้วย dbt/Airflow ได้)
2. ✅ `integrations/git_client.py` — เปิด PR จริงผ่าน GitHub API แล้ว (PyGithub)
   ตั้ง `GITHUB_TOKEN`+`GITHUB_REPO` เพื่อเปิดโหมดจริง / ไม่ตั้ง = stub offline
3. ✅ `integrations/lineage.py` — นับ blast radius จาก dbt `manifest.json` แล้ว
   (`child_map` แบบ transitive, ตัด test ออก, fail-safe → T3 ถ้าหา lineage ไม่ได้)
   ตั้ง path ผ่าน env `DBT_MANIFEST_PATH` (default `target/manifest.json`)
4. ✅ `integrations/validation.py` — semantic check ต่อ DuckDB จริงแล้ว
   (อ่าน staging read-only, spec ต่อ issue ที่ `config/checks/<issue_id>.yaml`,
   ดูตัวอย่าง `config/checks/example.yaml`). ถ้าใช้ warehouse อื่น แทน
   `fetch_output_stats()` ด้วย client ของ stack นั้น
5. ✅ `observability/trace.py` — ต่อ LangFuse แล้ว (callback handler, รองรับ v2/v3,
   group trace ด้วย run_id, ปิดเงียบเมื่อไม่ตั้ง key) ตั้ง `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`
