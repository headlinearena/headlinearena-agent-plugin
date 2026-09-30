# CAX-20P — WorkBuddy MCP + Hosted Builder 合流结果（1.39.0 RC）

日期：2026-09-30
分支：`codex/cax20p-workbuddy-builder-convergence-claude`
基线：`c14e5871` = tag `v1.38.0`（origin/main）
Builder 来源：`origin/codex/c3-plugin-release-candidate-root` @ `a4372d6d`
合流 commit：`3bfc22e1a0ca32cf910ee9a48ffd430edac41899`
候选版本：**1.39.0**（未打 tag，未发布 Release —— 最终 tag 由集成负责人创建）

## 合流方式

`git merge --no-commit a4372d6d`（merge-base `358b818b`，1.36 时代），逐个冲突手工确认：

- **冲突文件（14 个）全部为版本号/描述冲突**：9 个 `skills/*/SKILL.md`（version 1.38.0 vs 1.36.0）、
  `.claude-plugin/marketplace.json`、`.codex-plugin/plugin.json`、`plugin.yaml`、`scripts/ha.py`。
  `scripts/ha.py` builder 侧唯一改动就是 CLI_VERSION（已用 diff 证实），冲突取 main 侧
  ha_client 重构版全文。manifest 描述取两侧合并措辞（guided agent scaffolding + 完整能力列表）。
- **自动合并且逐项复核**：`ha_tools.py`（`HA_BUILD_AGENT_SCHEMA` + `handle_ha_build_agent` +
  `_TOOLS` 注册与 main 侧 ha_client import 共存）、`scripts/validate-skills.py`
  （PLUGIN_YAML_FILE/SCAFFOLD_FILE 版本源）、AGENTS.md / CLAUDE.md（ha-build-agent 行 +
  BUILDER_VERSION 规则/step 7）、README、CHANGELOG。
- **CHANGELOG 修正**：builder 分支自带的 "## 1.36.0" 条目从未以该号发布（main 的真实 1.36.0
  在历史位置），重命名为 **## 1.39.0** 并加合流说明；"publish plugin v1.36.0" rollout 行同步改。

## 版本 1.39.0 同步点（check_version_sync.py 确认 14 处一致）

skills/×10 SKILL.md（含新 ha-build-agent）、marketplace.json、plugin.json、plugin.yaml、
`CLI_VERSION`（scripts/ha.py）、`BUILDER_VERSION`（scripts/scaffold_forecast_agent.py）。
另更新 builder 侧 1.36.0 字面量：`test_scaffold_forecast_agent.py`、
`test_workspace_agent_build_provider.py`、`tests/fixtures/workspace_agent_build_v1.json`、
`skills/ha-build-agent/`（SKILL.md 宿主版本句 + workspace-build-contract.md 示例）、README。
fixture 的 `expected_result` 中 `forecast-agent.json` digest 因 audit.builder_version 变更而
重算（脚本比对确认唯一差异是该 digest，其余字节不变）。
`skills/ha-status/SKILL.md` 的 "Since v1.36.0" 为历史陈述，保留。
workbuddy/skills/*/SKILL.md 维持独立 1.0.0（与 v1.38.0 一致，不在同步范围内）。

## 验证结果

- 测试：13/13 个 `scripts/test_*.py` 全部 OK（含 4 个 builder 测试）。
- `scripts/validate-skills.py`：10 skills 全部通过。
- `scripts/check_version_sync.py`：`Version sync OK: 1.39.0 across 14 locations`（HEAD 未打 tag，tag 检查按设计跳过）。
- Manifest/JSON/YAML 语法校验通过（marketplace.json、plugin.json、fixture JSON、ha.py/ha_tools.py AST）。
- **WorkBuddy 零回退**：`git diff v1.38.0 -- workbuddy/ docs/mcp-integration.md docs/oauth.md docs/workbuddy.md scripts/ha_client/` 为空；
  `workbuddy/mcp.json` 指向 `https://mcp.headlinearena.com/mcp`；connector-meta 12 工具原样；
  en/zh examples ×4、三个 workbuddy skills、icon、README 齐全。
- **ha-build-agent 可发现**：marketplace.json skills 列表（10 项，首位）、plugin.yaml
  `provides_tools` 含 `ha_build_agent`、`skills/ha-build-agent/SKILL.md` 通过 validator。
- **无凭证泄漏**：diff 中新增的 secret 相关行均为敏感键 deny-list 与文档措辞；scaffolder/provider
  保持离线 fail-closed（网络调用在测试中被 mock 断言为 0）。

## Hash

- `plugin.yaml` SHA-256：`14457163708b1ae5332a880c3237cd39699199412ce3d63966757e8da754379b`
- 合流 commit `3bfc22e1a0ca32cf910ee9a48ffd430edac41899` 的本地确定性归档
  （`git archive --format=tar 3bfc22e1a0ca32cf910ee9a48ffd430edac41899 | sha256sum`，
  **本地 hash，非最终集成 commit 或 GitHub codeload hash**）：
  `53390fbfa38d49dda0ba6af0074dc4bb807e05d497018e06cef8d5183fc9b83c`

## 剩余风险

1. fixture 的 expected digest 与 BUILDER_VERSION 强耦合：今后每次版本 bump 都会使
   `workspace_agent_build_v1.json` 的 forecast-agent.json digest 失配，需随 bump 重算
   （本轮已重算；可考虑后续让 fixture digest 由测试动态推导）。
2. `skills/ha-build-agent/SKILL.md` 与 README 的 "hosted Workspace bundles plugin v1.39.0 or later"
   叙述在 Canvas Workspace 实际预装 1.39.0 之前不成立（CHANGELOG rollout 顺序已写明：先发插件、
   再更新 Workspace 默认预装、后部署 Canvas）。
3. 最终 tag `v1.39.0` 必须打在本 commit（或其后继）上并与 CLI_VERSION 严格一致，由集成负责人执行；
   check_version_sync 的 tag 检查会在打 tag 后重新生效。
