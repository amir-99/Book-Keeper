import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch


TOOL_DIR = Path(__file__).resolve().parents[1] / "letta-assets" / "tools"
JIRA_ENV = {
    "JIRA_BASE_URL": "https://jira.example.test",
    "JIRA_ACCESS_TOKEN": "test-token",
    "JIRA_AUTH_MODE": "bearer",
}


def load_tool(name):
    path = TOOL_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


class JiraToolTests(unittest.TestCase):
    def call_with_response(self, tool_name, payload, *args, **kwargs):
        tool = load_tool(tool_name)
        with patch.dict(os.environ, JIRA_ENV, clear=False):
            with patch("urllib.request.urlopen", return_value=FakeResponse(payload)) as mocked:
                result = tool(*args, **kwargs)
        return result, mocked.call_args.args[0]

    def test_create_issue_uses_native_extra_fields(self):
        result, request = self.call_with_response(
            "jira_create_issue",
            {"id": "20001", "key": "SEN-300"},
            "SEN",
            "Sub-task",
            "Document the changes",
            extra_fields={
                "parent": {"id": "13387"},
                "project": {"key": "WRONG"},
                "summary": "Wrong summary",
            },
        )

        self.assertEqual(json.loads(result)["key"], "SEN-300")
        fields = json.loads(request.data)["fields"]
        self.assertEqual(fields["parent"], {"id": "13387"})
        self.assertEqual(fields["project"], {"key": "SEN"})
        self.assertEqual(fields["issuetype"], {"name": "Sub-task"})
        self.assertEqual(fields["summary"], "Document the changes")

    def test_update_issue_uses_native_fields(self):
        result, request = self.call_with_response(
            "jira_update_issue",
            None,
            "SEN-204",
            {"labels": ["documented"], "priority": {"name": "Medium"}},
            notify_users=False,
        )

        parsed = json.loads(result)
        self.assertEqual(parsed["updated_fields"], ["labels", "priority"])
        self.assertFalse(parsed["notifications_requested"])
        self.assertIn("notifyUsers=false", request.full_url)
        self.assertEqual(
            json.loads(request.data)["fields"],
            {"labels": ["documented"], "priority": {"name": "Medium"}},
        )

    def test_transition_issue_uses_native_fields(self):
        result, request = self.call_with_response(
            "jira_transition_issue",
            None,
            "SEN-204",
            "31",
            fields={"resolution": {"name": "Done"}},
        )

        self.assertEqual(json.loads(result)["submitted_fields"], ["resolution"])
        self.assertEqual(
            json.loads(request.data),
            {
                "transition": {"id": "31"},
                "fields": {"resolution": {"name": "Done"}},
            },
        )

    def test_structured_parameters_reject_strings(self):
        self.assertEqual(
            load_tool("jira_create_issue")("SEN", "Task", "Summary", extra_fields="{}"),
            "JIRA_ERROR: extra_fields must be an object",
        )
        self.assertEqual(
            load_tool("jira_update_issue")("SEN-204", "{}"),
            "JIRA_ERROR: fields must be a non-empty object",
        )
        self.assertEqual(
            load_tool("jira_transition_issue")("SEN-204", "31", fields="{}"),
            "JIRA_ERROR: fields must be an object",
        )

    def test_malformed_read_envelopes_return_errors(self):
        cases = (
            ("jira_get_comments", ("SEN-204",), "invalid comment response"),
            ("jira_search_issues", ("project = SEN",), "invalid issue search response"),
            ("jira_list_transitions", ("SEN-204",), "invalid transition response"),
        )
        for tool_name, args, expected in cases:
            with self.subTest(tool=tool_name):
                result, _ = self.call_with_response(tool_name, [], *args)
                self.assertIn(expected, result)

    def test_incomplete_write_responses_are_not_reported_as_success(self):
        create_result, _ = self.call_with_response(
            "jira_create_issue", {}, "SEN", "Task", "Summary"
        )
        comment_result, _ = self.call_with_response(
            "jira_add_comment", {}, "SEN-204", "Comment"
        )
        issue_result, _ = self.call_with_response(
            "jira_get_issue", {"fields": {}}, "SEN-204"
        )

        self.assertIn("did not return a key", create_result)
        self.assertIn("did not return an id", comment_result)
        self.assertIn("without a key", issue_result)

    def test_project_listing_remains_bounded_and_paginated(self):
        projects = [
            {"id": str(index), "key": f"P{index}", "name": f"Project {index}"}
            for index in range(4)
        ]
        result, request = self.call_with_response(
            "jira_list_projects", projects, limit=2, start_at=1
        )

        parsed = json.loads(result)
        self.assertEqual([item["key"] for item in parsed["projects"]], ["P1", "P2"])
        self.assertEqual(parsed["projects"][0]["web_url"], "https://jira.example.test/browse/P1")
        self.assertEqual(parsed["total_visible"], 4)
        self.assertEqual(request.get_method(), "GET")


if __name__ == "__main__":
    unittest.main()
