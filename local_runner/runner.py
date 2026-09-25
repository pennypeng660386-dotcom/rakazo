import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from actions import execute_action
from config import ALLOWED_ROOT, POLL_SECONDS

REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_ID_OK = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def log_line(
    repo_root: Path,
    task_id: str,
    action: str,
    workspace: str,
    status: str,
    started: str = "",
    finished: str = "",
) -> None:
    path = repo_root / "logs" / "local_runner.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    finished = finished or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    started = started or finished
    line = (
        f"{finished} task_id={task_id} action={action} workspace={workspace} "
        f"started={started} finished={finished} status={status}\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def git(repo_root: Path, args: list[str], timeout: int = 120):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        timeout=timeout,
        env=env,
    )


def ensure_layout(repo_root: Path) -> None:
    for name in ("inbox", "processing", "results", "failed"):
        (repo_root / "runtime_tasks" / name).mkdir(parents=True, exist_ok=True)


def result_path(repo_root: Path, task_id: str) -> Path:
    return repo_root / "runtime_tasks" / "results" / f"{task_id}.json"


def write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_task(path: Path):
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    return body


def dispatch_file(repo_root: Path, task_path: Path, allowed_root: Path = ALLOWED_ROOT) -> str:
    ensure_layout(repo_root)
    failed_dir = repo_root / "runtime_tasks" / "failed"
    processing_dir = repo_root / "runtime_tasks" / "processing"
    task = load_task(task_path)
    file_id = task_path.stem
    if task is None or not TASK_ID_OK.match(file_id) or task.get("task_id") != file_id:
        claimed = failed_dir / task_path.name
        os.replace(task_path, claimed)
        log_line(repo_root, file_id, "", "", "BLOCKED")
        return "BLOCKED"
    task_id = task["task_id"]
    action = str(task.get("action", ""))
    workspace = str(task.get("workspace", ""))
    if result_path(repo_root, task_id).is_file():
        os.replace(task_path, failed_dir / f"{task_id}.duplicate.json")
        log_line(repo_root, task_id, action, workspace, "SKIP_DUPLICATE")
        return "SKIP_DUPLICATE"
    claimed = processing_dir / f"{task_id}.json"
    os.replace(task_path, claimed)
    outcome = execute_action(task, allowed_root)
    write_json(result_path(repo_root, task_id), outcome)
    status = str(outcome.get("status", "FAIL"))
    if status == "PASS":
        claimed.unlink(missing_ok=True)
    else:
        os.replace(claimed, failed_dir / f"{task_id}.json")
    log_line(
        repo_root,
        task_id,
        action,
        workspace,
        status,
        str(outcome.get("started_at", "")),
        str(outcome.get("finished_at", "")),
    )
    return status


def pull_branch(repo_root: Path) -> bool:
    branch = git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch.returncode != 0:
        return False
    name = branch.stdout.strip()
    if not name or name == "HEAD":
        return False
    for attempt in range(2):
        pulled = git(repo_root, ["pull", "--ff-only", "--no-edit", "origin", name])
        if pulled.returncode == 0:
            return True
        message = f"{pulled.stderr or ''} {pulled.stdout or ''}".lower()
        transient = any(token in message for token in ("reset", "timed out", "unable to access", "connection"))
        if attempt == 0 and transient:
            time.sleep(2)
            continue
        return False
    return False


def commit_runtime_tasks(repo_root: Path) -> bool:
    added = git(repo_root, ["add", "--", "runtime_tasks"])
    if added.returncode != 0:
        return False
    staged = git(repo_root, ["diff", "--cached", "--name-only"])
    if staged.returncode != 0:
        return False
    names = [line.strip().replace("\\", "/") for line in staged.stdout.splitlines() if line.strip()]
    if not names:
        return True
    if any(not name.startswith("runtime_tasks/") for name in names):
        return False
    committed = git(repo_root, ["commit", "-m", "Record Penny local bridge task results."])
    return committed.returncode == 0


def push_branch(repo_root: Path) -> bool:
    pushed = git(repo_root, ["push", "origin", "HEAD"])
    return pushed.returncode == 0


def run_once(repo_root: Path = REPO_ROOT) -> int:
    ensure_layout(repo_root)
    if not pull_branch(repo_root):
        log_line(repo_root, "-", "-", "-", "PULL_FAIL")
        return 1
    inbox = repo_root / "runtime_tasks" / "inbox"
    for task_path in sorted(inbox.glob("*.json")):
        dispatch_file(repo_root, task_path)
    if not commit_runtime_tasks(repo_root):
        log_line(repo_root, "-", "-", "-", "COMMIT_FAIL")
        return 1
    if not push_branch(repo_root):
        log_line(repo_root, "-", "-", "-", "PUSH_FAIL")
        return 1
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Penny local GitHub task bridge")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--watch", action="store_true")
    args = parser.parse_args(argv)
    if args.once:
        return run_once()
    while True:
        run_once()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
