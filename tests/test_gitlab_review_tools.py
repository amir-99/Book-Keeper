import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch


TOOL_DIR = Path(__file__).resolve().parents[1] / "letta-assets" / "tools"
GITLAB_ENV = {
    "GITLAB_BASE_URL": "https://gitlab.example.test",
    "GITLAB_ACCESS_TOKEN": "test-token",
}
JIRA_ENV = {
    "JIRA_BASE_URL": "https://jira.example.test",
    "JIRA_ACCESS_TOKEN": "test-token",
    "JIRA_AUTH_MODE": "bearer",
    "CONFLUENCE_BASE_URL": "https://confluence.example.test",
}
CONFLUENCE_ENV = {
    "CONFLUENCE_BASE_URL": "https://confluence.example.test",
    "CONFLUENCE_ACCESS_TOKEN": "test-token",
    "CONFLUENCE_AUTH_MODE": "bearer",
}


def load_tool(name):
    path = TOOL_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.body = b"" if payload is None else json.dumps(payload).encode()
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


def call(tool_name, env, payloads, *args, **kwargs):
    """Run a tool against a fixed sequence of upstream responses."""
    tool = load_tool(tool_name)
    responses = [FakeResponse(p) if not isinstance(p, FakeResponse) else p for p in payloads]
    with patch.dict(os.environ, env, clear=False):
        with patch("urllib.request.urlopen", side_effect=responses) as mocked:
            result = tool(*args, **kwargs)
    return result, [c.args[0] for c in mocked.call_args_list]


MERGE_REQUEST = {
    "project_id": 7,
    "title": "Add rate limiting",
    "state": "opened",
    "draft": False,
    "source_branch": "SEN-206-rate-limit",
    "target_branch": "main",
    "web_url": "https://gitlab.example.test/group/app/-/merge_requests/42",
    "diff_refs": {"base_sha": "base1", "head_sha": "head1", "start_sha": "start1"},
}


class ReviewDiffTests(unittest.TestCase):
    def test_line_numbers_are_resolved_per_hunk(self):
        patch_text = (
            "@@ -10,4 +10,5 @@ def existing():\n"
            " ctx1\n"
            "-old_line\n"
            "+new_line_a\n"
            "+new_line_b\n"
            " ctx2\n"
            "@@ -50,2 +51,2 @@\n"
            " ctxA\n"
            "+added\n"
        )
        result, _ = call(
            "gitlab_get_merge_request_review_diffs",
            GITLAB_ENV,
            [MERGE_REQUEST, [{"old_path": "a.py", "new_path": "a.py", "diff": patch_text}]],
            "group/app",
            42,
        )
        payload = json.loads(result)
        self.assertEqual(payload["diff_refs"], {"base_sha": "base1", "head_sha": "head1", "start_sha": "start1"})
        self.assertEqual(
            payload["files"][0]["lines"],
            [
                ["ctx", 10, 10, "ctx1"],
                ["del", 11, None, "old_line"],
                ["add", None, 11, "new_line_a"],
                ["add", None, 12, "new_line_b"],
                ["ctx", 12, 13, "ctx2"],
                ["ctx", 50, 51, "ctxA"],
                ["add", None, 52, "added"],
            ],
        )

    def test_trailing_newline_does_not_add_a_phantom_line(self):
        result, _ = call(
            "gitlab_get_merge_request_review_diffs",
            GITLAB_ENV,
            [MERGE_REQUEST, [{"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1,1 +1,1 @@\n+only\n"}]],
            "group/app",
            42,
        )
        self.assertEqual(json.loads(result)["files"][0]["lines"], [["add", None, 1, "only"]])

    def test_new_file_hunk_starting_at_zero_is_kept(self):
        result, _ = call(
            "gitlab_get_merge_request_review_diffs",
            GITLAB_ENV,
            [MERGE_REQUEST, [{"old_path": "n.py", "new_path": "n.py", "new_file": True, "diff": "@@ -0,0 +1,2 @@\n+first\n+second\n"}]],
            "group/app",
            42,
        )
        self.assertEqual(
            json.loads(result)["files"][0]["lines"],
            [["add", None, 1, "first"], ["add", None, 2, "second"]],
        )

    def test_per_file_cap_marks_truncation(self):
        patch_text = "@@ -1,40 +1,40 @@\n" + "".join(f"+line{i}\n" for i in range(40))
        result, _ = call(
            "gitlab_get_merge_request_review_diffs",
            GITLAB_ENV,
            [MERGE_REQUEST, [{"old_path": "b.py", "new_path": "b.py", "diff": patch_text}]],
            "group/app",
            42,
            max_lines_per_file=20,
        )
        payload = json.loads(result)
        self.assertEqual(len(payload["files"][0]["lines"]), 20)
        self.assertTrue(payload["files"][0]["truncated"])
        self.assertEqual(payload["files"][0]["lines_returned"], 20)
        self.assertEqual(payload["files"][0]["lines_dropped"], 20)
        self.assertEqual(payload["coverage"]["lines_dropped"], 20)
        self.assertEqual(payload["coverage"]["files_truncated"], ["b.py"])
        self.assertFalse(payload["coverage"]["complete"])

    def test_coverage_is_complete_for_a_fully_returned_merge_request(self):
        result, _ = call(
            "gitlab_get_merge_request_review_diffs",
            GITLAB_ENV,
            [
                dict(MERGE_REQUEST, changes_count="1"),
                FakeResponse(
                    [{"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1,1 +1,1 @@\n+only\n"}],
                    headers={"X-Next-Page": ""},
                ),
            ],
            "group/app",
            42,
        )
        coverage = json.loads(result)["coverage"]
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["files_total"], 1)
        self.assertEqual(coverage["files_returned"], 1)
        self.assertEqual(coverage["lines_returned"], 1)
        self.assertEqual(coverage["lines_dropped"], 0)
        self.assertFalse(coverage["has_more_pages"])
        self.assertIsNone(coverage["next_page"])

    def test_further_pages_are_reported_and_block_completeness(self):
        result, _ = call(
            "gitlab_get_merge_request_review_diffs",
            GITLAB_ENV,
            [
                dict(MERGE_REQUEST, changes_count="60"),
                FakeResponse(
                    [{"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1,1 +1,1 @@\n+only\n"}],
                    headers={"X-Next-Page": "2"},
                ),
            ],
            "group/app",
            42,
        )
        coverage = json.loads(result)["coverage"]
        self.assertTrue(coverage["has_more_pages"])
        self.assertEqual(coverage["next_page"], 2)
        self.assertFalse(coverage["complete"])

    def test_missing_pagination_header_falls_back_to_a_full_page(self):
        files = [
            {"old_path": f"f{index}.py", "new_path": f"f{index}.py", "diff": "@@ -1,1 +1,1 @@\n+only\n"}
            for index in range(2)
        ]
        result, _ = call(
            "gitlab_get_merge_request_review_diffs",
            GITLAB_ENV,
            [MERGE_REQUEST, files],
            "group/app",
            42,
            limit=2,
        )
        coverage = json.loads(result)["coverage"]
        self.assertTrue(coverage["has_more_pages"])
        self.assertIsNone(coverage["next_page"])
        self.assertFalse(coverage["complete"])

    def test_approximate_file_count_is_never_reported_as_complete(self):
        result, _ = call(
            "gitlab_get_merge_request_review_diffs",
            GITLAB_ENV,
            [
                dict(MERGE_REQUEST, changes_count="20+"),
                FakeResponse(
                    [{"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1,1 +1,1 @@\n+only\n"}],
                    headers={"X-Next-Page": ""},
                ),
            ],
            "group/app",
            42,
        )
        coverage = json.loads(result)["coverage"]
        self.assertTrue(coverage["files_total_approximate"])
        self.assertFalse(coverage["complete"])

    def test_too_large_file_is_truncated_without_dropped_lines(self):
        result, _ = call(
            "gitlab_get_merge_request_review_diffs",
            GITLAB_ENV,
            [
                dict(MERGE_REQUEST, changes_count="1"),
                FakeResponse(
                    [{"old_path": "big.bin", "new_path": "big.bin", "diff": "", "too_large": True}],
                    headers={"X-Next-Page": ""},
                ),
            ],
            "group/app",
            42,
        )
        payload = json.loads(result)
        self.assertTrue(payload["files"][0]["truncated"])
        self.assertEqual(payload["coverage"]["files_truncated"], ["big.bin"])
        self.assertFalse(payload["coverage"]["complete"])

    def test_merge_request_without_diff_refs_is_refused(self):
        result, _ = call(
            "gitlab_get_merge_request_review_diffs",
            GITLAB_ENV,
            [{"title": "no refs", "diff_refs": None}, []],
            "group/app",
            42,
        )
        self.assertTrue(result.startswith("GITLAB_ERROR: merge request has no usable diff_refs"))


class DraftNoteTests(unittest.TestCase):
    def position(self, **kwargs):
        _, requests = call(
            "gitlab_create_merge_request_draft_note",
            GITLAB_ENV,
            [{"id": 900}],
            "group/app",
            42,
            "body",
            "base1",
            "head1",
            "start1",
            "a.py",
            "a.py",
            **kwargs,
        )
        return json.loads(requests[0].data)["position"]

    def test_added_line_sends_only_new_line(self):
        position = self.position(new_line=142)
        self.assertEqual(position["new_line"], 142)
        self.assertNotIn("old_line", position)
        self.assertEqual(position["position_type"], "text")
        self.assertEqual(position["base_sha"], "base1")

    def test_removed_line_sends_only_old_line(self):
        position = self.position(old_line=88)
        self.assertEqual(position["old_line"], 88)
        self.assertNotIn("new_line", position)

    def test_context_line_sends_both(self):
        position = self.position(new_line=142, old_line=88)
        self.assertEqual((position["old_line"], position["new_line"]), (88, 142))

    def test_missing_both_line_numbers_is_refused(self):
        tool = load_tool("gitlab_create_merge_request_draft_note")
        with patch.dict(os.environ, GITLAB_ENV, clear=False):
            result = tool("group/app", 42, "body", "base1", "head1", "start1", "a.py", "a.py")
        self.assertTrue(result.startswith("GITLAB_ERROR: at least one of new_line or old_line"))

    def test_blank_diff_ref_is_refused(self):
        tool = load_tool("gitlab_create_merge_request_draft_note")
        with patch.dict(os.environ, GITLAB_ENV, clear=False):
            result = tool("group/app", 42, "body", "", "head1", "start1", "a.py", "a.py", 5)
        self.assertTrue(result.startswith("GITLAB_ERROR: base_sha must be"))

    def test_created_draft_reports_unpublished(self):
        result, requests = call(
            "gitlab_create_merge_request_draft_note",
            GITLAB_ENV,
            [{"id": 900}],
            "group/app",
            42,
            "body",
            "base1",
            "head1",
            "start1",
            "a.py",
            "a.py",
            142,
        )
        payload = json.loads(result)
        self.assertEqual(payload["id"], 900)
        self.assertFalse(payload["published"])
        self.assertTrue(requests[0].full_url.endswith("/merge_requests/42/draft_notes"))


class PublishTests(unittest.TestCase):
    def test_publish_sends_summary_and_reviewer_state(self):
        result, requests = call(
            "gitlab_publish_merge_request_draft_notes",
            GITLAB_ENV,
            [FakeResponse(None, status=204)],
            "group/app",
            42,
            "summary body",
            "requested_changes",
        )
        body = json.loads(requests[0].data)
        self.assertEqual(body, {"note": "summary body", "reviewer_state": "requested_changes"})
        self.assertTrue(requests[0].full_url.endswith("/draft_notes/bulk_publish"))
        self.assertTrue(json.loads(result)["published"])

    def test_invalid_reviewer_state_is_refused(self):
        tool = load_tool("gitlab_publish_merge_request_draft_notes")
        with patch.dict(os.environ, GITLAB_ENV, clear=False):
            result = tool("group/app", 42, "summary", "approved")
        self.assertTrue(result.startswith("GITLAB_ERROR: reviewer_state must be"))


class JiraContextTests(unittest.TestCase):
    FIELDS = [{"id": "customfield_10014", "name": "Epic Link"}, {"id": "summary", "name": "Summary"}]

    def story(self, fields):
        return {"key": "SEN-206", "fields": fields}

    def test_epic_resolved_from_parent(self):
        result, _ = call(
            "jira_get_issue_context",
            JIRA_ENV,
            [
                self.FIELDS,
                self.story({"summary": "Add limiting", "description": "see https://confluence.example.test/display/ENG/Design", "parent": {"key": "SEN-100", "fields": {"issuetype": {"name": "Epic"}}}}),
                [],
                self.story({"summary": "Epic", "description": ""}),
                [],
            ],
            "SEN-206",
        )
        payload = json.loads(result)
        self.assertEqual(payload["epic"]["resolved_via"], "parent")
        self.assertTrue(payload["epic_resolved"])
        self.assertEqual(payload["confluence_urls"], ["https://confluence.example.test/display/ENG/Design"])

    def test_epic_resolved_from_discovered_epic_link_field(self):
        result, _ = call(
            "jira_get_issue_context",
            JIRA_ENV,
            [
                self.FIELDS,
                self.story({"summary": "Add limiting", "description": "", "customfield_10014": "SEN-100"}),
                [],
                self.story({"summary": "Epic", "description": ""}),
                [],
            ],
            "SEN-206",
        )
        payload = json.loads(result)
        self.assertEqual(payload["epic"]["resolved_via"], "epic link (customfield_10014)")
        self.assertEqual(payload["epic_link_field"], "customfield_10014")

    def test_epic_resolved_from_issue_link(self):
        result, _ = call(
            "jira_get_issue_context",
            JIRA_ENV,
            [
                self.FIELDS,
                self.story({"summary": "s", "description": "", "issuelinks": [{"outwardIssue": {"key": "SEN-100", "fields": {"issuetype": {"name": "Epic"}}}}]}),
                [],
                self.story({"summary": "Epic", "description": ""}),
                [],
            ],
            "SEN-206",
        )
        self.assertEqual(json.loads(result)["epic"]["resolved_via"], "issue link")

    def test_foreign_urls_are_not_collected(self):
        result, _ = call(
            "jira_get_issue_context",
            JIRA_ENV,
            [self.FIELDS, self.story({"summary": "s", "description": "https://example.com/doc"}), []],
            "SEN-206",
        )
        payload = json.loads(result)
        self.assertEqual(payload["confluence_urls"], [])
        self.assertIsNone(payload["epic"])

    def test_malformed_issue_key_is_refused(self):
        tool = load_tool("jira_get_issue_context")
        with patch.dict(os.environ, JIRA_ENV, clear=False):
            self.assertTrue(tool("not-a-key!").startswith("JIRA_ERROR: issue_key must look like"))


class ConfluenceUrlTests(unittest.TestCase):
    PAGE = {"id": "12345", "title": "Design", "space": {"key": "ENG"}, "version": {"number": 3}, "body": {"view": {"value": "content"}}, "_links": {"webui": "/display/ENG/Design"}}

    def test_page_id_query_parameter(self):
        result, requests = call(
            "confluence_get_page_by_url",
            CONFLUENCE_ENV,
            [self.PAGE],
            "https://confluence.example.test/pages/viewpage.action?pageId=12345",
        )
        self.assertEqual(json.loads(result)["resolved_via"], "pageId parameter")
        self.assertIn("/rest/api/content/12345", requests[0].full_url)

    def test_pages_path_segment(self):
        result, _ = call(
            "confluence_get_page_by_url",
            CONFLUENCE_ENV,
            [self.PAGE],
            "https://confluence.example.test/spaces/ENG/pages/12345/Design",
        )
        self.assertEqual(json.loads(result)["resolved_via"], "pages path segment")

    def test_display_path_falls_back_to_title_search(self):
        result, requests = call(
            "confluence_get_page_by_url",
            CONFLUENCE_ENV,
            [{"results": [{"id": "12345"}]}, self.PAGE],
            "https://confluence.example.test/display/ENG/Rate+Limiting+Design",
        )
        self.assertEqual(json.loads(result)["resolved_via"], "display path title search")
        self.assertIn("Rate+Limiting+Design", requests[0].full_url.replace("%20", "+"))

    def test_foreign_host_is_refused(self):
        tool = load_tool("confluence_get_page_by_url")
        with patch.dict(os.environ, CONFLUENCE_ENV, clear=False):
            result = tool("https://evil.example.com/display/ENG/Design")
        self.assertTrue(result.startswith("CONFLUENCE_ERROR: page_url does not belong"))


if __name__ == "__main__":
    unittest.main()
