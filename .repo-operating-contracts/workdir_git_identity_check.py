import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
Runner = Callable[[list[str], Path], tuple[int, str, str]]


def run_command(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        creationflags=NO_WINDOW,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _run_git(runner: Runner, cwd: Path, args: list[str]) -> str | None:
    code, stdout, _stderr = runner(["git", *args], cwd)
    if code != 0:
        return None
    return stdout.strip()


def _parse_ahead_behind(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    parts = text.split()
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None


def _parse_status_header(status: str | None) -> tuple[str | None, str | None, int | None, int | None]:
    if not status:
        return None, None, None, None
    header = next((line for line in status.splitlines() if line.startswith("## ")), "")
    if not header:
        return None, None, None, None

    body = header[3:]
    ahead = 0
    behind = 0
    if "[" in body:
        body, flags = body.split("[", 1)
        flags = flags.rstrip("]")
        for part in flags.split(","):
            part = part.strip()
            if part.startswith("ahead "):
                ahead = int(part.removeprefix("ahead "))
            elif part.startswith("behind "):
                behind = int(part.removeprefix("behind "))

    body = body.strip()
    if "..." in body:
        branch, upstream = body.split("...", 1)
        return branch.strip() or None, upstream.strip() or None, ahead, behind
    return body.strip() or None, None, ahead, behind


def _sync_state(upstream: str | None, ahead: int | None, behind: int | None) -> str:
    if not upstream:
        return "no_upstream"
    if ahead is None or behind is None:
        return "unknown"
    if ahead and behind:
        return "diverged"
    if ahead:
        return "ahead"
    if behind:
        return "behind"
    return "synced"


def _remote_repo_matches(remotes: str, expected_owner: str, expected_repo: str) -> bool:
    if not remotes or not expected_repo:
        return False
    owner_prefix = f"{re.escape(expected_owner)}/" if expected_owner else ""
    pattern = rf"(?:[:/]){owner_prefix}{re.escape(expected_repo)}(?:\.git)?(?:\s|$)"
    return re.search(pattern, remotes, flags=re.IGNORECASE) is not None


def check_workdir_identity(
    cwd: Path,
    expected_repo: str,
    expected_owner: str = "",
    expected_root_hint: str = "",
    current_task_goal: str = "",
    allow_dirty: bool = False,
    allow_no_upstream: bool = False,
    runner: Runner = run_command,
) -> dict[str, Any]:
    root = _run_git(runner, cwd, ["rev-parse", "--show-toplevel"])
    status = _run_git(runner, cwd, ["status", "--short", "--branch"])
    remotes = _run_git(runner, cwd, ["remote", "-v"])

    branch, upstream, ahead, behind = _parse_status_header(status)
    if branch is None:
        branch = _run_git(runner, cwd, ["branch", "--show-current"])
    if upstream is None:
        upstream = _run_git(runner, cwd, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream and (ahead is None or behind is None):
        ahead_behind_text = _run_git(runner, cwd, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
        ahead, behind = _parse_ahead_behind(ahead_behind_text)

    sync_state = _sync_state(upstream, ahead, behind)

    root_text = root or ""
    remotes_text = remotes or ""
    repo_match = bool(expected_repo and expected_repo.lower() in root_text.lower()) or _remote_repo_matches(
        remotes_text, expected_owner, expected_repo
    )
    owner_match = not expected_owner or expected_owner.lower() in remotes_text.lower()
    hint_match = not expected_root_hint or expected_root_hint.replace("\\", "/").lower() in root_text.replace("\\", "/").lower()

    if not root:
        repo_identity = "unknown"
    elif repo_match and owner_match and hint_match:
        repo_identity = "matched"
    else:
        repo_identity = "mismatched"

    dirty_lines = []
    if status:
        dirty_lines = [line for line in status.splitlines() if not line.startswith("##")]
    workdir_state = "dirty" if dirty_lines else "clean"

    if repo_identity == "mismatched":
        recommendation = "stop_for_review"
        reason = "想定 repo と実際の作業場所が一致していません。"
    elif repo_identity == "unknown":
        recommendation = "suggest_new_chat"
        reason = "Git repository の同一性を確認できません。"
    elif sync_state == "diverged" or (sync_state == "no_upstream" and not allow_no_upstream):
        recommendation = "stop_for_review"
        reason = "upstream が未設定、または branch が diverged しています。"
    elif workdir_state == "dirty" and not allow_dirty:
        recommendation = "stop_for_review"
        reason = "未整理の dirty state があります。"
    else:
        recommendation = "continue_here"
        reason = "作業場所と Git 同一性は想定内です。"

    return {
        "recommendation": recommendation,
        "repo_identity": repo_identity,
        "workdir_state": workdir_state,
        "sync_state": sync_state,
        "reason": reason,
        "safe_next_action": "書き込み前に main task と changed files を確認する。",
        "skill": "side-route-chat-router",
        "facts": {
            "repo_root": root,
            "branch": branch,
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "current_task_goal": current_task_goal,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="作業ディレクトリと Git repository の同一性を read-only で確認します。")
    parser.add_argument("--expected-repo", required=True)
    parser.add_argument("--expected-owner", default="")
    parser.add_argument("--expected-root-hint", default="")
    parser.add_argument("--current-task-goal", default="")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument(
        "--allow-no-upstream",
        action="store_true",
        help="新規local branchであることを確認済みの場合だけno_upstreamを許可します。",
    )
    parser.add_argument("--cwd", default=".")
    args = parser.parse_args()

    result = check_workdir_identity(
        cwd=Path(args.cwd).resolve(),
        expected_repo=args.expected_repo,
        expected_owner=args.expected_owner,
        expected_root_hint=args.expected_root_hint,
        current_task_goal=args.current_task_goal,
        allow_dirty=args.allow_dirty,
        allow_no_upstream=args.allow_no_upstream,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
