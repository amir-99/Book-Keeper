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
  with bounded Confluence and Jira orchestration. It gathers evidence from the
  required domain specialists, sends the evidence and original question to one
  difficulty-matched engineering worker, and optionally sends explicitly
  authorized mutations back to the matching specialists.
- `agents/office-agent-confluence-worker.json`: A hidden, stateless Confluence
  specialist backed by Gemini 3.7 Flash. It separates evidence gathering from
  explicitly authorized create, update, and comment operations.
- `agents/office-agent-jira-worker.json`: A hidden, stateless Jira specialist
  backed by Gemini 3.7 Flash. It separates issue evidence gathering from
  explicitly authorized create, update, comment, and transition operations.
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
- Merge requests: `gitlab_list_merge_requests`,
  `gitlab_get_merge_request`, `gitlab_get_merge_request_diffs`,
  `gitlab_create_merge_request`, `gitlab_update_merge_request`, and
  `gitlab_add_merge_request_note`.
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

Bootstrap registers all GitLab functions in Letta's tool catalog but does not
attach them automatically. Add only the required function names to a target
agent's `custom_tools` array. No existing agent is granted GitLab access merely
because the connection is configured.

For cross-domain requests, the office manager gathers Confluence and Jira
evidence independently before making exactly one engineering-worker call. It
can then invoke each relevant specialist once more for explicitly authorized
writes; authorization for one system never authorizes a write to the other.

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

Before invoking a worker, the router streams a concise, user-visible preamble
inside `<think>...</think>`. Open WebUI renders it incrementally as a
collapsible reasoning section while the worker runs. The worker then returns
only the final answer. This preamble is an approach-and-checks summary; raw
hidden chain-of-thought, prompts, memory, credentials, tool payloads, and
routing details must not be exposed.

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
