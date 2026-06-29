"""
dashboard.py — Streamlit dashboard กดรัน + monitor agent
========================================================
รัน:  streamlit run dashboard.py
เปิดที่ http://localhost:8501 (เปิด browser ให้อัตโนมัติ)

ทำได้:
  • เลือก backend (offline demo / real Claude+Iceberg) ใน UI
  • กรอก context ของปัญหา (error log / โค้ด / schema) แล้วกดรัน
  • monitor loop ทีละ node แบบ live: analyze → fix → test → tiering → submit
  • ดูประวัติ run ย้อนหลังในเซสชัน + ผลลัพธ์ output บน warehouse

🚨 ยังยึดกฎเหล็กเดิม: Check รันบน staging เท่านั้น, agent แค่เปิด PR (ไม่ merge เอง)
   dashboard เป็นแค่ "ปุ่มกด inner loop" — ไม่ได้เพิ่มสิทธิ์ให้ agent
"""

from __future__ import annotations

import os
import socket
import tempfile
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

import dashboard_backends as backends
from config.goal import BLAST_RADIUS_SMALL, MAX_ATTEMPTS
from graph import build_app
from state import new_state

st.set_page_config(page_title="Pipeline Debug Agent", page_icon="🛠️", layout="wide")

# ── session state ──
if "history" not in st.session_state:
    st.session_state.history = []          # list ของ run ที่ผ่านมา (ในเซสชันนี้)
if "run_seq" not in st.session_state:
    st.session_state.run_seq = 0

_NODE_META = {
    "analyze": ("🔍", "Analyze — วิเคราะห์สาเหตุ"),
    "fix": ("🛠️", "Fix — เสนอโค้ดแก้"),
    "test": ("✅", "Check — รัน + ตรวจ semantic บน staging"),
    "tiering": ("🏷️", "Tiering — ประเมิน tier"),
    "submit_pr": ("📝", "Review — เปิด PR"),
    "escalate": ("🛑", "Escalate — ยอมแพ้ ส่งให้คน"),
}
_STATUS_BADGE = {
    "auto_merged": "🟢 auto_merged (T1)",
    "awaiting_review": "🟡 awaiting_review",
    "gave_up": "🔴 gave_up",
    "fixed": "🟢 fixed",
    "running": "⚪ running",
}


def _tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """เช็คเร็วๆ ว่าพอร์ตเปิดอยู่ไหม (ใช้ดูว่า docker stack ขึ้นหรือยัง)"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _render_node(container, node: str, update: dict, full: dict) -> None:
    """วาดผลของ 1 node ลง timeline ตามชนิดของ node"""
    emoji, label = _NODE_META.get(node, ("•", node))
    with container.expander(f"{emoji} {label}", expanded=True):
        if node == "analyze":
            st.markdown(update.get("diagnosis", "") or "_(ไม่มี)_")
        elif node == "fix":
            st.code(update.get("proposed_fix", ""), language="sql")
            st.caption(f"รอบที่ {update.get('attempts', full.get('attempts'))}")
        elif node == "test":
            tr = update.get("test_result", "")
            sr = update.get("semantic_result", "")
            (st.success if tr.startswith("PASS") else st.error)(f"test: {tr}")
            if sr:
                (st.success if sr.startswith("PASS") else st.warning)(f"semantic: {sr}")
            if not sr and not tr.startswith("PASS"):
                st.info("→ feed error กลับเข้า loop เพื่อวิเคราะห์รอบใหม่")
        elif node == "tiering":
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Tier", update.get("tier", "?"))
            c2.metric("Blast radius", update.get("blast_radius", "?"),
                      help=f"≤ {BLAST_RADIUS_SMALL} = เล็ก")
            c3.metric("Idempotent", "✓" if update.get("is_idempotent") else "✗")
            c4.metric("Runbook", "✓" if update.get("has_runbook") else "✗")
            if update.get("is_data_fix"):
                st.warning("⚠️ เป็น data fix — ต้องทำผ่าน migration script ที่ DE review")
        elif node in ("submit_pr", "escalate"):
            if url := update.get("pr_url"):
                st.markdown(f"**PR:** [{url}]({url})")
            st.write(f"สถานะ: {_STATUS_BADGE.get(update.get('status'), update.get('status'))}")


# ── Sidebar: เลือก backend + pre-flight ──
with st.sidebar:
    st.header("⚙️ Backend")
    mode_label = st.radio(
        "โหมดรัน",
        ["Offline demo", "Real (Claude + Iceberg)"],
        help="offline = fake LLM + DuckDB (ไม่ต้องมี key/docker)\n"
             "real = Claude จริงผ่าน gateway + Trino→Iceberg (ต้องมี docker stack + .env)",
    )
    mode = "real" if mode_label.startswith("Real") else "offline"

    st.divider()
    st.caption("Pre-flight")
    if mode == "real":
        trino_up = _tcp_open("localhost", 8080)
        env_ok = bool(os.environ.get("CODESMART_API_KEY")
                      or os.environ.get("ANTHROPIC_API_KEY")
                      or (backends.ROOT / ".env").exists())
        st.write(("✅" if trino_up else "❌") + " Trino (localhost:8080)")
        if not trino_up:
            st.code("docker compose -f dev/iceberg/docker-compose.yml up -d", language="bash")
        st.write(("✅" if env_ok else "❌") + " .env / CODESMART_API_KEY")
    else:
        st.write("✅ ไม่ต้องตั้งค่าอะไร (offline)")

    st.divider()
    st.caption("Config (config/goal.py)")
    st.write(f"MAX_ATTEMPTS = **{MAX_ATTEMPTS}**")
    st.write(f"BLAST_RADIUS_SMALL = **{BLAST_RADIUS_SMALL}**")

# ── Main ──
st.title("🛠️ Pipeline Debugging Agent")
st.caption("Goal → Context → Action → Check → Fix → Repeat → Review")

defaults = dict(backends.DEFAULT_INPUTS)
with st.form("run_form"):
    st.subheader("Context — ป้อนข้อมูลของปัญหา")
    col_a, col_b = st.columns(2)
    issue_id = col_a.text_input("Issue ID", defaults["issue_id"])
    file_path = col_b.text_input("File path", defaults["file_path"])
    error_log = st.text_area("Error log", defaults["error_log"], height=80)
    pipeline_code = st.text_area("Pipeline code (ที่พัง)", defaults["pipeline_code"], height=120)
    schema_info = st.text_area("Schema / hints", defaults["schema_info"], height=100)
    submitted = st.form_submit_button(f"▶️ รัน agent ({mode})", type="primary",
                                       use_container_width=True)

if submitted:
    st.session_state.run_seq += 1
    work_dir = Path(tempfile.mkdtemp(prefix="pda-dash-"))

    try:
        prep = backends.prepare(mode, work_dir)
    except Exception as e:                                   # docker/.env ไม่พร้อม ฯลฯ
        st.error(f"เตรียม backend ไม่สำเร็จ: {e}")
        st.stop()

    st.info(f"Backend: {prep['info']}")
    initial = new_state(
        issue_id=issue_id, error_log=error_log, pipeline_code=pipeline_code,
        file_path=file_path, schema_info=schema_info,
    )
    app = build_app()
    thread_id = f"{issue_id}-{st.session_state.run_seq}"

    st.subheader("📡 Live monitor")
    timeline = st.container()
    final: dict = dict(initial)
    started = time.time()

    try:
        with st.spinner("กำลังรัน loop..."):
            for chunk in app.stream(
                initial, config={"configurable": {"thread_id": thread_id}},
                stream_mode="updates",
            ):
                for node, update in chunk.items():
                    if update:
                        final.update(update)
                        _render_node(timeline, node, update, final)
    except Exception as e:
        st.error(f"run ล้มเหลว: {e}")
        st.stop()

    elapsed = time.time() - started

    # ── สรุปผล ──
    st.subheader("🏁 ผลลัพธ์")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("สถานะ", final.get("status", "?"))
    c2.metric("Tier", final.get("tier", "?"))
    c3.metric("รอบที่ลอง", f"{final.get('attempts', 0)}/{MAX_ATTEMPTS}")
    c4.metric("เวลา", f"{elapsed:.1f}s")
    if final.get("pr_url"):
        st.markdown(f"**PR:** [{final['pr_url']}]({final['pr_url']})")

    rows = prep["read_output"]()
    if rows:
        st.caption("Output บน warehouse หลัง fix")
        if mode == "real":
            _, rc, nulls, ssum = rows[0]
            st.write(f"row_count=**{rc}**, null(user_id)=**{nulls}**, sum(amount)=**{ssum}**")
        else:
            st.table([{"user_id": r[0], "amount": r[1]} for r in rows])

    # เก็บเข้า history
    st.session_state.history.insert(0, {
        "time": datetime.now().strftime("%H:%M:%S"),
        "mode": mode,
        "issue_id": issue_id,
        "status": final.get("status"),
        "tier": final.get("tier"),
        "attempts": final.get("attempts"),
        "run_id": final.get("run_id"),
        "pr_url": final.get("pr_url"),
        "diagnosis": final.get("diagnosis", ""),
        "proposed_fix": final.get("proposed_fix", ""),
        "semantic_result": final.get("semantic_result", ""),
    })

# ── ประวัติ run ──
st.divider()
st.subheader("📜 ประวัติ run (เซสชันนี้)")
if not st.session_state.history:
    st.caption("ยังไม่มี run — กดรันด้านบนก่อน")
else:
    st.table([
        {"เวลา": h["time"], "โหมด": h["mode"], "issue": h["issue_id"],
         "สถานะ": _STATUS_BADGE.get(h["status"], h["status"]),
         "tier": h["tier"], "รอบ": h["attempts"], "run_id": h["run_id"]}
        for h in st.session_state.history
    ])
    for h in st.session_state.history:
        with st.expander(f"{h['time']} · {h['issue_id']} · {h['status']} ({h['tier']})"):
            st.markdown(f"**Diagnosis:** {h['diagnosis'][:300]}")
            st.code(h["proposed_fix"], language="sql")
            st.write(f"semantic: {h['semantic_result']}")
            if h["pr_url"]:
                st.markdown(f"PR: [{h['pr_url']}]({h['pr_url']})")
