# Local Apache Iceberg (Docker) สำหรับ dev/staging

ตั้ง Iceberg จริงแบบ local (REST catalog + MinIO) ไว้พัฒนา/ทดสอบ Check layer
โดยไม่ต้องมี cloud warehouse จริง — ใช้แทนชั่วคราวจนกว่าจะต่อ Iceberg ของจริง

## เริ่ม

```bash
# 1) ยก stack ขึ้น (REST catalog :8181, MinIO :9000 / console :9001)
docker compose -f dev/iceberg/docker-compose.yml up -d

# 2) ติดตั้ง client (s3fs สำหรับคุยกับ MinIO)
pip install -e ".[iceberg]" "pyiceberg[s3fs]"

# 3) ชี้ PyIceberg มาที่ config ของ local (catalog ชื่อ "local")
export PYICEBERG_HOME=$PWD/dev/iceberg

# 4) smoke test — สร้าง table จริง + เขียนข้อมูล + อ่านด้วย IcebergAdapter ของเรา
python dev/iceberg/smoke_test.py
# คาดหวัง: ✅ SMOKE TEST ผ่าน (row_count=4, null(user_id)=1, sum(amount)=159.0)

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
| `docker-compose.yml` | REST catalog + MinIO + mc (สร้าง bucket `warehouse` อัตโนมัติ) |
| `.pyiceberg.yaml` | config catalog `local` (creds dev เท่านั้น) |
| `smoke_test.py` | สร้าง/เขียน table จริง แล้วอ่านด้วย `IcebergAdapter` → ยืนยันถูกต้อง |

> ⚠️ creds (`admin`/`password`) เป็นของ local dev เท่านั้น — prod ใช้ catalog/IAM จริง
> และต้องชี้ไป **staging** เสมอ (adapter บล็อก `PIPELINE_TARGET_ENV=production`)
