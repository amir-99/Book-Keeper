# Letta assets

This directory stores declarative assets and supporting information for the
local Letta deployment. The `bootstrap` script applies the assets to the Letta
API and is safe to run repeatedly.

## Included assets

- `mcp-servers/tavily.json`: Tavily's remote Streamable HTTP MCP server. Its
  credential-bearing URL is read from `TAVILY_MCP_URL`; no secret is stored in
  this directory.
- `agents/engineering-assistant.json`: A concise engineering chat agent with
  persistent user/project memory, Letta's base memory tools, and all declared
  Tavily tools.

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

The defaults target `http://127.0.0.1:8283`. To target another Letta API, set
`LETTA_BASE_URL` in the process environment or pass `--base-url`. Use
`--env-file` if the secrets file is stored elsewhere.

MCP tools are registered in Letta's tool catalog but are attached only to
agents that explicitly declare them. Bootstrap adds missing declared tools but
does not remove tools attached outside the asset workflow.

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
