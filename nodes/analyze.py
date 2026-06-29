"""
nodes/analyze.py — Action (ครึ่งแรก)
==================================
อ่าน error log + โค้ด + context แล้ววิเคราะห์ว่าทำไม pipeline ถึง fail
"""

from integrations.llm import get_llm
from state import DebugState


def analyze_error(state: DebugState) -> dict:
    """วิเคราะห์สาเหตุที่ fail — ยิ่ง context ครบ ยิ่งวิเคราะห์แม่น"""
    prompt = f"""คุณเป็น data engineer ผู้เชี่ยวชาญ
วิเคราะห์ว่า pipeline นี้ fail เพราะอะไร ตอบสั้น ตรงประเด็น

=== Error log ===
{state['error_log']}

=== โค้ด pipeline ===
{state['pipeline_code']}

=== Schema ที่เกี่ยวข้อง ===
{state.get('schema_info') or '(ไม่มีข้อมูล)'}

=== Runbook เดิม (ถ้ามี) ===
{state.get('runbook') or '(ไม่มี — เป็นปัญหาใหม่)'}
"""
    response = get_llm().invoke(prompt)
    print(f"🔍 [รอบ {state['attempts'] + 1}] วิเคราะห์: {response.content[:80]}...")
    return {"diagnosis": response.content}
