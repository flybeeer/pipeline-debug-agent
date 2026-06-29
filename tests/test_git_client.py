"""
ทดสอบ git_client — ทั้งโหมด stub (offline) และโหมดจริง (mock PyGithub)
ไม่ยิง GitHub จริง: ฉีด fake module `github` เข้า sys.modules แทน
"""

import sys
import types

import pytest

import integrations.git_client as gc

_KW = dict(
    issue_id="null-userid-042",
    file_path="models/sales/daily_sales.sql",
    new_code="CREATE OR REPLACE TABLE t AS SELECT 1",
    diagnosis="null user_id ใน source\nรายละเอียดเพิ่ม",
    error_log="ValueError: null user_id",
    test_result="PASS",
    run_id="abc123",
    trace_url="https://trace/xyz",
)


# ── (1) commit/PR message ── convention ── audit ──

def test_messages_have_audit_trailer_and_ai_warning():
    commit, title, body = gc._build_messages(
        "null-userid-042", "null user_id\nบรรทัดสอง", "err", "PASS", "abc123", "http://t"
    )
    # commit trailer สำหรับ audit (กฎเหล็ก #6) — ใช้บรรทัดแรกของ diagnosis
    assert "fix: null user_id" in commit
    assert "Agent-Run-Id: abc123" in commit
    assert "Trace: http://t" in commit
    # PR ต้องเตือนชัดว่า AI แก้ + ระบุ identity ของ agent (ไม่ปลอมเป็นคน)
    assert "AI" in body and gc.AGENT_AUTHOR in body
    assert "PASS" in body
    assert title.startswith("[AI fix] null-userid-042")


# ── (2) โหมด stub (ไม่ตั้ง GitHub) ──

def test_stub_when_unconfigured(monkeypatch):
    monkeypatch.setattr(gc, "GITHUB_TOKEN", "")
    monkeypatch.setattr(gc, "GITHUB_REPO", "")
    pr = gc.create_branch_and_pr(**_KW)
    assert pr.branch == "ai-fix/null-userid-042"
    assert pr.number is None and pr._obj is None
    # enable_auto_merge บน stub ต้องไม่พัง
    gc.enable_auto_merge(pr)


# ── (3) โหมดจริง: mock PyGithub ──

class _FakeException(Exception):
    def __init__(self, status):
        self.status = status


class _Contents:
    sha = "oldsha"


class _FakePR:
    def __init__(self, number, automerge_calls):
        self.number = number
        self.html_url = f"https://github.com/o/r/pull/{number}"
        self._automerge_calls = automerge_calls

    def enable_automerge(self, merge_method):
        self._automerge_calls.append(merge_method)


class _FakeRepo:
    def __init__(self, calls, file_exists):
        self.calls = calls
        self.file_exists = file_exists
        self.calls["automerge"] = []

    def get_branch(self, name):
        self.calls["base_branch"] = name
        return types.SimpleNamespace(commit=types.SimpleNamespace(sha="basesha"))

    def create_git_ref(self, ref, sha):
        self.calls["ref"] = (ref, sha)

    def get_contents(self, path, ref):
        if not self.file_exists:
            raise _FakeException(404)
        return _Contents()

    def update_file(self, **kw):
        self.calls["write"] = ("update", kw)

    def create_file(self, **kw):
        self.calls["write"] = ("create", kw)

    def create_pull(self, **kw):
        self.calls["pull"] = kw
        return _FakePR(42, self.calls["automerge"])


def _install_fake_github(monkeypatch, calls, file_exists=True):
    mod = types.ModuleType("github")

    class _Github:
        def __init__(self, token):
            calls["token"] = token

        def get_repo(self, repo):
            calls["repo"] = repo
            return _FakeRepo(calls, file_exists)

    mod.Github = _Github
    mod.GithubException = _FakeException
    mod.InputGitAuthor = lambda name, email: ("author", name, email)
    monkeypatch.setitem(sys.modules, "github", mod)
    monkeypatch.setattr(gc, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(gc, "GITHUB_REPO", "org/repo")
    monkeypatch.setattr(gc, "GITHUB_BASE_BRANCH", "main")


def test_real_flow_updates_existing_file_and_opens_pr(monkeypatch):
    calls = {}
    _install_fake_github(monkeypatch, calls, file_exists=True)

    pr = gc.create_branch_and_pr(**_KW)

    assert calls["repo"] == "org/repo"
    assert calls["ref"] == ("refs/heads/ai-fix/null-userid-042", "basesha")
    assert calls["write"][0] == "update"                       # ไฟล์มีอยู่ → update
    assert calls["write"][1]["branch"] == "ai-fix/null-userid-042"
    assert calls["pull"]["head"] == "ai-fix/null-userid-042"   # PR จาก branch → main
    assert calls["pull"]["base"] == "main"
    assert pr.number == 42 and pr._obj is not None

    # T1 auto-merge: ผูก rule ไม่ใช่ merge เอง
    gc.enable_auto_merge(pr, merge_method="squash")
    assert calls["automerge"] == ["squash"]


def test_real_flow_creates_file_when_missing(monkeypatch):
    calls = {}
    _install_fake_github(monkeypatch, calls, file_exists=False)
    gc.create_branch_and_pr(**_KW)
    assert calls["write"][0] == "create"                       # ไฟล์ยังไม่มี → create


def test_refuses_branch_equal_to_base(monkeypatch):
    calls = {}
    _install_fake_github(monkeypatch, calls, file_exists=True)
    monkeypatch.setattr(gc, "GITHUB_BASE_BRANCH", "ai-fix/null-userid-042")
    with pytest.raises(RuntimeError, match="base"):
        gc.create_branch_and_pr(**_KW)
