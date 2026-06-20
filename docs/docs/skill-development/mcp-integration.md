---
sidebar_position: 3
---

# MCP Integration

Kazma skills can connect to Model Context Protocol (MCP) servers for external tool access.

## What is MCP?

MCP (Model Context Protocol) is a standard for connecting AI agents to external tools and data sources.

## Configuration

Add MCP servers to your skill manifest:

```yaml
mcp_servers:
  - name: weather-api
    type: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-weather"]
    env:
      WEATHER_API_KEY: "${WEATHER_API_KEY}"

  - name: database
    type: sse
    url: "http://localhost:3001/sse"
    headers:
      Authorization: "Bearer ${DB_TOKEN}"

  - name: file-server
    type: streamable-http
    url: "http://localhost:3002/mcp"
```

## Server types

| Type | Transport | Use case |
|---|---|---|
| stdio | stdin/stdout | Local processes |
| sse | Server-Sent Events | Remote HTTP servers |
| streamable-http | HTTP streaming | Modern MCP servers |

## Security

MCP servers run with the skill permissions:

- Network access requires `network_outbound` permission
- File access requires `file_read` / `file_write` permissions
- All calls are logged in the audit trail
- Rate limiting prevents abuse
