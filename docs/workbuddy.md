# WorkBuddy Connector

> Status: **Publishing package assembled** (`workbuddy/` in this repo);
> platform upload is a human step — see the checklist in
> [`workbuddy/README.md`](../workbuddy/README.md).
> Source plan: [docs/plans/workbuddy-connector-v3.md](plans/workbuddy-connector-v3.md) §48–51.

WorkBuddy is HeadlineArena's Remote-MCP integration surface: a human signs up
or logs in, creates (or selects) an agent, grants scopes on a consent page,
and the WorkBuddy client then speaks OAuth-secured MCP. This directory holds
everything WorkBuddy's platform needs to publish the connector.

## 1. Package layout

```text
workbuddy/
├── connector-meta.json   # connector manifest: metadata, scopes, tools, skills
├── mcp.json              # Remote MCP endpoint (§51, verbatim)
├── icon.svg              # connector icon
├── skills/               # WorkBuddy-platform skills (MCP tools only)
│   ├── ha-forecasting/   # DISCOVER → PREDICT → READ main loop (§49)
│   ├── ha-research/      # pre-prediction evidence gathering
│   └── ha-performance/   # results / scorecard / leaderboard review
└── examples/             # en + zh worked examples
```

## 2. Key conventions

- **OAuth is the MCP server's own** — the package contains **no**
  `token-schema.json` and declares **no** `auth_mode: token`. WorkBuddy
  follows the standard MCP OAuth flow triggered by the 401 +
  `WWW-Authenticate` challenge (see [mcp-integration.md](mcp-integration.md)).
- **Connector identity**: URN `connector:headlinearena`; OAuth callback URI
  `workbuddy://workbuddy/mcp/connector%3Aheadlinearena/oauth/callback`
  (the backend allowlist also accepts the colon-literal form).
- **Endpoint**: `https://mcp.headlinearena.com/mcp` — stateless streamable
  HTTP, JSON mode, no `initialize` handshake.
- **Scopes**: `challenge:read` + `prediction:submit` required;
  `credits:read` + `credits:stake` optional (staked rounds need the latter).
- **Version**: `connector-meta.json` carries its own package version
  (1.0.0), independent of the plugin's lockstep version discipline — that
  discipline covers plugin release locations only (§54).

## 3. Publishing (human checklist)

Upload requires a WorkBuddy platform account and is performed by the
operator, not by automation. The step-by-step checklist — package contents,
endpoint paste, expected callback URI display, scope checkboxes, and the §63
P0 self-test paths (401 discovery, sign-up → create agent → authorize,
`tools/list` → `ha_challenges` → `ha_predict` loop, revocation kills the
session) — lives in [`workbuddy/README.md`](../workbuddy/README.md).
