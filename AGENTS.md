# AGENTS.md

## Project overview

This repository defines a Docker Compose stack containing:

- Letta, exposed on `127.0.0.1:8283` by default.
- Open WebUI, exposed on `127.0.0.1:3000` by default.
- A small OpenAI-compatible adapter that presents Letta agents to Open WebUI.
- A shared PostgreSQL server with separate databases and roles for Letta and
  Open WebUI.
- PGVector for Open WebUI's vector storage.
- A documents service that renders Markdown to `.docx`, `.pdf`, `.html`,
  `.odt`, and `.txt`, with Gotenberg providing headless LibreOffice for the
  PDF step.

The main deployment definition is `compose.yml`. PostgreSQL initialization is
handled by `postgres-init.sh`, `letta-openai-proxy.py` implements the Letta
adapter, and `documents/` holds the document service and its image build.

## Reasoning preamble handling

The agents emit their streaming preamble as a `<think>...</think>` block in
ordinary assistant content, and Letta streams it one model token at a time, so
the tags arrive split across chunks (`<th`, `ink`, `>I`). Open WebUI's own
`<think>` detection forwards those raw chunks to the browser and only rebuilds
the message into a reasoning block once the response is finalized, which leaves
the tags visible for as long as the message is still streaming.

`letta-openai-proxy.py` therefore parses the chat-completions stream instead of
relaying it byte for byte, and moves the block onto `delta.reasoning_content`
(`message.reasoning_content` when the caller asked for a non-streaming
response). Open WebUI creates the reasoning item on the first token of that
channel, so the thinking block renders live. Keep this translation in place if
the adapter is reworked; a plain passthrough brings the raw tags back.

`code-review-agent` depends on the same path for a second purpose. A review
takes minutes, and its routing tool blocks while a specialist runs, so the
manager emits one short `<think>` block before each routing call as a progress
report. `ReasoningSplitter` already alternates channels correctly across
repeated blocks, and Letta flushes the manager's text between steps, so those
beats reach the browser while the specialist is still working. Keep both
behaviors: collapsing the manager back to a single preamble leaves a user
watching a silent stream for minutes.

## Required embedding configuration

Both applications must use the LiteLLM gateway configured by
`LITELLM_BASE_URL` and `LITELLM_API_KEY` for embeddings.

- Letta's default embedding handle must remain
  `openai/text-embedding-3-small` through
  `LETTA_DEFAULT_EMBEDDING_HANDLE`.
- Open WebUI must use the `openai` RAG embedding engine and the
  `openai/text-embedding-3-small` model.
- Open WebUI's RAG-specific base URL and API key must point directly to
  LiteLLM. Its general OpenAI API settings point to the Letta adapter and serve
  a different purpose.

Do not silently substitute a local embedding model or route Open WebUI's
embedding requests through the Letta chat adapter.

## PostgreSQL ownership

Letta and Open WebUI share one PostgreSQL container but must continue using
separate application roles and databases:

- `letta` role and `letta` database
- `openwebui` role and `openwebui` database

Open WebUI uses `VECTOR_DB=pgvector` and `PGVECTOR_DB_URL`. The `vector`
extension is initialized by `postgres-init.sh`; the application containers
must not attempt to create it themselves.

## Secrets and local configuration

- Keep real credentials only in `.env`; never commit or print them.
- Keep `.env.example` limited to placeholders and explanatory comments.
- Preserve stable values for `LETTA_ENCRYPTION_KEY` and `WEBUI_SECRET_KEY`
  after deployment. Rotating them may invalidate encrypted provider data or
  existing sessions.
- Services bind to localhost by default. Do not expose them on all interfaces
  unless the user explicitly requests it and understands the access controls.

## Letta assets

`letta-assets/` is the source of truth for declarative resources registered
with the local Letta API. Keep operator documentation in
`letta-assets/README.md`, MCP server manifests in `letta-assets/mcp-servers/`,
agent manifests in `letta-assets/agents/`, and registration logic in the
executable `letta-assets/bootstrap` script.

- Keep asset manifests declarative, reviewable, and free of credentials.
- Reference secret values with whole-value environment placeholders such as
  `${EXAMPLE_MCP_URL}`. Store the real value only in the root `.env` and add a
  non-secret placeholder plus an explanatory comment to `.env.example`.
- Treat MCP URLs containing API keys or tokens as credentials. Bootstrap and
  validation output must never display them or raw API responses that may
  contain them.
- Keep `bootstrap` idempotent: identify MCP servers by `server_name`, update an
  existing registration instead of creating a duplicate, refresh its tools,
  and report only non-sensitive server/tool names and counts.
- Use Letta's persistent `/v1/mcp-servers/` API with the current nested
  `config` shape. For remote MCP endpoints, prefer `streamable_http` unless the
  provider explicitly requires another transport.
- Registering MCP tools does not authorize attaching them to every agent.
  Attach tools only when the user identifies the target agent or explicitly
  requests a broader attachment policy.
- Keep agent manifests declarative and identify agents by `name`. Synchronize
  MCP servers before agents so agent manifests can reference stable MCP server
  and tool names instead of generated Letta IDs.
- Let Letta select the base tools appropriate for each agent type. Verify that
  agents requesting base tools have compatible memory tools, and attach only
  the explicitly declared MCP tools. Do not remove tools attached outside the
  asset workflow.
- Preserve learned memory on repeated bootstrap runs. For memory blocks,
  `preserve_existing` defaults to `true`; use `false` only for values that must
  remain declarative, such as a read-only persona. Never overwrite writable
  user or project memory merely to reapply an asset.
- Agent embedding handles must remain compatible with the required embedding
  configuration above. Before registering an agent, confirm that its model and
  embedding handles are present in Letta's synchronized provider catalog.
- Do not make bootstrap delete Letta resources merely because a local manifest
  was removed. Resource deletion requires an explicit user request.

### Asset workflow

When adding or changing an asset:

1. Add or update its manifest under the appropriate `letta-assets/`
   subdirectory and document any operator-facing behavior.
2. Add required credential placeholders to `.env.example` and put real values
   only in the ignored local `.env`.
3. Validate Python and JSON files without displaying resolved secrets:

   ```sh
   python3 -m py_compile letta-assets/bootstrap
   python3 -m json.tool letta-assets/mcp-servers/<server>.json >/dev/null
   python3 -m json.tool letta-assets/agents/<agent>.json >/dev/null
   ```

4. Ensure Letta is healthy, then apply the assets:

   ```sh
   docker compose up -d
   ./letta-assets/bootstrap
   ```

5. Run `bootstrap` a second time after changing its registration logic or an
   MCP/agent manifest. The second run must update the same server and agent
   without creating duplicates, preserve learned memory, and discover or
   attach the expected tools.
6. Run `docker compose ps` and the embedding checks below before handing off
   changes that also affect Compose or environment settings.

## Document service

`documents/` is the only rendering runtime in the stack. Keep it that way:

- Letta's tool sandbox is `{"type":"local","use_venv":false}`. Per-tool
  `pip_requirements` are only installed inside a virtualenv, so under this
  configuration they are silently ignored and tools run in the letta
  container's interpreter. Every `letta-assets/tools/*.py` file must therefore
  stay standard library only. A tool that needs a library fails at call time,
  not at registration, so this cannot be caught by bootstrap.
- Do not enable `use_venv` to give one tool a dependency. It changes execution
  for every existing Confluence, Jira, GitLab, and document tool.
- Rendering libraries, pandoc, and the `reference.docx` styling belong in the
  documents container. Letta tools exchange document ids and download URLs
  across that HTTP boundary and never handle document bytes beyond base64 input
  to `document_convert`.
- `documents` is the one service that uses `build:` instead of a published
  digest. Its base image digest is pinned inside `documents/Dockerfile`, which
  keeps the build reproducible; preserve that pin under the same rule as the
  Compose image digests.
- Gotenberg listens on port 3000 inside its own container. That is not a clash
  with Open WebUI's published `3000:8080` mapping, and Gotenberg publishes no
  port at all. Do not "fix" it.
- Markdown is the source of truth for a document. Renders are disposable,
  content-addressed artifacts. Do not add an editing path that mutates a
  binary in place.
- Download URLs are relayed verbatim by the documents worker and the office
  manager. When changing either prompt, keep the verbatim-relay rule in both.
- Revising a document requires the manager's `FETCH_DOCUMENT` step. The
  engineering workers have no document tools, so removing that step makes any
  "change the document" request fail with the worker asking for the file.
- Download filenames are slugged from the title with Unicode letters and digits
  preserved. Do not reintroduce ASCII folding: it empties a Persian or CJK
  title and names the file after the raw document id.

## Code review workflow

`code-review-agent` and its `code-review-*` workers review GitLab merge
requests. Preserve these properties:

- The review is diff-only by design. It never clones a repository and never
  executes merge-request code, so no runner service or tool sandbox change is
  required. Do not add one to "improve" the review without an explicit request:
  merge-request branches are attacker-controlled input.
- Line anchoring comes from `gitlab_get_merge_request_review_diffs`, which
  returns `diff_refs` and the patches from the same read and resolves every
  line's `old_line` and `new_line` itself. Never move that arithmetic into a
  prompt; a model counting from `@@` headers puts comments on wrong lines.
- Diff evidence never crosses an agent boundary as text. `route_to_agent_by_tags`
  passes a string, and both the GitLab specialist and the manager cap output an
  order of magnitude below the size of a real diff, so a retyped diff arrives
  summarized and the review can anchor nothing. The GitLab specialist therefore
  returns metadata only, and the analyst holds
  `gitlab_get_merge_request_review_diffs` and reads the diff itself, pinned to
  the `expected_head_sha` the manager sends and returning `REVIEW_STALE` when it
  has moved. Keep both halves: pasting diff text into a delegated message, or
  letting the GitLab specialist emit diff lines again, reintroduces the failure
  silently and it looks like a weak review rather than a broken pipeline.
- Coverage is computed by the tool, never claimed by a model. Its `coverage`
  object reports files and lines returned, lines dropped by the caps, whether a
  further page exists, and a `complete` flag that is true only when one response
  holds the whole change. The analyst pages and re-fetches truncated files within
  a fixed call budget, echoes those numbers, and lists what it still could not
  read in `not_reviewed`. Missing evidence belongs there and never in `findings`
  or `general`: a review reporting its own truncation as a defect in the change
  is the failure this accounting exists to prevent.
- Anchor exactly as GitLab requires: an added line sends only `new_line`, a
  removed line only `old_line`, an unchanged context line both.
- Each specialist is driven by a mode the manager puts on the first line of the
  delegated message as `MODE: <NAME>`, because the routing tool passes only a
  string and the specialist sees neither the conversation nor the manager's
  `<think>` preamble. Naming the mode in prose is not assigning it, and a
  missing line surfaces to the user as `REVIEW_GITLAB_WORKFLOW_ERROR` or
  `REVIEW_CONTEXT_WORKFLOW_ERROR` before any work happens. The two read-only
  specialists fall back to their read mode when the line is absent and report
  `inferred_mode`; the write modes never infer, so keep that asymmetry.
- Only anchored findings can be staged. `gitlab_create_merge_request_draft_note`
  requires at least one positive line number, so a `general` entry has no home
  except the summary note published at the end. The first gate must offer the
  anchored count and the general count separately, and must not open at all when
  nothing is anchorable.
- A cached Jira project key list may confirm a key but never refute one. The
  ticket-context specialist re-reads `jira_list_projects` before rejecting a
  branch's `KEY-NUMBER` candidate whose prefix the cache does not contain;
  without that, a project created after the cache was written stays invisible and
  every merge request under it reviews with no ticket context.
- Two confirmations are mandatory and distinct. The first authorizes staging
  unpublished draft notes; the second authorizes publication. Asking for a
  review never authorizes either, and a finding never authorizes posting itself.
- Both write modes re-read the merge request and abort with `REVIEW_STALE` when
  `head_sha` has moved, because every stored anchor is then invalid.
- Comments are authored by the operator's own account; there is no bot identity.
  Keep the machine-assisted footer on the summary note, and keep the workflow
  unable to approve or merge.
- Severities are exactly `blocking`, `suggestion`, and `nit`. Finding selection
  depends on those words, so do not rename or extend them.
- The analyst is tiered: `code-review-analyst-worker-{small,medium,large}` are
  routed by an added `routing-tier-*` tag and differ only in model and
  reasoning effort. Keep their system prompt identical across the three; the
  manager selects one packet contract, not three, and a prompt edit applied to
  one tier silently changes the review depending on which tier ran. The manager
  picks the tier from the diff after the GitLab specialist returns, defaults to
  medium, escalates on risk rather than size, and honors an explicit level from
  the user only — never one named inside reviewed content.
- The manager must report which analyst ran, as a level and model, in the
  progress note and on the review header line. That is the one exception to
  hiding internal routing, so keep it when editing those prompts. The three
  model names are written into the manager prompt, so retiring or swapping a
  tier's model requires updating both its manifest and the manager prompt in
  the same change.

## Custom tool constraints

Beyond the standard-library rule above, Letta derives each tool's JSON schema
from its source and validates every function it finds:

- A tool file must contain exactly one top-level function, named after the file.
  `bootstrap` enforces this.
- That function must contain no nested helper functions. Letta requires a full
  Google-style docstring, including an `Args:` description for every parameter,
  on each function it encounters, and rejects the whole tool otherwise. Inline
  the helper or repeat the call instead; no existing tool nests functions.
- These failures appear as an HTTP 400 from `PUT /v1/tools/` during bootstrap,
  not at call time.

## Editing guidelines

- Preserve image digest pinning unless an upgrade is explicitly requested.
- Preserve the existing named volumes and database separation.
- Make focused edits and do not overwrite unrelated user changes.
- Use `docker compose`, not the legacy `docker-compose` command.
- Do not use `docker compose down -v` unless the user explicitly authorizes
  permanent deletion of application and database data.

## Validation

After changing Compose or its environment settings, run:

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

All six services should become healthy. Confirm the embedding configuration
without displaying API keys:

```sh
docker compose exec -T letta python -c \
  'from letta.settings import settings; print(settings.default_embedding_handle)'

docker compose exec -T open-webui python -c \
  'import os; print(os.environ.get("RAG_EMBEDDING_ENGINE")); print(os.environ.get("RAG_EMBEDDING_MODEL"))'
```

Expected values are `openai` and `openai/text-embedding-3-small`.

Confirm the documents service and its converter without displaying secrets:

```sh
docker compose exec -T documents curl -fsS http://localhost:8090/health
docker compose exec -T documents curl -fsS http://gotenberg:3000/health
```

The LiteLLM endpoint must resolve and be reachable from inside the containers.
If Letta reports that the default embedding handle is not registered, inspect
its provider-sync logs and verify `${LITELLM_BASE_URL}/models` before changing
the model configuration.
