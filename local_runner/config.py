from pathlib import Path

ALLOWED_ROOT = Path(r"D:\软件\CursorProjects")
POLL_SECONDS = 60
MAX_OUTPUT_CHARS = 64_000
MAX_READ_BYTES = 256_000
TASK_VERSION = "PENNY_TASK_V1"
RESULT_VERSION = "PENNY_RESULT_V1"
ALLOWED_ACTIONS = frozenset(
    {
        "git_status",
        "git_diff",
        "git_log",
        "list_files",
        "read_text_file",
        "search_text",
        "run_existing_test",
    }
)
ACTION_ARGUMENT_KEYS = {
    "git_status": frozenset(),
    "git_diff": frozenset({"path"}),
    "git_log": frozenset({"max_count"}),
    "list_files": frozenset({"path"}),
    "read_text_file": frozenset({"path"}),
    "search_text": frozenset({"query", "path"}),
    "run_existing_test": frozenset({"test_file"}),
}
# Exact workspace-relative paths. Filename patterns are not permission.
ALLOWED_TEST_FILES = frozenset(
    {
        "local_runner/test_security.py",
    }
)
