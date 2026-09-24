import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import (
    ACTION_ARGUMENT_KEYS,
    ALLOWED_ACTIONS,
    ALLOWED_ROOT,
    MAX_OUTPUT_CHARS,
    MAX_READ_BYTES,
    RESULT_VERSION,
    TASK_VERSION,
)

SECRET_NAME = re.compile(
    r"(?i)^(\.env|\.env\..+|\.npmrc|\.git-credentials|id_rsa|id_dsa|credentials\.json)$"
    r"|credential|secret|password|cookie|\.pem$|\.key$|\.pfx$|\.p12$"
)
SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|authorization|bearer|cookie)\s*[:=]\s*\S+"
    r"|\b(?:ghp_|gho_|github_pat_|sk-|AKIA|xox[baprs]-)[A-Za-z0-9_\-]{8,}"
)
PY_TEST = re.compile(r"(?i)(^test_.+\.py|.+_test\.py)$")
JS_TEST = re.compile(r"(?i)\.test\.(js|mjs)$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def redact(text: str) -> str:
    cleaned = SECRET_VALUE.sub("[REDACTED]", text or "")
    if len(cleaned) > MAX_OUTPUT_CHARS:
        return cleaned[:MAX_OUTPUT_CHARS] + "\n[truncated]\n"
    return cleaned


def is_secret_path(path: Path) -> bool:
    return any(SECRET_NAME.search(part) for part in path.parts)


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def resolve_workspace(workspace, allowed_root: Path):
    if not isinstance(workspace, str) or workspace != Path(workspace).name:
        return None, "PATH_OUTSIDE_ALLOWED_ROOT"
    if workspace in {"", ".", ".."} or any(sep in workspace for sep in ("/", "\\", ":")):
        return None, "PATH_OUTSIDE_ALLOWED_ROOT"
    root = allowed_root.resolve()
    candidate = (root / workspace).resolve()
    if not is_within(candidate, root) or os.path.normcase(str(candidate.parent)) != os.path.normcase(str(root)):
        return None, "PATH_OUTSIDE_ALLOWED_ROOT"
    if not candidate.is_dir():
        return None, "WORKSPACE_NOT_FOUND"
    return candidate, None


def resolve_relative(workspace: Path, relative, reason_if_missing: str | None = None):
    if not isinstance(relative, str) or relative.strip() == "":
        return None, "PATH_OUTSIDE_ALLOWED_ROOT"
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        return None, "PATH_OUTSIDE_ALLOWED_ROOT"
    candidate = (workspace / raw).resolve()
    if not is_within(candidate, workspace):
        return None, "PATH_OUTSIDE_ALLOWED_ROOT"
    if is_secret_path(candidate):
        return None, "SECRET_PATH_BLOCKED"
    if reason_if_missing and not candidate.exists():
        return None, reason_if_missing
    return candidate, None


def result(task, status, started, exit_code, stdout="", stderr="", reason=None):
    body = {
        "version": RESULT_VERSION,
        "task_id": str(task.get("task_id", "")),
        "action": str(task.get("action", "")),
        "workspace": str(task.get("workspace", "")),
        "started_at": started,
        "finished_at": utc_now(),
        "exit_code": exit_code,
        "status": status,
        "stdout": redact(stdout),
        "stderr": redact(stderr),
    }
    if reason:
        body["reason"] = reason
    return body


def blocked(task, started, reason):
    return result(task, "BLOCKED", started, 1, stderr="", reason=reason)


def run_argv(argv, cwd: Path, timeout: int):
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


def git_status_argv():
    return ["git", "-c", "core.pager=cat", "status", "--short"]


def validate_arguments(action, arguments):
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return None, "INVALID_ARGUMENTS"
    unknown = set(arguments) - ACTION_ARGUMENT_KEYS[action]
    if unknown:
        return None, "INVALID_ARGUMENTS"
    return arguments, None


def execute_action(task: dict, allowed_root: Path = ALLOWED_ROOT) -> dict:
    started = utc_now()
    if not isinstance(task, dict):
        return blocked({"task_id": "", "action": "", "workspace": ""}, started, "INVALID_TASK")
    if task.get("version") != TASK_VERSION or task.get("status") != "pending":
        return blocked(task, started, "INVALID_TASK")
    action = task.get("action")
    if action not in ALLOWED_ACTIONS:
        return blocked(task, started, "ACTION_NOT_ALLOWLISTED")
    arguments, arg_error = validate_arguments(action, task.get("arguments", {}))
    if arg_error:
        return blocked(task, started, arg_error)
    workspace, path_error = resolve_workspace(task.get("workspace"), allowed_root)
    if path_error == "PATH_OUTSIDE_ALLOWED_ROOT":
        return blocked(task, started, "PATH_OUTSIDE_ALLOWED_ROOT")
    if path_error:
        return result(task, "FAIL", started, 1, stderr=path_error)
    try:
        return _dispatch(task, workspace, arguments, started)
    except subprocess.TimeoutExpired:
        return result(task, "FAIL", started, 1, stderr="TIMEOUT")
    except OSError:
        return result(task, "FAIL", started, 1, stderr="OS_ERROR")


def _dispatch(task, workspace: Path, arguments: dict, started: str):
    action = task["action"]
    if action == "git_status":
        code, out, err = run_argv(git_status_argv(), workspace, 60)
        return result(task, "PASS" if code == 0 else "FAIL", started, code, out, err)
    if action == "git_diff":
        argv = ["git", "-c", "core.pager=cat", "diff", "--no-ext-diff", "--"]
        if "path" in arguments:
            target, error = resolve_relative(workspace, arguments["path"])
            if error:
                return blocked(task, started, error)
            argv.append(str(target))
        code, out, err = run_argv(argv, workspace, 60)
        return result(task, "PASS" if code == 0 else "FAIL", started, code, out, err)
    if action == "git_log":
        count = arguments.get("max_count", 20)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 50:
            return blocked(task, started, "INVALID_ARGUMENTS")
        argv = ["git", "-c", "core.pager=cat", "log", "-n", str(count), "--oneline", "--"]
        code, out, err = run_argv(argv, workspace, 60)
        return result(task, "PASS" if code == 0 else "FAIL", started, code, out, err)
    if action == "list_files":
        return _list_files(task, workspace, arguments, started)
    if action == "read_text_file":
        return _read_text(task, workspace, arguments, started)
    if action == "search_text":
        return _search_text(task, workspace, arguments, started)
    if action == "run_existing_test":
        return _run_existing_test(task, workspace, arguments, started)
    return blocked(task, started, "ACTION_NOT_ALLOWLISTED")


def _list_files(task, workspace: Path, arguments: dict, started: str):
    relative = arguments.get("path", ".")
    target, error = resolve_relative(workspace, relative)
    if error:
        return blocked(task, started, error)
    if not target.is_dir():
        return result(task, "FAIL", started, 1, stderr="NOT_A_DIRECTORY")
    names = []
    for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
        if is_secret_path(child):
            continue
        names.append(child.name + ("/" if child.is_dir() else ""))
        if len(names) >= 500:
            break
    return result(task, "PASS", started, 0, stdout="\n".join(names))


def _read_text(task, workspace: Path, arguments: dict, started: str):
    target, error = resolve_relative(workspace, arguments.get("path"), "FILE_NOT_FOUND")
    if error:
        status = "BLOCKED" if error in {"PATH_OUTSIDE_ALLOWED_ROOT", "SECRET_PATH_BLOCKED"} else "FAIL"
        if status == "BLOCKED":
            return blocked(task, started, error)
        return result(task, "FAIL", started, 1, stderr=error)
    if not target.is_file():
        return result(task, "FAIL", started, 1, stderr="NOT_A_FILE")
    if target.stat().st_size > MAX_READ_BYTES:
        return result(task, "FAIL", started, 1, stderr="FILE_TOO_LARGE")
    data = target.read_bytes()
    if b"\x00" in data:
        return result(task, "FAIL", started, 1, stderr="BINARY_FILE")
    return result(task, "PASS", started, 0, stdout=data.decode("utf-8", errors="replace"))


def _search_text(task, workspace: Path, arguments: dict, started: str):
    query = arguments.get("query")
    if not isinstance(query, str) or not 1 <= len(query) <= 200:
        return blocked(task, started, "INVALID_ARGUMENTS")
    relative = arguments.get("path", ".")
    target, error = resolve_relative(workspace, relative)
    if error:
        return blocked(task, started, error)
    if not target.exists():
        return result(task, "FAIL", started, 1, stderr="PATH_NOT_FOUND")
    files = [target] if target.is_file() else []
    if target.is_dir():
        seen = 0
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [
                name
                for name in dirnames
                if name != ".git" and not is_secret_path(Path(dirpath) / name)
            ]
            for name in filenames:
                seen += 1
                if seen > 2000:
                    break
                candidate = Path(dirpath) / name
                if is_secret_path(candidate) or not is_within(candidate, workspace):
                    continue
                files.append(candidate)
            if seen > 2000:
                break
    matches = []
    for file_path in files:
        if not file_path.is_file() or file_path.stat().st_size > MAX_READ_BYTES:
            continue
        data = file_path.read_bytes()
        if b"\x00" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if query in line:
                relative_name = file_path.resolve().relative_to(workspace.resolve()).as_posix()
                matches.append(f"{relative_name}:{line_no}:{line[:400]}")
                if len(matches) >= 50:
                    return result(task, "PASS", started, 0, stdout="\n".join(matches))
    return result(task, "PASS", started, 0, stdout="\n".join(matches))


def _run_existing_test(task, workspace: Path, arguments: dict, started: str):
    target, error = resolve_relative(workspace, arguments.get("test_file"), "TEST_FILE_NOT_FOUND")
    if error:
        status = "BLOCKED" if error in {"PATH_OUTSIDE_ALLOWED_ROOT", "SECRET_PATH_BLOCKED"} else "FAIL"
        if status == "BLOCKED":
            return blocked(task, started, error)
        return result(task, "FAIL", started, 1, stderr=error)
    if not target.is_file():
        return result(task, "FAIL", started, 1, stderr="TEST_FILE_NOT_FOUND")
    name = target.name
    if PY_TEST.search(name):
        argv = [sys.executable, "-m", "unittest", str(target)]
    elif JS_TEST.search(name):
        node = shutil_which_node()
        if not node:
            return result(task, "FAIL", started, 1, stderr="TEST_RUNNER_UNSUPPORTED")
        argv = [node, "--test", str(target)]
    else:
        return result(task, "FAIL", started, 1, stderr="TEST_RUNNER_UNSUPPORTED")
    code, out, err = run_argv(argv, workspace, 180)
    return result(task, "PASS" if code == 0 else "FAIL", started, code, out, err)


def shutil_which_node():
    from shutil import which

    return which("node")
