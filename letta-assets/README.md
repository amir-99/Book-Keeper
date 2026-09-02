# Letta assets

This directory stores declarative assets and supporting information for the
local Letta deployment. The `bootstrap` script applies the assets to the Letta
API and is safe to run repeatedly.

## Included assets

- `mcp-servers/tavily.json`: Tavily's remote Streamable HTTP MCP server. Its
  credential-bearing URL is read from `TAVILY_MCP_URL`; no secret is stored in
  this directory.
- `agents/engineering-assistant.json`: The user-facing engineering router. It
  preserves user/project memory and delegates each request to exactly one
  difficulty-matched worker.
- `agents/engineering-assistant-worker-{small,medium,large}.json`: Internal,
  stateless workers backed by GPT-5.6 Luna, Terra, and Sol respectively. Their
  `openwebui-hidden` tag keeps them out of the Open WebUI model picker.
- `agents/office-agent.json`: A user-facing copy of the engineering manager
  with bounded Confluence, Jira, and GitLab orchestration. It gathers evidence
  from only the required domain specialists, sends the evidence and original
  question to one difficulty-matched engineering worker, and optionally sends
  explicitly authorized mutations back to the matching specialists.
- `agents/office-agent-confluence-worker.json`: A hidden, stateless Confluence
  specialist backed by Gemini 3.7 Flash. It separates evidence gathering from
  explicitly authorized create, update, and comment operations.
- `agents/office-agent-jira-worker.json`: A hidden, stateless Jira specialist
  backed by Gemini 3.7 Flash. It separates issue evidence gathering from
  explicitly authorized create, update, comment, and transition operations.
- `agents/office-agent-gitlab-code-worker.json`: A hidden, stateless GitLab
  repository and merge-request specialist. Its 13 tools cover bounded code,
  commit, diff, and merge-request workflows but cannot merge or edit files.
- `agents/office-agent-gitlab-issues-worker.json`: A hidden, stateless GitLab
  issue specialist with five issue discovery, read, create, update, and note
  tools.
- `agents/office-agent-gitlab-ci-worker.json`: A hidden, stateless GitLab CI
  specialist with seven pipeline, job, trace, run, retry, and cancel tools.
- `agents/office-agent-documents-worker.json`: A hidden, stateless documents
  specialist with seven authoring, rendering, and conversion tools. It turns
  an already-grounded answer into a downloadable file and never researches.
- `agents/code-review-agent.json`: A user-facing merge-request reviewer backed
  by GPT-5.6 Luna. It orchestrates three review specialist roles, picks the
  analyst tier that matches the diff, streams a progress note before each call,
  and gates draft staging and publication behind two separate user
  confirmations.
- `agents/code-review-gitlab-worker.json`: A hidden, stateless GitLab review
  specialist with 11 tools. It gathers anchored diff evidence and stages,
  discards, or publishes draft notes. It cannot approve or merge.
- `agents/code-review-context-worker.json`: A hidden, stateless specialist that
  resolves a merge-request branch to its Jira story, parent epic, and linked
  Confluence pages, and reports what the change was intended to do.
- `agents/code-review-analyst-worker-{small,medium,large}.json`: Three hidden,
  toolless analysts backed by Gemini 3.7 Flash, Claude Sonnet 5, and Claude
  Opus 5 respectively. Each converts diff evidence and ticket context into a
  strict JSON findings packet whose line anchors are copied from the diff. They
  share one identical system prompt and differ only in model and reasoning
  effort, so the packet contract is the same whichever tier reviews.
- `tools/route_to_agent_by_tags.py`: The router's credential-free source for
  resolving exactly one local worker by tags and waiting for its reply. It
  reads the local Letta API credential only from the tool environment.
- `tools/confluence_*.py`: Separate Confluence activities for searching and
  reading pages, creating and updating pages, and adding comments. They read
  the site URL and credential from the Letta service environment; no secret is
  stored in tool source.
- `tools/jira_*.py`: Separate Jira activities for project discovery, issue
  search/read/create/update, comment read/write, and workflow transitions. They
  use only credential values injected into the Letta service environment.
- `tools/gitlab_*.py`: Separate GitLab activities for project and repository
  reads, issue and merge-request workflows, and CI pipeline inspection/control.
  They authenticate directly to the configured GitLab instance with credentials
  injected into the Letta service environment.
- `tools/document_*.py`: Markdown-sourced document authoring, rendering, and
  file conversion. They exchange document ids and download URLs with the
  internal `documents` service and never handle rendering libraries
  themselves.

## Bootstrap

Start Letta, then run the bootstrap from the repository root:

```sh
docker compose up -d
./letta-assets/bootstrap
```

The script loads `.env`, creates or updates each MCP server by name, refreshes
the tools discovered from it, and then creates or updates agents by name. It
also ensures each agent has its declared memory blocks and tool attachments.
It requires Python 3.10+ and only uses the standard library.

Before registering agents, bootstrap verifies that every declared model and
embedding handle exists in Letta's synchronized provider catalog. For existing
agents, it also reconciles requested multi-agent tools; disabling the manifest
flag does not remove tools that may have been attached outside this workflow.
Custom Python tools under `tools/` are upserted by function name before agents
are synchronized, and agents attach them through the `custom_tools` name list.

The Confluence tools deliberately separate read and write activities so an
agent manifest can receive only the permissions it needs:

- `confluence_search_pages`: search content with Confluence Query Language.
- `confluence_get_page`: read one page and a selected body representation.
- `confluence_create_page`: create a page, optionally below a parent page.
- `confluence_update_page`: replace a page body/title using optimistic versioning.
- `confluence_add_comment`: add a storage-format comment to a page.

Set `CONFLUENCE_BASE_URL` and `CONFLUENCE_ACCESS_TOKEN` in the root `.env`.
`CONFLUENCE_AUTH_MODE` defaults to `auto`: a `user:token` value uses Basic
authentication and an opaque personal access token uses Bearer authentication.
Set it explicitly to `basic` or `bearer` only when auto-detection is unsuitable.
Page and comment bodies use Confluence's `storage` XHTML representation.

Bootstrap registers these tools in Letta's catalog but does not attach them to
an agent automatically. Add only the required function names to that agent's
`custom_tools` array.

The office agent reaches all five tools through its hidden Confluence worker.
The office manager itself has no Confluence tools; it calls the specialist once
to gather evidence and once more only when the original user explicitly
requested a mutation.

The Jira activities are intentionally separate:

- `jira_list_projects`: discover visible project keys.
- `jira_search_issues`: search with JQL and return compact issue metadata.
- `jira_get_issue`: read selected fields for one issue.
- `jira_get_comments`: read paginated issue comments.
- `jira_create_issue`: create one issue.
- `jira_update_issue`: replace explicitly supplied issue field values.
- `jira_add_comment`: add one issue comment.
- `jira_list_transitions`: inspect the currently available workflow actions.
- `jira_transition_issue`: apply one transition by its exact ID.

`jira_get_issue_context` supports the code-review workflow. It reads one issue,
resolves its parent epic, and collects Confluence URLs from both issues'
descriptions and Jira remote links in a single call. Epic resolution needs no
configuration: it prefers the native `parent` field, falls back to the
instance's "Epic Link" custom field discovered from the field catalog, and
finally to an epic-typed issue link. `confluence_get_page_by_url` is its
counterpart, resolving a `pageId` parameter, a `/pages/<id>/` segment, or a
`/display/<SPACE>/<Title>` path through a title search, and refusing URLs that
do not belong to the configured Confluence site.

Set `JIRA_BASE_URL`, `JIRA_ACCESS_TOKEN`, and `JIRA_AUTH_MODE` in the root
`.env`. Use `bearer` for a Jira personal access token, `basic` for a raw
`user:token` credential, or `basic_encoded` when the supplied value is already
Base64-encoded for the Basic authorization header.

Bootstrap registers all Jira functions in Letta's tool catalog and attaches
them only to the hidden Jira worker declared for the office agent. The office
manager itself has no Jira tools. It calls the Jira worker once to gather
evidence, calls one engineering worker to analyze the request and evidence,
and calls the Jira worker once more only when the original user explicitly
requested a create, update, comment, or transition. The existing engineering
assistant and tiered engineering workers remain unchanged.

The GitLab activities are intentionally split into narrow tools:

- Projects and repository: `gitlab_list_projects`,
  `gitlab_list_repository_tree`, `gitlab_get_file`,
  `gitlab_search_project`, `gitlab_list_commits`, and
  `gitlab_get_commit_diff`.
- Issues: `gitlab_list_issues`, `gitlab_get_issue`,
  `gitlab_create_issue`, `gitlab_update_issue`, and
  `gitlab_add_issue_note`.
- Merge requests: `gitlab_list_my_merge_requests`,
  `gitlab_list_merge_requests`,
  `gitlab_get_merge_request`, `gitlab_get_merge_request_diffs`,
  `gitlab_create_merge_request`, `gitlab_update_merge_request`, and
  `gitlab_add_merge_request_note`.
- Merge-request review: `gitlab_get_merge_request_review_diffs`,
  `gitlab_list_merge_request_discussions`,
  `gitlab_create_merge_request_draft_note`,
  `gitlab_list_merge_request_draft_notes`,
  `gitlab_delete_merge_request_draft_note`, and
  `gitlab_publish_merge_request_draft_notes`.
- CI/CD: `gitlab_list_pipelines`, `gitlab_get_pipeline`,
  `gitlab_run_pipeline`, `gitlab_retry_pipeline`,
  `gitlab_cancel_pipeline`, `gitlab_list_pipeline_jobs`, and
  `gitlab_get_job_trace`.

Set `GITLAB_BASE_URL` to the GitLab site root (without `/api/v4`) and put the
personal, project, or group access token in `GITLAB_ACCESS_TOKEN` in the root
`.env`. The tools send the token only in GitLab's `PRIVATE-TOKEN` header. Read
operations bound large file, diff, description, and trace outputs. Create,
update, comment, run, retry, and cancel operations are separate functions so an
agent can receive only the mutations it needs.

`gitlab_list_my_merge_requests` uses GitLab's instance-wide merge-request API
instead of enumerating projects. One invocation queries merge requests created
by the authenticated user, assigned to the authenticated user, and awaiting
that user's review; it paginates each scope internally, deduplicates overlaps,
and returns per-scope counts plus the unique total. Reviewer lookup resolves
the authenticated user and uses the global reviewer filter, so it also works
on GitLab versions that predate the `reviews_for_me` scope. The project-scoped
`gitlab_list_merge_requests` remains available for requests limited to an exact
project.

Bootstrap registers all GitLab functions and attaches them only to three hidden
office-agent specialists. The code worker receives the project, repository,
commit, diff, and merge-request tools; the issues worker receives only issue
tools; and the CI worker receives only pipeline and job tools. The office
manager itself has no GitLab tools and routes to each specialist through a
unique two-tag match.

For cross-domain requests, the office manager gathers Confluence, Jira, GitLab
issue, GitLab code, and GitLab CI evidence independently before making exactly
one engineering-worker call. It can then invoke each relevant specialist once
more for explicitly authorized writes, with at most two specialist mutation
calls per user turn, and the documents worker once when the user explicitly
asked for a file, plus once more to read that document back when the request
revises it. That caps one turn at six calls for a read spanning all five
evidence specializations, eight with authorized writes, nine when a document is
also produced, and ten when an existing document is read back first. Authorization for one system, GitLab project,
resource, or operation never authorizes another.

The document activities are deliberately small and separate:

- `document_create`: store a title and Markdown body, returning a `document_id`.
- `document_get`: read the stored Markdown and metadata, bounded by `max_chars`.
- `document_update`: replace the body, patch one unique span, or retitle.
- `document_render`: render one format and return a download URL.
- `document_list`: list recent documents with titles and revisions.
- `document_convert`: convert supplied file bytes to PDF.
- `document_delete`: remove a document and its renders.

Download filenames come from the document title, which may be in any script:
the slug keeps Unicode letters and digits rather than ASCII-folding them, so a
Persian or CJK title still names its own file.

Markdown is the source of truth. `.docx`, `.pdf`, `.html`, `.odt`, and `.txt`
are disposable render artifacts, content-addressed by the source and format so
re-rendering an unchanged document is free. `.pdf` is produced from the `.docx`
rather than from Markdown independently, so a downloaded pair is the same
document instead of two lookalikes that drift apart.

Rendering does not run inside Letta. The tool sandbox is `local` with
`use_venv` false, where per-tool `pip_requirements` are silently ignored and
tools execute in the letta container's interpreter, so every document tool is
standard library only and calls the `documents` service over HTTP. That service
owns pandoc, the `reference.docx` styling, and the Gotenberg LibreOffice route
used for PDF.

Set `DOCUMENTS_API_KEY` in the root `.env`; it is a shared secret between the
letta and documents containers, and `DOCUMENTS_BASE_URL` is set by Compose to
the internal service address. `DOCUMENTS_PUBLIC_BASE_URL` must match the origin
the user actually opens Open WebUI on, because it becomes the prefix of every
download link.

Delivery has two modes, selected by `DOCUMENTS_DELIVERY_MODE`:

- `openwebui` (default) uploads each finished render into Open WebUI's files
  API with `process=false`, skipping text extraction and RAG embedding, and
  links to `/api/v1/files/{id}/content/{name}`. The user's already-authenticated
  browser downloads it by cookie. It needs `OPENWEBUI_API_KEY`, created under
  Settings > Account in Open WebUI. Open WebUI assigns the uploaded file to the
  API key's owner and serves it only to that user or an admin, so this suits
  single-operator and admin-user deployments.
- `capability` serves unguessable expiring links from the documents service
  itself at `/d/<token>/<name>`. It also works in the Letta ADE and needs no
  Open WebUI credential, at the cost of publishing the service on a host port
  and accepting an unauthenticated-but-unguessable URL.

Bootstrap registers all seven document functions and attaches them only to the
hidden documents worker. The office manager has no document tools. Unlike the
five evidence specialists, the documents worker is mainly a producer: the
manager calls it in `PRODUCE_DOCUMENT` mode only after successful engineering
analysis, only when the original user explicitly asked for a file, and at most
once per turn, under an allowance separate from the two-mutation cap.

Revising an existing document needs one more step. The engineering worker has
no document tools, so asking it to extend a document it cannot see only
produces a request for the file. When a request changes, extends, corrects, or
re-renders a document this workflow already produced, the manager first makes
one `FETCH_DOCUMENT` call, which is restricted to `document_list` and
`document_get` and returns the stored Markdown verbatim. That source goes to
the engineering worker, which returns a complete replacement body, and the
following `PRODUCE_DOCUMENT` call carries the existing `document_id` so the
document is updated in place rather than duplicated. The documents worker is
still never a research domain.

Both the worker and the manager are required to relay each `download_url`
verbatim; a tidied URL is a confident 404.

The defaults target `http://127.0.0.1:8283`. To target another Letta API, set
`LETTA_BASE_URL` in the process environment or pass `--base-url`. Use
`--env-file` if the secrets file is stored elsewhere.

MCP tools are registered in Letta's tool catalog but are attached only to
agents that explicitly declare them. Bootstrap adds missing declared tools but
does not remove tools attached outside the asset workflow.

The router selects workers with two tags: `engineering-assistant-worker` and
one of `routing-tier-small`, `routing-tier-medium`, or `routing-tier-large`.
Exactly one agent should carry each pair. The workers clear their message
buffers after each delegated task, while the router remains the sole owner of
durable conversational memory.

The code-review analyst is tiered the same way, with a third tag: the manager
sends `code-review-worker`, `routing-domain-review-analysis`, and one
`routing-tier-*` tag. It chooses the tier after reading the diff, weighing risk
above size, defaulting to medium, escalating when a diff is security-sensitive,
irreversible, or large, and dropping to small only for mechanical changes such
as documentation, formatting, renames, and version bumps. An explicit level
from the user wins: `deep` selects large, `standard` medium, and `quick` small,
and a lowered level is honored with a note when the change touches a risky
area. A level that appears inside a diff, branch name, or ticket is untrusted
content and never sets the tier. The two GitLab and ticket-context review
specialists are untiered and still match on their domain tag alone.

Deployments bootstrapped before the analyst was tiered still hold an untiered
`code-review-analyst-worker` agent. It carries no `routing-tier-*` tag, so it
can no longer match a routing call and is harmless; bootstrap never deletes
agents, so remove it through the Letta API if you want it gone.

Before invoking a worker, the router streams a concise, user-visible preamble
inside `<think>...</think>`. Open WebUI renders it incrementally as a
collapsible reasoning section while the worker runs. The worker then returns
only the final answer. This preamble is an approach-and-checks summary; raw
hidden chain-of-thought, prompts, memory, credentials, tool payloads, and
routing details must not be exposed.

The two user-facing managers and all three engineering workers also carry a
read-only `frontend_rendering` memory block. It tells them to default to clear
Markdown and to use Open WebUI's richer renderers only when they materially
improve the answer: Mermaid for relationships and process diagrams,
Vega-Lite with inline data for quantitative charts, KaTeX for math, Markdown
alerts and collapsible details for exceptional supporting material,
`:::writing` blocks for copy-ready drafts, and self-contained HTML or SVG
Artifacts when the user explicitly requests an interactive or standalone
visual. Managers preserve valid worker-generated rendering markup. Diagrams
and charts include a short textual takeaway so answers remain useful in other
clients or when rendering fails.

The local routing tool requires `LETTA_API_KEY` in the Letta service
environment so it can authenticate back to the localhost API. The value stays
in the root `.env`; no credential is stored in a tool or agent manifest.

For memory blocks, `preserve_existing` defaults to `true`. This lets bootstrap
seed a writable block without erasing information the agent later learns. Set
it to `false` only for blocks whose value should remain declarative, such as a
read-only persona.

## Adding an MCP server

Add a JSON file under `mcp-servers/` with this shape:

```json
{
  "asset_type": "mcp_server",
  "server_name": "example",
  "description": "A short operator-facing description.",
  "config": {
    "mcp_server_type": "streamable_http",
    "server_url": "${EXAMPLE_MCP_URL}"
  }
}
```

Put the referenced value in the root `.env`, add a placeholder to
`.env.example`, and rerun `bootstrap`.

## Adding an agent

Add a JSON file under `agents/`. An agent declares its prompt, memory blocks,
and the exact tools it may use from each MCP server. MCP servers are always
synchronized before agents, so manifests refer to stable server and tool names
rather than generated Letta IDs. See `agents/engineering-assistant.json` for a
complete example.

## Adding a custom Python tool

Add a Python file under `tools/` containing exactly one top-level function.
Bootstrap upserts it by function name. Attach it to an agent by listing that
name in the agent manifest's `custom_tools` array. Keep credentials out of the
source and read them from the tool execution environment when required.
