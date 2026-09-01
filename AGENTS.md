# AGENTS.md

## Project overview

This repository defines a Docker Compose stack containing:

- Letta, exposed on `127.0.0.1:8283` by default.
- Open WebUI, exposed on `127.0.0.1:3000` by default.
- A small OpenAI-compatible adapter that presents Letta agents to Open WebUI.
- A shared PostgreSQL server with separate databases and roles for Letta and
  Open WebUI.
- PGVector for Open WebUI's vector storage.

The main deployment definition is `compose.yml`. PostgreSQL initialization is
handled by `postgres-init.sh`, and `letta-openai-proxy.py` implements the Letta
adapter.

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
and registration logic in the executable `letta-assets/bootstrap` script.

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
   ```

4. Ensure Letta is healthy, then apply the assets:

   ```sh
   docker compose up -d
   ./letta-assets/bootstrap
   ```

5. Run `bootstrap` a second time after changing its registration logic or an
   MCP manifest. The second run must update the same server without creating a
   duplicate and must discover the expected tools.
6. Run `docker compose ps` and the embedding checks below before handing off
   changes that also affect Compose or environment settings.

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
docker compose up -d
docker compose ps
```

All four services should become healthy. Confirm the embedding configuration
without displaying API keys:

```sh
docker compose exec -T letta python -c \
  'from letta.settings import settings; print(settings.default_embedding_handle)'

docker compose exec -T open-webui python -c \
  'import os; print(os.environ.get("RAG_EMBEDDING_ENGINE")); print(os.environ.get("RAG_EMBEDDING_MODEL"))'
```

Expected values are `openai` and `openai/text-embedding-3-small`.

The LiteLLM endpoint must resolve and be reachable from inside the containers.
If Letta reports that the default embedding handle is not registered, inspect
its provider-sync logs and verify `${LITELLM_BASE_URL}/models` before changing
the model configuration.
