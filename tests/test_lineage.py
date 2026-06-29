"""
ทดสอบ lineage / blast radius จาก dbt manifest.json
รวมเคส fail-safe: หา lineage ไม่ได้ → ต้องคืนค่าใหญ่ (ดันขึ้น T3)
"""

import json

from integrations.lineage import UNKNOWN_BLAST, count_downstream

# manifest จำลอง: a → b → c, และ a → test (test ไม่นับเป็น blast)
_MANIFEST = {
    "nodes": {
        "model.s.a": {"original_file_path": "models/a.sql", "resource_type": "model"},
        "model.s.b": {"original_file_path": "models/b.sql", "resource_type": "model"},
        "model.s.c": {"original_file_path": "models/c.sql", "resource_type": "model"},
    },
    "child_map": {
        "model.s.a": ["model.s.b", "test.s.a_notnull"],
        "model.s.b": ["model.s.c"],
        "model.s.c": [],
        "test.s.a_notnull": [],
    },
}


def _write(tmp_path, manifest=_MANIFEST):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest))
    return str(p)


def test_counts_transitive_descendants(tmp_path):
    # a → b → c = 2 downstream (test ไม่นับ)
    assert count_downstream("models/a.sql", _write(tmp_path)) == 2


def test_leaf_has_no_downstream(tmp_path):
    assert count_downstream("models/c.sql", _write(tmp_path)) == 0


def test_excludes_tests_from_blast(tmp_path):
    # b → c เท่านั้น (1) — ไม่มี test ปน
    assert count_downstream("models/b.sql", _write(tmp_path)) == 1


def test_missing_manifest_is_failsafe_large():
    assert count_downstream("models/a.sql", "/no/such/manifest.json") == UNKNOWN_BLAST


def test_unresolved_node_is_failsafe_large(tmp_path):
    assert count_downstream("models/unknown.sql", _write(tmp_path)) == UNKNOWN_BLAST


def test_manifest_without_child_map_is_failsafe(tmp_path):
    bad = _write(tmp_path, {"nodes": _MANIFEST["nodes"]})  # ไม่มี child_map
    assert count_downstream("models/a.sql", bad) == UNKNOWN_BLAST
