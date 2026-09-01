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
