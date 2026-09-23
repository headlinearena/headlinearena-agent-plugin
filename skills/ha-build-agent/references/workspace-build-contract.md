# Hosted Workspace AgentDraft Build Contract

This internal provider contract is for a HeadlineArena Workspace runtime that has already
verified and pinned the plugin bundle. It creates draft files only. It does not
register, deploy, activate, schedule, fund, or submit an agent, and it performs
no network calls.

The contract version is `workspace-conversation-v1`. The embedded Agent
Specification remains schema version integer `1`; these two versions describe
different boundaries.

This is not the browser Workspace API contract. The browser never invokes this
script or supplies plugin metadata. The backend/control plane validates the
browser command, resolves tenant and idempotency state, constructs this internal
request, invokes the provider, and transforms the result into its persisted
`AgentDraft` and immutable `AgentVersion` models.

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
    "version": "1.36.0",
    "commit_sha": "0123456789abcdef0123456789abcdef01234567",
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
Every extension key must match ASCII `[A-Za-z][A-Za-z0-9_.-]{0,63}`. Credential
components such as `token`, `key`, `pat`, `jwt`, `oauth`, `secret`, `auth`, and
`password` are rejected at every nesting depth; `activation_function` remains
the sole explicit lifecycle-key exception.

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
    "version": "1.36.0",
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
JSON numbers use their shortest lossless decimal value expanded without exponent
notation; integral floats and negative zero normalize to integers. It excludes
generated audit timestamps. File digests hash the exact generated UTF-8 bytes
and therefore bind the audit metadata too.

The result intentionally omits the idempotency key, Workspace reference,
credentials, absolute paths, execution mode, and deployment or registration
claims.

## Offline one-shot invocation

The isolated hosted runtime passes the complete request only on stdin, never in
Docker argv, environment variables, labels, or a request file:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_agent_build_provider.py" \
  --request-stdin \
  --workspace /path/to/trusted-tenant-workspace \
  < /dev/stdin
```

The runtime fixes both the Workspace root and bundled-plugin root; neither comes
from the request. `BUNDLED_BUILD.json` must attest the immutable name, version,
commit, manifest SHA-256, archive SHA-256, required skill, and successful archive
verification. The entrypoint hashes the installed Codex manifest and verifies
those values before deriving the six-field provider snapshot. A request-supplied
snapshot is only compared with that trusted identity and is never trusted by
itself.

The trusted bundle metadata has this exact shape (digests are lowercase hex
without the wire contract's `sha256:` prefix):

```json
{
  "name": "headlinearena-agent-plugin",
  "version": "1.36.0",
  "commit": "<40 lowercase hex characters>",
  "manifest_sha256": "<64 lowercase hex characters>",
  "archive_sha256": "<64 lowercase hex characters>",
  "required_skill": "ha-build-agent",
  "archive_verified": true
}
```

For shared staging volumes, `--workspace` must name a fresh, runtime-created
per-job directory such as `/staging/<opaque-staging-ref>`. The provider retains
the request's logical `output_directory`, so the two files are created beneath
`/staging/<opaque-staging-ref>/<output_directory>/`; it does not flatten or move
them into the staging root.

stdin is bounded at 2 MiB and must contain one finite JSON value with no trailing
document or duplicate object keys. stdout contains exactly one bounded compact
JSON result on success. Failures emit only a stable error code on stderr, never
request content. The adapter validates the envelope and calls the same
`scaffold_forecast_agent.scaffold_data` function used by the standalone and
Hermes builders. It fails closed unless the request snapshot exactly matches
the derived trusted value. It never fetches or upgrades the plugin.

The canonical provider fixture is
`tests/fixtures/workspace_agent_build_v1.json`. Its commit and bundle digests
are explicitly synthetic contract-test values; they do not claim to identify
the commit containing this fixture.
