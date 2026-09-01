#!/usr/bin/env bash
set -Eeuo pipefail

: "${LETTA_DB_PASSWORD:?LETTA_DB_PASSWORD must be set}"
: "${OPENWEBUI_DB_PASSWORD:?OPENWEBUI_DB_PASSWORD must be set}"

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=letta_password="$LETTA_DB_PASSWORD" \
  --set=openwebui_password="$OPENWEBUI_DB_PASSWORD" <<'EOSQL'
SELECT format('CREATE ROLE letta LOGIN PASSWORD %L', :'letta_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'letta') \gexec
ALTER ROLE letta WITH LOGIN PASSWORD :'letta_password';

SELECT 'CREATE DATABASE letta OWNER letta'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'letta') \gexec

SELECT format('CREATE ROLE openwebui LOGIN PASSWORD %L', :'openwebui_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openwebui') \gexec
ALTER ROLE openwebui WITH LOGIN PASSWORD :'openwebui_password';

SELECT 'CREATE DATABASE openwebui OWNER openwebui'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'openwebui') \gexec

\connect letta
CREATE EXTENSION IF NOT EXISTS vector;

\connect openwebui
CREATE EXTENSION IF NOT EXISTS vector;
EOSQL
