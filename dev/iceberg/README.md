# Local Apache Iceberg (Docker) สำหรับ dev/staging

ตั้ง Iceberg จริงแบบ local (REST catalog + MinIO) ไว้พัฒนา/ทดสอบ Check layer
โดยไม่ต้องมี cloud warehouse จริง — ใช้แทนชั่วคราวจนกว่าจะต่อ Iceberg ของจริง

## เริ่ม

```bash
# 1) ยก stack ขึ้น (REST :8181, MinIO :9000/:9001, Trino :8080)
docker compose -f dev/iceberg/docker-compose.yml up -d

# 2) ติดตั้ง client (s3fs คุยกับ MinIO, trino รัน fix SQL)
pip install -e ".[iceberg,trino]" "pyiceberg[s3fs]"

# 3) ชี้ PyIceberg มาที่ config ของ local (catalog ชื่อ "local")
export PYICEBERG_HOME=$PWD/dev/iceberg

# 4a) smoke test (read path) — เขียน table จริ ง + อ่านด้วย IcebergAdapter
python dev/iceberg/smoke_test.py
# ✅ SMOKE TEST ผ่าน (row_count=4, null(user_id)=1, sum(amount)=159.0)

# 4b) verify full loop — Trino รัน fix (execute) → IcebergAdapter อ่าน (Check)
python dev/iceberg/verify_loop.py
# ✅ VERIFY ผ่าน (row_count=3, null(user_id)=0, sum(amount)=65.0)

# หยุด (ข้อมูลใน MinIO เป็น ephemeral — หายตอน down)
docker compose -f dev/iceberg/docker-compose.yml down
```

## ต่อ agent เข้ากับ local Iceberg

ใน check spec (`config/checks/<issue_id>.yaml`):

```yaml
warehouse: iceberg
iceberg_table: staging.daily_sales
catalog: local            # ตรงกับ catalog ใน .pyiceberg.yaml
key_columns: [user_id]
sum_columns: [amount]
baseline_row_count: 4
```

แล้ว export `PYICEBERG_HOME=$PWD/dev/iceberg` ก่อนรัน agent
(หรือคัดลอกค่าใน `.pyiceberg.yaml` ไปไว้ที่ `~/.pyiceberg.yaml`)

## ไฟล์ในโฟลเดอร์นี้

| ไฟล์ | หน้าที่ |
|------|--------|
| `docker-compose.yml` | REST catalog + MinIO + mc + **Trino** (Iceberg connector) |
| `trino/iceberg.properties` | catalog config ของ Trino → ต่อ REST + MinIO เดียวกัน |
| `.pyiceberg.yaml` | config catalog `local` (creds dev เท่านั้น) |
| `smoke_test.py` | read path — เขียน table จริงแล้วอ่านด้วย `IcebergAdapter` |
| `verify_loop.py` | full loop — `RUNNER=trino` รัน fix (execute) → อ่าน (Check) |

> รัน agent ให้ execute ลง Iceberg จริง: `export RUNNER=trino` (+ `TRINO_HOST`/`TRINO_CATALOG`/
> `TRINO_SCHEMA` ถ้าไม่ใช่ default `localhost`/`iceberg`/`staging`) — default `RUNNER=duckdb`
> คือ harness offline ของ demo/test

> ⚠️ creds (`admin`/`password`) เป็นของ local dev เท่านั้น — prod ใช้ catalog/IAM จริง
> และต้องชี้ไป **staging** เสมอ (adapter บล็อก `PIPELINE_TARGET_ENV=production`)
