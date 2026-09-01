import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


REPO_DIR = Path(__file__).resolve().parents[1]
TOOL_DIR = REPO_DIR / "letta-assets" / "tools"
DOCUMENTS_ENV = {
    "DOCUMENTS_BASE_URL": "http://documents.test:8090",
    "DOCUMENTS_API_KEY": "test-documents-key",
}
DOCUMENT_ID = "doc_" + "a" * 32


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool(name):
    return getattr(load_module(name, TOOL_DIR / f"{name}.py"), name)


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


class DocumentToolTests(unittest.TestCase):
    def call_with_response(self, tool_name, payload, *args, **kwargs):
        tool = load_tool(tool_name)
        with patch.dict(os.environ, DOCUMENTS_ENV, clear=False):
            with patch("urllib.request.urlopen", return_value=FakeResponse(payload)) as mocked:
                result = tool(*args, **kwargs)
        return result, mocked.call_args.args[0]

    def call_without_network(self, tool_name, *args, **kwargs):
        tool = load_tool(tool_name)
        with patch.dict(os.environ, DOCUMENTS_ENV, clear=False):
            with patch("urllib.request.urlopen") as mocked:
                result = tool(*args, **kwargs)
        mocked.assert_not_called()
        return result

    def test_create_sends_title_and_markdown(self):
        result, request = self.call_with_response(
            "document_create",
            {"document_id": DOCUMENT_ID, "title": "Weekly status", "revision": 1},
            "  Weekly status  ",
            "# Weekly status\n\nAll green.\n",
        )

        self.assertEqual(json.loads(result)["document_id"], DOCUMENT_ID)
        body = json.loads(request.data)
        self.assertEqual(body["title"], "Weekly status")
        self.assertEqual(body["markdown"], "# Weekly status\n\nAll green.\n")
        self.assertEqual(request.headers["Authorization"], "Bearer test-documents-key")

    def test_render_returns_the_service_download_url_unaltered(self):
        url = "http://localhost:3000/api/v1/files/9f1c%2Ftricky/content/weekly-status.docx?v=2"
        result, request = self.call_with_response(
            "document_render",
            {"document_id": DOCUMENT_ID, "format": "docx", "filename": "weekly-status.docx", "bytes": 8123, "download_url": url},
            DOCUMENT_ID,
            "docx",
        )

        self.assertEqual(json.loads(result)["download_url"], url)
        self.assertEqual(json.loads(request.data), {"format": "docx"})
        self.assertTrue(request.full_url.endswith(f"/documents/{DOCUMENT_ID}/render"))

    def test_render_rejects_an_unsupported_format_before_any_network_call(self):
        result = self.call_without_network("document_render", DOCUMENT_ID, "pptx")
        self.assertTrue(result.startswith("DOCUMENTS_ERROR: output_format must be one of"))

    def test_render_rejects_a_malformed_document_id_before_any_network_call(self):
        for candidate in ["../../etc/passwd", "doc", "", "doc_../secret"]:
            with self.subTest(document_id=candidate):
                result = self.call_without_network("document_render", candidate)
                self.assertEqual(
                    result,
                    "DOCUMENTS_ERROR: document_id must be an identifier from document_create",
                )

    def test_missing_api_key_fails_closed(self):
        tool = load_tool("document_render")
        with patch.dict(os.environ, {"DOCUMENTS_BASE_URL": DOCUMENTS_ENV["DOCUMENTS_BASE_URL"]}, clear=True):
            with patch("urllib.request.urlopen") as mocked:
                result = tool(DOCUMENT_ID, "pdf")
        mocked.assert_not_called()
        self.assertEqual(result, "DOCUMENTS_ERROR: DOCUMENTS_API_KEY is unavailable")

    def test_missing_base_url_fails_closed(self):
        tool = load_tool("document_render")
        with patch.dict(os.environ, {"DOCUMENTS_API_KEY": "test-documents-key"}, clear=True):
            with patch("urllib.request.urlopen") as mocked:
                result = tool(DOCUMENT_ID, "pdf")
        mocked.assert_not_called()
        self.assertEqual(result, "DOCUMENTS_ERROR: DOCUMENTS_BASE_URL is unavailable")

    def test_http_error_surfaces_the_status_without_the_response_body(self):
        tool = load_tool("document_render")
        error = HTTPError(
            "http://documents.test:8090/documents/x/render",
            502,
            "Bad Gateway",
            {},
            None,
        )
        with patch.dict(os.environ, DOCUMENTS_ENV, clear=False):
            with patch("urllib.request.urlopen", side_effect=error):
                result = tool(DOCUMENT_ID, "pdf")

        self.assertEqual(
            result,
            "DOCUMENTS_ERROR: document service returned HTTP 502 while rendering",
        )
        self.assertNotIn("Bad Gateway", result)
        self.assertNotIn("test-documents-key", result)

    def test_update_sends_a_unique_span_replacement(self):
        result, request = self.call_with_response(
            "document_update",
            {"document_id": DOCUMENT_ID, "title": "Weekly status", "revision": 2},
            DOCUMENT_ID,
            replace_old="All green.",
            replace_new="One amber.",
        )

        self.assertEqual(json.loads(result)["revision"], 2)
        self.assertEqual(request.get_method(), "PATCH")
        self.assertEqual(json.loads(request.data)["str_replace"], {"old": "All green.", "new": "One amber."})

    def test_update_refuses_a_body_replacement_and_a_span_edit_together(self):
        result = self.call_without_network(
            "document_update", DOCUMENT_ID, "# New body", replace_old="All green."
        )
        self.assertEqual(result, "DOCUMENTS_ERROR: supply either markdown or replace_old, not both")

    def test_update_requires_something_to_change(self):
        result = self.call_without_network("document_update", DOCUMENT_ID)
        self.assertEqual(result, "DOCUMENTS_ERROR: supply markdown, replace_old, or title")

    def test_convert_requires_a_file_extension(self):
        result = self.call_without_network("document_convert", "report", "QQ==")
        self.assertEqual(result, "DOCUMENTS_ERROR: filename must include a file extension")

    def test_convert_rejects_an_oversized_payload_before_any_network_call(self):
        result = self.call_without_network("document_convert", "report.docx", "A" * 36000004)
        self.assertEqual(result, "DOCUMENTS_ERROR: content_base64 exceeds the 25 MiB file limit")

    def test_delete_requires_service_confirmation(self):
        result, _ = self.call_with_response(
            "document_delete", {"document_id": DOCUMENT_ID, "deleted": False}, DOCUMENT_ID
        )
        self.assertEqual(result, "DOCUMENTS_ERROR: document service did not confirm the deletion")

    def test_list_validates_its_bounds_before_any_network_call(self):
        self.assertEqual(
            self.call_without_network("document_list", 0),
            "DOCUMENTS_ERROR: limit must be from 1 through 100",
        )
        self.assertEqual(
            self.call_without_network("document_list", 25, -1),
            "DOCUMENTS_ERROR: offset must be zero or greater",
        )

    def test_get_bounds_the_returned_source(self):
        result, request = self.call_with_response(
            "document_get",
            {"document_id": DOCUMENT_ID, "markdown": "# Weekly status", "truncated": False},
            DOCUMENT_ID,
            2000,
        )

        self.assertFalse(json.loads(result)["truncated"])
        self.assertIn("max_chars=2000", request.full_url)
        self.assertEqual(
            self.call_without_network("document_get", DOCUMENT_ID, 10),
            "DOCUMENTS_ERROR: max_chars must be from 1000 through 1000000",
        )


class DocumentsServiceTests(unittest.TestCase):
    """The service enforces its own boundaries; the tools are not the only gate."""

    @classmethod
    def setUpClass(cls):
        cls.app = load_module("documents_app", REPO_DIR / "documents" / "app.py")

    def test_document_dir_rejects_path_traversal(self):
        for candidate in [
            "../../etc",
            "doc_../../etc",
            "doc_" + "a" * 31,
            "doc_" + "A" * 32,
            "/etc/passwd",
            "",
            None,
        ]:
            with self.subTest(document_id=candidate):
                with self.assertRaises(self.app.ServiceError) as raised:
                    self.app.document_dir(candidate)
                self.assertEqual(raised.exception.status, 400)

    def test_document_dir_accepts_a_well_formed_id(self):
        self.assertEqual(
            self.app.document_dir(DOCUMENT_ID),
            self.app.DATA_DIR / DOCUMENT_ID,
        )

    def test_service_and_tool_format_allowlists_agree(self):
        source = (TOOL_DIR / "document_render.py").read_text()
        self.assertIn(
            'allowed = {"docx", "pdf", "html", "odt", "txt", "md"}',
            source,
        )
        self.assertEqual(set(self.app.FORMATS), {"docx", "pdf", "html", "odt", "txt", "md"})

    def test_slugify_produces_a_safe_download_filename(self):
        self.assertEqual(self.app.slugify("Q3 Status: review/notes", "fallback"), "q3-status-review-notes")
        self.assertEqual(self.app.slugify("../../etc/passwd", "fallback"), "etc-passwd")
        self.assertEqual(self.app.slugify("!!!", "fallback"), "fallback")

    def test_slugify_keeps_non_latin_titles_instead_of_falling_back_to_the_id(self):
        # ASCII-folding a Persian or CJK title leaves nothing, which used to
        # name the download after the raw document id.
        for title, expected in [
            ("\u0646\u063a\u0645\u0647\u0654 \u0631\u0648\u0634\u0646\u0627\u06cc\u06cc", "\u0646\u063a\u0645\u0647\u0654-\u0631\u0648\u0634\u0646\u0627\u06cc\u06cc"),
            ("\u7b2c\u4e09\u5b63\u5ea6\u66f4\u65b0", "\u7b2c\u4e09\u5b63\u5ea6\u66f4\u65b0"),
            ("Q3 R\u00e9view", "q3-r\u00e9view"),
        ]:
            with self.subTest(title=title):
                self.assertEqual(self.app.slugify(title, "doc_id_fallback"), expected)

    def test_content_disposition_survives_a_non_ascii_filename(self):
        header = self.app.content_disposition("\u0646\u063a\u0645\u0647.docx")
        self.assertIn("filename*=UTF-8''", header)
        self.assertIn("%D9%86", header)
        header.encode("ascii")


if __name__ == "__main__":
    unittest.main()
