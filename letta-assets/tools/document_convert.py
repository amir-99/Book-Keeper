def document_convert(filename: str, content_base64: str) -> str:
    """Convert an existing office or text file to PDF and return a download link.

    Use this for a file the workflow already has in hand, such as one fetched
    from another system. Relay the returned download_url exactly as received.

    Args:
        filename: Original file name including its extension, such as report.docx.
        content_base64: Base64-encoded file bytes, at most 25 MiB decoded.

    Returns:
        JSON with filename, byte size, and download_url, or a DOCUMENTS_ERROR
        string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
        return "DOCUMENTS_ERROR: filename must be a non-empty string up to 255 characters"
    if "." not in filename.strip().strip("/").split("/")[-1]:
        return "DOCUMENTS_ERROR: filename must include a file extension"
    if not isinstance(content_base64, str) or not content_base64.strip():
        return "DOCUMENTS_ERROR: content_base64 must be a non-empty base64 string"
    if len(content_base64) > 36000000:
        return "DOCUMENTS_ERROR: content_base64 exceeds the 25 MiB file limit"

    base_url = os.getenv("DOCUMENTS_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("DOCUMENTS_API_KEY", "")
    if not base_url:
        return "DOCUMENTS_ERROR: DOCUMENTS_BASE_URL is unavailable"
    if not api_key:
        return "DOCUMENTS_ERROR: DOCUMENTS_API_KEY is unavailable"

    body = json.dumps({"filename": filename.strip(), "content_base64": content_base64.strip()}).encode()
    request = Request(
        f"{base_url}/convert",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "letta-document-tools/1.0",
        },
    )
    try:
        with urlopen(request, timeout=180) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"DOCUMENTS_ERROR: document service returned HTTP {error.code} while converting the file"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"DOCUMENTS_ERROR: unable to convert the file ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("download_url"), str):
        return "DOCUMENTS_ERROR: document service returned an unexpected payload"
    return json.dumps(payload, ensure_ascii=False)
