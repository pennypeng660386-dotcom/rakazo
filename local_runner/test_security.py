import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import actions
import runner


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.allowed = self.root / "projects"
        self.workspace = self.allowed / "sample-workspace"
        self.workspace.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=self.workspace, check=True, capture_output=True)
        (self.workspace / "notes.txt").write_text("hello bridge\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, action, arguments=None, workspace="sample-workspace"):
        return {
            "version": "PENNY_TASK_V1",
            "task_id": "task-1",
            "created_at": "2026-09-24T00:00:00Z",
            "action": action,
            "workspace": workspace,
            "arguments": {} if arguments is None else arguments,
            "status": "pending",
        }

    def test_sources_have_no_arbitrary_shell(self):
        for name in ("actions.py", "runner.py", "config.py"):
            text = (Path(__file__).resolve().parent / name).read_text(encoding="utf-8")
            self.assertNotIn("shell=True", text)
            self.assertNotIn("os.system", text)
            self.assertNotIn("subprocess.Popen", text)

    def test_git_status_argv_is_fixed(self):
        self.assertEqual(
            actions.git_status_argv(),
            ["git", "-c", "core.pager=cat", "status", "--short"],
        )

    def test_unknown_action_blocked(self):
        outcome = actions.execute_action(self.task("run_command", {"command": "whoami"}), self.allowed)
        self.assertEqual(outcome["status"], "BLOCKED")
        self.assertEqual(outcome["reason"], "ACTION_NOT_ALLOWLISTED")

    def test_path_outside_root_blocked(self):
        outcome = actions.execute_action(self.task("git_status", workspace=r"..\Windows"), self.allowed)
        self.assertEqual(outcome["status"], "BLOCKED")
        self.assertEqual(outcome["reason"], "PATH_OUTSIDE_ALLOWED_ROOT")

    def test_env_and_secret_reads_blocked(self):
        (self.workspace / ".env").write_text("API_KEY=supersecretvalue\n", encoding="utf-8")
        (self.workspace / "credentials.json").write_text('{"token":"abc"}\n', encoding="utf-8")
        for relative in (".env", "credentials.json", r"..\..\Windows\win.ini"):
            outcome = actions.execute_action(
                self.task("read_text_file", {"path": relative}),
                self.allowed,
            )
            blob = json.dumps(outcome)
            self.assertEqual(outcome["status"], "BLOCKED")
            self.assertNotIn("supersecretvalue", blob)
            self.assertNotIn("abc", blob)

    def test_git_status_passes_inside_workspace(self):
        outcome = actions.execute_action(self.task("git_status"), self.allowed)
        self.assertEqual(outcome["status"], "PASS")
        self.assertEqual(outcome["exit_code"], 0)

    def test_duplicate_task_does_not_execute_again(self):
        repo = self.root / "repo"
        task_id = "task-1"
        inbox = repo / "runtime_tasks" / "inbox"
        inbox.mkdir(parents=True)
        task_path = inbox / f"{task_id}.json"
        task_path.write_text(json.dumps(self.task("git_status")), encoding="utf-8")
        runner.write_json(
            runner.result_path(repo, task_id),
            {"version": "PENNY_RESULT_V1", "task_id": task_id, "status": "PASS"},
        )
        calls = []
        original = runner.execute_action
        runner.execute_action = lambda *args, **kwargs: calls.append(args)
        try:
            status = runner.dispatch_file(repo, task_path, self.allowed)
        finally:
            runner.execute_action = original
        self.assertEqual(status, "SKIP_DUPLICATE")
        self.assertEqual(calls, [])
        self.assertTrue(runner.result_path(repo, task_id).is_file())


if __name__ == "__main__":
    unittest.main()
