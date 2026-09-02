import base64
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
APP_PATH = REPO_DIR / "review" / "app.py"


def load_review_app():
    spec = importlib.util.spec_from_file_location("review_app", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review = load_review_app()


class ReviewWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        data = Path(self.temporary.name)
        review.DATA_DIR = data
        review.REVIEWS_DIR = data / "reviews"
        review.WORKSPACES_DIR = data / "workspaces"
        review.TRASH_DIR = data / "_trash"
        for directory in (review.REVIEWS_DIR, review.WORKSPACES_DIR, review.TRASH_DIR):
            directory.mkdir(parents=True)

        self.repo = data / "source"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.email", "review@example.invalid")
        self.git("config", "user.name", "Review Test")
        (self.repo / "safe.txt").write_text("one\ntwo\n", encoding="utf-8")
        (self.repo / ".env.local").write_text("TOKEN=must-not-leak\n", encoding="utf-8")
        self.git("add", "safe.txt", ".env.local")
        self.git("commit", "--quiet", "-m", "base")
        self.base_sha = self.git("rev-parse", "HEAD").strip()
        (self.repo / "safe.txt").write_text("one\nTWO\nthree\n", encoding="utf-8")
        (self.repo / ".env.local").write_text("TOKEN=changed-secret\n", encoding="utf-8")
        self.git("add", "safe.txt", ".env.local")
        self.git("commit", "--quiet", "-m", "change")
        self.head_sha = self.git("rev-parse", "HEAD").strip()

        record = review.create_review(
            {
                "project": "group/app",
                "merge_request_iid": 42,
                "expected_head_sha": self.head_sha,
            }
        )
        self.review_id = record["review_id"]
        self.workspace_id = "ws_" + "b" * 32
        workspace = review.workspace_dir(self.workspace_id)
        workspace.mkdir()
        shutil.copytree(self.repo, workspace / "repo", symlinks=True)
        meta = {
            "workspace_id": self.workspace_id,
            "review_id": self.review_id,
            "project": "group/app",
            "merge_request_iid": 42,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "target_branch": "main",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds"),
            "history_truncated": False,
            "fetch_depth": 50,
            "size_bytes": 1,
        }
        review.write_json(workspace / "meta.json", meta)
        record = review.load_review(self.review_id)
        record["workspace_id"] = self.workspace_id
        record["workspace_status"] = "active"
        review.save_review(record)

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *arguments):
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return completed.stdout

    def test_workspace_diff_computes_anchorable_line_numbers_and_coverage(self):
        payload = review.workspace_diff(
            self.workspace_id,
            {"paths": ["safe.txt"], "max_lines_per_file": ["400"], "max_total_lines": ["4000"]},
        )

        self.assertEqual(payload["head_sha"], self.head_sha)
        self.assertTrue(payload["coverage"]["complete"])
        lines = payload["files"][0]["lines"]
        self.assertIn(["del", 2, None, "two"], lines)
        self.assertIn(["add", None, 2, "TWO"], lines)
        self.assertIn(["add", None, 3, "three"], lines)

    def test_secret_files_are_withheld_server_side(self):
        payload = review.read_file(
            self.workspace_id,
            {"path": [".env.local"], "start_line": ["1"], "max_lines": ["100"]},
        )

        self.assertTrue(payload["content_withheld"])
        self.assertNotIn("TOKEN", json.dumps(payload))

    def test_secret_content_is_withheld_from_search_and_diff_too(self):
        search = review.search_workspace(
            self.workspace_id,
            {"q": ["TOKEN"], "path": [""], "max_matches": ["20"]},
        )
        diff = review.workspace_diff(
            self.workspace_id,
            {"paths": [".env.local"], "max_lines_per_file": ["400"], "max_total_lines": ["4000"]},
        )

        self.assertEqual(search["matches"], [])
        self.assertGreater(search["content_withheld_matches"], 0)
        self.assertTrue(diff["files"][0]["content_withheld"])
        self.assertFalse(diff["coverage"]["complete"])
        self.assertNotIn("changed-secret", json.dumps(diff))

    def test_search_and_file_lines_are_context_only_payloads(self):
        search = review.search_workspace(
            self.workspace_id,
            {"q": ["TWO"], "path": ["safe.txt"], "max_matches": ["20"]},
        )
        file_payload = review.read_file(
            self.workspace_id,
            {"path": ["safe.txt"], "start_line": ["2"], "max_lines": ["2"]},
        )

        self.assertEqual(search["matches"][0]["line"], 2)
        self.assertEqual(file_payload["lines"], [[2, "TWO"], [3, "three"]])
        coverage = review.load_review(self.review_id)["coverage"]
        self.assertEqual(coverage["workspace_calls_used"], 2)
        self.assertEqual(coverage["searches_used"], 1)

    def test_record_close_discards_owned_workspace(self):
        result = review.close_review(self.review_id)

        self.assertTrue(result["workspace_discarded"])
        self.assertFalse(review.workspace_dir(self.workspace_id).exists())
        self.assertEqual(review.load_review(self.review_id)["status"], "closed")

    def test_ttl_reaper_discards_workspace_when_manager_cleanup_is_missing(self):
        directory = review.workspace_dir(self.workspace_id)
        meta = review.read_json(directory / "meta.json", "workspace not found")
        meta["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
        review.write_json(directory / "meta.json", meta)

        review.cleanup_once()

        self.assertFalse(directory.exists())
        self.assertEqual(review.load_review(self.review_id)["workspace_status"], "expired")

    def test_event_denylist_rejects_credentials_and_raw_payloads(self):
        with self.assertRaisesRegex(review.ServiceError, "credential"):
            review.append_event(self.review_id, "analyst", "Authorization: Bearer secret")
        with self.assertRaisesRegex(review.ServiceError, "raw payload"):
            review.append_event(self.review_id, "analyst", '{"files": 2}')

    def test_server_enforces_workspace_call_budget(self):
        record = review.load_review(self.review_id)
        record["workspace_calls"] = review.MAX_WORKSPACE_CALLS
        review.save_review(record)

        with self.assertRaisesRegex(review.ServiceError, "budget exhausted"):
            review.list_files(self.workspace_id, {"max_entries": ["10"]})

    def test_git_transport_uses_basic_auth_without_exposing_raw_token(self):
        original_token = review.GITLAB_WORKSPACE_TOKEN
        review.GITLAB_WORKSPACE_TOKEN = "test-read-repository-token"
        try:
            header = review.git_authorization_header()
        finally:
            review.GITLAB_WORKSPACE_TOKEN = original_token

        expected = base64.b64encode(b"oauth2:test-read-repository-token").decode()
        self.assertEqual(header, f"Authorization: Basic {expected}")
        self.assertNotIn("PRIVATE-TOKEN", header)
        self.assertNotIn("test-read-repository-token", header)


if __name__ == "__main__":
    unittest.main()
