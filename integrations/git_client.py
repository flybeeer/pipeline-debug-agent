"""
integrations/git_client.py — GitOps layer
=========================================
สร้าง branch + เปิด PR ผ่าน GitHub API ด้วย service account ที่จำกัดสิทธิ์

🚨 กฎเหล็ก:
  • service account นี้ต้องมีสิทธิ์แค่: สร้าง branch + เปิด PR
    ห้ามมีสิทธิ์ push main หรือ merge เอง (auto-merge ทำผ่าน GitHub rule + CI เท่านั้น)
  • commit ต้องใช้ identity ของ agent — ห้ามปลอมเป็นมนุษย์ (กฎเหล็ก #6)
  • commit message มี trailer Agent-Run-Id / Trace เพื่อ audit

โหมดการทำงาน (เลือกอัตโนมัติจาก env):
  • ตั้ง GITHUB_TOKEN + GITHUB_REPO → เปิด PR จริงผ่าน GitHub API (PyGithub)
  • ไม่ตั้ง                          → stub: พิมพ์เฉยๆ (ให้ demo/test รัน offline ได้)

ติดตั้ง dependency สำหรับโหมดจริง:  pip install -e .[github]
"""

import os
from dataclasses import dataclass, field
from typing import Any

# ── identity ของ agent — ห้ามปลอมเป็นมนุษย์ (override ได้ผ่าน env) ──
AGENT_GIT_NAME = os.environ.get("AGENT_GIT_NAME", "debug-agent")
AGENT_GIT_EMAIL = os.environ.get("AGENT_GIT_EMAIL", "agent@company.com")
AGENT_AUTHOR = f"{AGENT_GIT_NAME} <{AGENT_GIT_EMAIL}>"

# ── การตั้งค่า GitHub (service account จำกัดสิทธิ์) ──
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")              # รูปแบบ "org/repo"
GITHUB_BASE_BRANCH = os.environ.get("GITHUB_BASE_BRANCH", "main")


@dataclass
class PullRequest:
    url: str
    branch: str
    number: int | None = None
    # อ้างถึง PR object จริงของ PyGithub (ไว้ enable auto-merge ต่อ) — None ตอน stub
    _obj: Any = field(default=None, repr=False)


def _is_configured() -> bool:
    """ตั้งครบไหมว่าจะยิง GitHub จริง — ถ้าไม่ครบใช้ stub (กันเผลอยิงตอน test/demo)"""
    return bool(GITHUB_TOKEN and GITHUB_REPO)


def _build_messages(
    issue_id: str, diagnosis: str, error_log: str, test_result: str,
    run_id: str, trace_url: str,
) -> tuple[str, str, str]:
    """ประกอบ commit message + PR title + PR body ให้ตรง convention (audit ครบ)"""
    summary = (diagnosis or "fix pipeline").strip().splitlines()[0][:50]

    commit_message = (
        f"fix: {summary}\n\n"
        f"Agent-Run-Id: {run_id}\n"
        f"Trace: {trace_url or 'n/a'}"
    )
    pr_title = f"[AI fix] {issue_id}: {summary}"
    pr_body = (
        f"## 🐛 อาการที่ fail\n```\n{error_log}\n```\n\n"
        f"## 🔍 สาเหตุ (AI วิเคราะห์)\n{diagnosis}\n\n"
        f"## ✅ ผล test (staging)\n{test_result}\n\n"
        f"## 📎 Audit\nRun-Id: `{run_id}` | [Trace]({trace_url or '#'})\n\n"
        f"> ⚠️ โค้ดนี้ AI ({AGENT_AUTHOR}) เป็นคนแก้ — กรุณา review ก่อน merge"
    )
    return commit_message, pr_title, pr_body


def create_branch_and_pr(
    issue_id: str,
    file_path: str,
    new_code: str,
    diagnosis: str,
    error_log: str,
    test_result: str,
    run_id: str,
    trace_url: str = "",
    draft: bool = False,
) -> PullRequest:
    """สร้าง branch ใหม่ commit fix แล้วเปิด PR พร้อม context ครบสำหรับ review"""
    branch = f"ai-fix/{issue_id}"
    commit_message, pr_title, pr_body = _build_messages(
        issue_id, diagnosis, error_log, test_result, run_id, trace_url
    )

    # ── โหมด stub (offline / ยังไม่ตั้ง GitHub) ──
    if not _is_configured():
        print(f"📝 [stub] เปิด {'draft ' if draft else ''}PR จาก branch '{branch}' "
              f"แก้ไฟล์ '{file_path}' (ตั้ง GITHUB_TOKEN+GITHUB_REPO เพื่อเปิดจริง)")
        fake_url = f"https://github.com/your-org/your-repo/pull/0?branch={branch}"
        return PullRequest(url=fake_url, branch=branch)

    # ── โหมดจริง: เปิด PR ผ่าน GitHub API ──
    try:
        from github import Github, GithubException, InputGitAuthor
    except ImportError as e:
        raise RuntimeError(
            "ต้องติดตั้ง PyGithub ก่อนเปิด PR จริง: pip install -e .[github]"
        ) from e

    if branch == GITHUB_BASE_BRANCH:
        # กฎเหล็ก #1: ห้าม push base ตรงๆ — ต้องผ่าน branch + PR เสมอ
        raise RuntimeError(f"ปฏิเสธ: branch ห้ามเท่ากับ base ('{GITHUB_BASE_BRANCH}')")

    repo = Github(GITHUB_TOKEN).get_repo(GITHUB_REPO)
    author = InputGitAuthor(AGENT_GIT_NAME, AGENT_GIT_EMAIL)

    # 1) สร้าง branch จาก HEAD ของ base (ถ้ามีอยู่แล้วใช้ซ้ำ — rerun ของ issue เดิม)
    base_sha = repo.get_branch(GITHUB_BASE_BRANCH).commit.sha
    try:
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)
    except GithubException as e:
        if e.status != 422:   # 422 = ref มีอยู่แล้ว → reuse
            raise

    # 2) commit ไฟล์ที่แก้ลง branch ด้วย identity ของ agent (create ถ้ายังไม่มีไฟล์)
    try:
        existing = repo.get_contents(file_path, ref=branch)
        repo.update_file(
            path=file_path, message=commit_message, content=new_code,
            sha=existing.sha, branch=branch, author=author, committer=author,
        )
    except GithubException as e:
        if e.status != 404:
            raise
        repo.create_file(
            path=file_path, message=commit_message, content=new_code,
            branch=branch, author=author, committer=author,
        )

    # 3) เปิด PR (agent ทำได้แค่นี้ — merge เป็นเรื่องของคน/auto-merge rule)
    pr = repo.create_pull(
        title=pr_title, body=pr_body, head=branch, base=GITHUB_BASE_BRANCH, draft=draft,
    )
    print(f"📝 เปิด {'draft ' if draft else ''}PR #{pr.number}: {pr.html_url}")
    return PullRequest(url=pr.html_url, branch=branch, number=pr.number, _obj=pr)


def enable_auto_merge(pr: PullRequest, merge_method: str = "squash") -> None:
    """ตั้ง auto-merge — GitHub จะ merge เองเมื่อ CI ผ่านครบเท่านั้น (ใช้กับ T1)

    ⚠️ นี่ไม่ใช่การ merge เอง — แค่ผูก rule ให้ GitHub merge เมื่อ required check เขียว
    """
    if pr._obj is None:
        print(f"⚙️  [stub] ตั้ง auto-merge (รอ CI เขียว): {pr.url}")
        return
    pr._obj.enable_automerge(merge_method=merge_method)
    print(f"⚙️  ตั้ง auto-merge ({merge_method}, รอ CI เขียว): {pr.url}")
