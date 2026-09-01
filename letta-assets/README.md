# Letta assets

This directory stores declarative assets and supporting information for the
local Letta deployment. The `bootstrap` script applies the assets to the Letta
API and is safe to run repeatedly.

## Included assets

- `mcp-servers/tavily.json`: Tavily's remote Streamable HTTP MCP server. Its
  credential-bearing URL is read from `TAVILY_MCP_URL`; no secret is stored in
  this directory.

## Bootstrap

Start Letta, then run the bootstrap from the repository root:

```sh
docker compose up -d
./letta-assets/bootstrap
```

The script loads `.env`, creates or updates each MCP server by name, refreshes
the tools discovered from it, and reports their names. It requires Python 3.10+
and only uses the standard library.

The defaults target `http://127.0.0.1:8283`. To target another Letta API, set
`LETTA_BASE_URL` in the process environment or pass `--base-url`. Use
`--env-file` if the secrets file is stored elsewhere.

MCP tools are registered in Letta's tool catalog but are not automatically
attached to every agent. Attach the desired tools to an agent in Letta after
bootstrapping.

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
