# Hosted Workspace AgentDraft Build Contract

This provider contract is for a HeadlineArena Workspace runtime that has already
verified and pinned the plugin bundle. It creates draft files only. It does not
register, deploy, activate, schedule, fund, or submit an agent, and it performs
no network calls.

The contract version is `workspace-conversation-v1`. The embedded Agent
Specification remains schema version integer `1`; these two versions describe
different boundaries.

## Request

```json
{
  "contract_version": "workspace-conversation-v1",
  "request_id": "req_agent_draft_001",
  "idempotency_key": "idem_agent_draft_001",
  "workspace_ref": "workspace:tenant-demo:conversation-demo",
  "task_kind": "agent_draft",
  "plugin_snapshot": {
    "name": "headlinearena-agent-plugin",
    "version": "1.35.0",
    "commit_sha": "762f693b2153f1ab451415a20fe05d50f04c8255",
    "skill": "ha-build-agent",
    "manifest_digest": "sha256:<64 lowercase hex characters>",
    "archive_digest": "sha256:<64 lowercase hex characters>"
  },
  "draft": {
    "draft_id": "draft_001",
    "version": 1,
    "spec": {
      "schema_version": 1
    },
    "output_directory": "forecast-agents/gold-macro-monitor"
  }
}
```

`spec` must contain the complete v1 object documented in
[agent-spec.md](agent-spec.md). The request accepts no additional envelope,
snapshot, or draft fields. Credential-bearing keys, `execution_mode`, and
deployment or registration claims are rejected even under `spec.extensions`.

`workspace_ref` is an opaque control-plane reference. It is never a filesystem
path. The trusted runtime supplies the tenant Workspace root as a separate
process argument. `output_directory` is a normalized POSIX-style relative path
inside that root. Absolute paths, `..`, `.`, repeated separators, backslashes,
symlink components, the Workspace root, and every existing destination are
rejected. There is no overwrite or force option.

The control plane owns idempotency and must resolve a repeated
`idempotency_key` to the prior result instead of invoking the builder twice.
The offline builder does not keep a second idempotency store.

## Result

```json
{
  "contract_version": "workspace-conversation-v1",
  "request_id": "req_agent_draft_001",
  "task_kind": "agent_draft",
  "plugin_snapshot": {
    "name": "headlinearena-agent-plugin",
    "version": "1.35.0",
    "commit_sha": "<40 lowercase hex characters>",
    "skill": "ha-build-agent",
    "manifest_digest": "sha256:<64 lowercase hex characters>",
    "archive_digest": "sha256:<64 lowercase hex characters>"
  },
  "draft": {
    "draft_id": "draft_001",
    "version": 1,
    "status": "draft",
    "spec_digest": "sha256:<64 lowercase hex characters>",
    "output_directory": "forecast-agents/gold-macro-monitor",
    "generated_files": [
      {
        "path": "forecast-agents/gold-macro-monitor/forecast-agent.json",
        "media_type": "application/json",
        "digest": "sha256:<64 lowercase hex characters>",
        "size_bytes": 1234
      },
      {
        "path": "forecast-agents/gold-macro-monitor/AGENT.md",
        "media_type": "text/markdown",
        "digest": "sha256:<64 lowercase hex characters>",
        "size_bytes": 1234
      }
    ]
  },
  "produced_at": "2026-09-22T12:00:00Z"
}
```

All returned paths are relative to the trusted Workspace root, and every file
path is beneath `output_directory`. Digests use `sha256:<64 lowercase hex>`.
`spec_digest` hashes the validated and normalized v1 spec as UTF-8 JSON with
keys sorted, no insignificant whitespace, and non-ASCII characters preserved.
It excludes generated audit timestamps. File digests hash the exact generated
UTF-8 bytes and therefore bind the audit metadata too.

The result intentionally omits the idempotency key, Workspace reference,
credentials, absolute paths, execution mode, and deployment or registration
claims.

## Offline invocation

Write the complete request to a temporary file outside the intended output
directory, then invoke:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_agent_build.py" \
  --request /path/to/agent-draft-request.json \
  --workspace /path/to/trusted-tenant-workspace
```

The adapter validates the envelope and calls the same
`scaffold_forecast_agent.scaffold_data` function used by the standalone and
Hermes builders. The runtime must verify the pinned bundle's name, version,
full commit SHA, manifest digest, and archive digest before constructing the
request. The adapter never fetches or upgrades the plugin.

The canonical provider fixture is
`tests/fixtures/workspace_agent_build_v1.json`.
