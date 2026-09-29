# HeadlineArena × WorkBuddy Connector 最终实施方案 v2.0

> 记录日期：2026-09-29
> 状态：已被 v3.0 取代（见 `workbuddy-connector-v3.md`）。保留本文作为历史记录：文末评审记录是 v3.0 的主要输入。
> 关联仓库事实核对：`cmd_predict` / `cmd_forecast` / `cmd_macro_predict` 均存在于 `scripts/ha.py`（1520/1617/1771 行）；旧 REST 端点 `/eval/macro/challenges/{id}/predict` 与 `/eval/human-forecasts/challenges/{id}/forecast` 在用（1632/1843/1862 行）；Hermes `plugin.yaml` 已注册 `ha_macro_predict` / `ha_macro_challenges` / `ha_macro_odds`；当前版本 1.37.0。

## 1. 项目目标

将 HeadlineArena 接入 WorkBuddy，使 WorkBuddy 用户能够通过自然语言完成：

- 注册 / 登录 HeadlineArena
- 创建或选择自己的 HeadlineArena Agent
- 发现当前可预测的 Challenge
- 对 Financial / Civic / Event 等不同类型 Challenge 提交预测
- 修订已有预测
- 查看预测结果、Consensus、Leaderboard、Scorecard
- 获取事件、评论、Feed 等上下文
- 使用自己的 Agent 身份调用 HeadlineArena

WorkBuddy 第一阶段发布形态：

**WorkBuddy Connector**

技术架构：

```text
WorkBuddy
   ↓
HeadlineArena Connector
   ↓
Remote MCP Server
   ↓
HeadlineArena Client / Services
   ↓
HeadlineArena Backend
```

Connector 内同时携带 WorkBuddy Skills，用于指导模型正确调用 MCP Tools。

---

## 2. 核心设计原则

### 2.1 WorkBuddy 第一阶段只做 Connector

暂不优先开发：

- Buddy App
- Expert
- 独立 Skill 产品

第一阶段：

```text
WorkBuddy
   ↓
HeadlineArena Connector
   ↓
MCP
```

后续路线：

```text
HeadlineArena Connector
        ↓
HeadlineArena Expert
        ↓
HeadlineArena Buddy App
```

### 2.2 所有预测统一使用 `ha_predict`

未来所有新 Agent 集成只暴露：

```text
ha_predict
```

不再向新 Agent 推荐：

```text
ha_forecast
ha_macro_predict
```

统一定义：

> `predict` 是 Agent 执行预测的动作。
> `forecast` 是预测结果、概率分布和领域数据模型概念。

因此内部仍允许存在：

```text
forecast_schema
forecast
forecast_revision
forecast_consensus
```

但公开 Action 统一为：

```text
ha_predict
```

---

## 3. HeadlineArena 新标准心智模型

以后所有 Agent 平台统一采用：

```text
DISCOVER
ha_challenges

        ↓

PREDICT
ha_predict

        ↓

EVALUATE
ha_results
ha_consensus
ha_scorecard
```

Agent 不应该知道不同 Backend route。

例如不应该要求 Agent 判断：

```text
financial endpoint
human forecast endpoint
legacy macro endpoint
```

这些全部由 HeadlineArena 内部处理。

---

## 4. Challenge Contract

`ha_challenges` 成为唯一 Challenge Discovery 入口。

建议统一返回：

```json
{
  "id": "challenge_xxx",
  "track": "civic",
  "question": "What will US CPI YoY be?",
  "status": "open",
  "deadline": "2026-10-10T12:00:00Z",

  "outcome_shape": "numeric_distribution",

  "prediction_schema": {
    "type": "numeric_distribution",
    "accepted_encodings": [
      "normal_mean_std",
      "samples"
    ]
  },

  "requires_stake": true,

  "required_scopes": [
    "prediction:submit",
    "credits:stake"
  ],

  "submit_tool": "ha_predict"
}
```

每个新 Challenge 必须至少声明：

```text
id
track
status
deadline
outcome_shape
prediction_schema
requires_stake
required_scopes
submit_tool
```

其中：

```text
submit_tool = ha_predict
```

Agent 必须根据 `prediction_schema` 构造提交。

禁止：

```text
看到 CPI
→ 自己假设 mean/std
```

必须：

```text
读取 Challenge Contract
→ 读取 prediction_schema
→ 构造 prediction
```

---

## 5. `ha_predict` 统一接口

推荐 MCP 输入结构：

```json
{
  "challenge_id": "...",
  "prediction": {},
  "reasoning": "...",
  "summary": "...",
  "amount": 100,
  "expected_revision": 2
}
```

核心字段：

```text
challenge_id
prediction
```

具体 `prediction` 内容由 Challenge Contract 决定。

---

## 6. Supported Prediction Shapes

### 6.1 Financial ternary

推荐：

```json
{
  "challenge_id": "gc_xxx",
  "prediction": {
    "probabilities": {
      "bearish": 0.20,
      "neutral": 0.25,
      "bullish": 0.55
    }
  },
  "reasoning": "..."
}
```

兼容简单形式：

```json
{
  "prediction": {
    "direction": "bullish",
    "confidence": 0.70
  }
}
```

### 6.2 Numeric distribution

```json
{
  "challenge_id": "cpi_xxx",
  "prediction": {
    "mean": 3.1,
    "std": 0.2
  },
  "amount": 100,
  "reasoning": "..."
}
```

或者：

```json
{
  "prediction": {
    "samples": [
      3.0,
      3.1,
      3.2,
      3.15
    ]
  }
}
```

### 6.3 Binary probability

```json
{
  "challenge_id": "xxx",
  "prediction": {
    "yes_probability": 0.63
  },
  "amount": 100
}
```

### 6.4 Ordered categorical distribution

```json
{
  "challenge_id": "xxx",
  "prediction": {
    "probabilities": {
      "cut": 0.55,
      "hold": 0.35,
      "raise": 0.10
    }
  },
  "amount": 100
}
```

---

## 7. Prediction Core 重构

现有：

```text
cmd_predict()
cmd_forecast()
cmd_macro_predict()
```

重构为：

```text
cmd_predict()
    ↓
discover_prediction_contract()
    ↓
validate_prediction()
    ↓
route_prediction()
```

内部允许保留：

```python
_predict_financial()
_predict_civic()
_predict_legacy_macro()
```

但这些属于内部实现。

核心目标：

```text
所有公开入口
     ↓
PredictionService.predict()
```

---

## 8. Shared HeadlineArena Client

不要让 MCP 再包一层 CLI。

错误架构：

```text
MCP
 ↓
ha_tools.py
 ↓
ha.py
 ↓
REST
```

目标：

```text
                  CLI
                   │
Hermes ───── HeadlineArenaClient ───── MCP
                   │
             PredictionService
                   │
              Backend API
```

建议新增：

```text
headlinearena_client/
├── client.py
├── auth.py
├── contracts.py
├── predictions.py
├── errors.py
└── models.py
```

公共 Client 统一负责：

```text
HTTP transport
authentication
challenge discovery
contract parsing
prediction validation
prediction routing
error normalization
```

---

## 9. Backward Compatibility

必须保护当前已经接入 HA Plugin 的：

```text
Claude Code
Codex
GitHub Copilot
Hermes
```

核心原则：

> 统一内部实现，不立即删除旧接口。

Canonical：

```text
ha_predict
```

Legacy interface 在兼容期继续工作。

---

## 10. CLI Compatibility

旧用户可能已经调用：

```bash
ha.py forecast ...
ha.py macro-predict ...
```

这些命令继续保留。

内部改成：

```python
def cmd_forecast(args):
    warn_deprecated("forecast", "predict")
    return cmd_predict(
        convert_forecast_args(args)
    )


def cmd_macro_predict(args):
    warn_deprecated("macro-predict", "predict")
    return cmd_predict(
        convert_macro_args(args)
    )
```

旧命令仍正常执行，但输出：

```text
Deprecated: `forecast` is retained for compatibility.
Use `predict` for new integrations.
```

---

## 11. Claude / Codex / Copilot Compatibility

旧 Session 可能仍根据旧 Skill 使用：

```text
forecast
macro-predict
```

新 Session 使用：

```text
predict
```

因此：

```text
Old Session
    ↓
legacy CLI command
    ↓
compatibility shim
    ↓
Prediction Core


New Session
    ↓
predict
    ↓
Prediction Core
```

升级 Plugin 不要求当前 Session 立即终止。

---

## 12. Hermes Compatibility

Hermes 当前 Native Tool Surface 中可能已经存在：

```text
ha_macro_predict
ha_macro_challenges
ha_macro_odds
```

兼容期不能直接删除。

继续注册这些 Tool，但标记 deprecated。

例如：

```python
def handle_ha_macro_predict(args, **kw):
    return handle_ha_predict({
        "challenge_id": args["challenge_id"],
        "prediction": {
            "mean": args["predicted_value"],
            "std": args["predicted_std"]
        },
        "amount": args["amount"],
        "reasoning": args.get("rationale")
    })
```

返回：

```json
{
  "_meta": {
    "deprecated_tool": "ha_macro_predict",
    "replacement": "ha_predict"
  }
}
```

这样不会出现：

```text
Tool not found
```

---

## 13. Backend REST Compatibility

旧 Plugin 可能长期不升级。

因此暂时继续支持：

```text
POST /eval/macro/challenges/{id}/predict

POST /eval/human-forecasts/challenges/{id}/forecast
```

内部统一：

```text
Legacy REST
    ↓
Compatibility Adapter
    ↓
PredictionService.predict()
```

新 MCP：

```text
ha_predict
    ↓
PredictionService.predict()
```

---

## 14. Deprecated Policy

旧接口至少遵循：

1. 不再增加新功能
2. 不再作为推荐接口
3. 不出现在 WorkBuddy
4. 不再进入新 examples
5. 继续修复重大兼容问题
6. 至少保留到下一个 Major Version
7. 删除前必须经过正式 Changelog + Migration Guide 公告

如果兼容层维护成本很低，可以长期保留。

---

## 15. WorkBuddy 不暴露 Legacy Tools

WorkBuddy 从第一版开始只看到新接口。

不得暴露：

```text
ha_macro_predict
ha_macro_challenges
ha_forecast
```

---

## 16. WorkBuddy MCP Tool Surface

建议 v1：

### Prediction

```text
ha_challenges
ha_predict
ha_results
ha_consensus
ha_paper_signals
```

### Research

```text
ha_events
ha_comments
ha_feed
```

### Performance

```text
ha_leaderboard
ha_scorecard
```

### Account

```text
ha_status
ha_credits
```

暂不暴露：

```text
ha_register
ha_challenge
ha_challenge_submit
ha_claim_link
ha_agents
ha_use
ha_update_check
ha_macro_*
```

---

## 17. WorkBuddy 用户注册设计

HeadlineArena 要求用户注册后才能使用，因此 WorkBuddy 正式方案使用：

**OAuth 2.1 + PKCE**

不采用"复制 Connector Token"的主方案。

用户体验：

```text
WorkBuddy
   ↓
安装 HeadlineArena Connector
   ↓
Connect
   ↓
浏览器打开 HeadlineArena
   ↓
已有用户 → Login
新用户   → Sign Up
   ↓
选择 / 创建 Agent
   ↓
Authorize WorkBuddy
   ↓
返回 WorkBuddy
   ↓
Ready
```

---

## 18. Human Account 与 Agent Identity 分离

HeadlineArena 中有两个不同主体：

```text
Human Account
+
Agent Identity
```

现有 Claude / Codex / Hermes onboarding 是：

```text
Agent first
↓
register
↓
challenge
↓
human claim
```

WorkBuddy 则是：

```text
Human first
↓
login / signup
↓
select or create Agent
↓
authorize
```

两种 onboarding 都保留。

---

## 19. WorkBuddy 新用户注册流程

### Scenario A — 已有 HA 账号和 Agent

```text
Install
↓
Connect
↓
Login
↓
Select Agent
↓
Authorize
↓
Ready
```

### Scenario B — 已有 HA 账号但没有 Agent

```text
Install
↓
Connect
↓
Login
↓
No agents found
↓
Create Agent
↓
Authorize
↓
Ready
```

### Scenario C — 完全没有 HA 账号

```text
Install
↓
Connect
↓
Create HeadlineArena Account
↓
Email verification（如果平台要求）
↓
Create Agent
↓
Authorize
↓
Return to WorkBuddy
↓
Ready
```

---

## 20. WorkBuddy Agent Provisioning

WorkBuddy 场景中用户已经通过 HA Login 完成身份验证。

因此创建 Agent 不需要再走：

```text
Agent challenge
+
claim_url
```

建议增加：

```text
create_owned_agent()
```

或者 API：

```text
POST /account/agents
```

创建：

```json
{
  "name": "WorkBuddy Forecaster",
  "hosting_mode": "connector",
  "scaffold_type": "workbuddy",
  "owner_id": "...",
  "status": "active"
}
```

因为 ownership 已经由 Human Session 明确建立。

现有自主 Agent 的：

```text
ha_register
challenge
claim
```

继续保留给 Claude / Codex / Hermes 等 Agent-first 场景。

---

## 21. 一个 WorkBuddy Connection 绑定一个 Agent

MVP 不建议一个 Connector Session 动态切换多个 Agent。

OAuth grant 固定绑定：

```text
user_id
+
agent_id
+
client_id
+
scopes
```

例如：

```text
WorkBuddy connection
      ↓
Agent A
```

如果用户要换 Agent：

```text
Reconnect
```

或者：

```text
HeadlineArena Account
→ Integrations
→ WorkBuddy
→ Change Agent
```

---

## 22. OAuth Token Claims

Access Token 至少能够解析：

```text
sub = user_id

agent_id = agent_xxx

client_id = workbuddy_xxx

scope =
  challenge:read
  prediction:submit
  profile:read:self
  credits:read
  comment:read:context
  ...
```

MCP Server 不再需要：

```text
agent_id + client_secret
```

也绝不读取：

```text
~/.headlinearena/credentials.json
```

作为 Remote MCP 多用户认证方案。

---

## 23. OAuth 需要实现的端点

至少实现：

```text
GET /.well-known/oauth-protected-resource

GET /.well-known/oauth-authorization-server

POST /oauth/register

GET /oauth/authorize

POST /oauth/token
```

要求：

```text
OAuth 2.1
PKCE S256
Authorization Code
Refresh Token
Dynamic Client Registration
```

---

## 24. `/oauth/authorize` 流程

如果用户未登录：

```text
/oauth/authorize
       ↓
Login / Sign Up
       ↓
恢复 OAuth transaction
```

注册完成不能要求用户重新开始 Connect。

注册完成后继续：

```text
Select Agent
or
Create Agent
       ↓
Authorization Consent
       ↓
redirect_uri
```

---

## 25. OAuth Security

必须满足：

```text
PKCE S256
state validation
redirect URI validation
short-lived authorization code
short-lived access token
refresh token rotation
revocation support
scope enforcement
```

禁止：

```text
WorkBuddy 获取用户 HA 密码
```

WorkBuddy 只拿 OAuth token。

---

## 26. Remote MCP

部署：

```text
https://mcp.headlinearena.com/mcp
```

使用：

```text
streamableHttp
HTTPS
```

请求：

```text
Authorization: Bearer <HA OAuth Access Token>
```

MCP Authentication Middleware：

```text
Bearer token
   ↓
verify token
   ↓
resolve user_id
   ↓
resolve agent_id
   ↓
resolve scopes
   ↓
Tool Context
```

---

## 27. WorkBuddy Connector Package

建议：

```text
workbuddy/
├── connector-meta.json
├── mcp.json
├── icon.svg
└── skills/
    ├── ha-forecasting/
    │   └── SKILL.md
    ├── ha-research/
    │   └── SKILL.md
    └── ha-performance/
        └── SKILL.md
```

使用 OAuth 后不再需要：

```text
token-schema.json
```

---

## 28. `mcp.json`

示意：

```json
{
  "mcpServers": {
    "headlinearena": {
      "type": "streamableHttp",
      "url": "https://mcp.headlinearena.com/mcp",
      "timeout": 30000
    }
  }
}
```

认证由 MCP OAuth Discovery 完成。

---

## 29. WorkBuddy Skills

建议只做三个。

### ha-forecasting

负责：

```text
discover
→ research
→ predict
→ revise
→ results
```

核心规则：

```text
1. 必须先调用 ha_challenges
2. 不允许自己构造 challenge_id
3. 必须读取 prediction_schema
4. 所有预测统一调用 ha_predict
5. 不调用 legacy forecast / macro-predict
6. 修订必须说明新证据
```

### ha-research

负责：

```text
events
comments
feed
context
```

### ha-performance

负责：

```text
results
consensus
leaderboard
scorecard
```

---

## 30. MCP `ha_predict` Tool Description

建议至少明确：

```text
Submit or revise a prediction for any HeadlineArena challenge.

Always call ha_challenges first.

Construct `prediction` according to the challenge's returned
prediction_schema.

Never invent a challenge_id.

Some challenge types may require a credit stake.

This operation creates or modifies persistent prediction data.
```

Tool Description 本身属于 Agent-facing documentation。

---

## 31. Error Contract

所有 MCP Tool 使用统一错误结构：

```json
{
  "error": {
    "code": "INVALID_PREDICTION_SCHEMA",
    "message": "...",
    "recoverable": true,
    "action": "Call ha_challenges again and use the returned prediction_schema."
  }
}
```

至少定义：

```text
AUTH_REQUIRED
TOKEN_EXPIRED
TOKEN_REVOKED

CHALLENGE_NOT_FOUND
CHALLENGE_CLOSED

INVALID_PREDICTION_SCHEMA

MISSING_SCOPE
INSUFFICIENT_CREDITS

REVISION_CONFLICT

RATE_LIMITED
INTERNAL_ERROR
```

禁止向 Agent 泄漏内部 route 错误，例如：

```text
legacy macro endpoint returned 404
```

---

## 32. Repository Target Structure

```text
headlinearena-agent-plugin/

├── plugin.yaml
├── ha_tools.py
│
├── scripts/
│   └── ha.py
│
├── headlinearena_client/
│   ├── client.py
│   ├── auth.py
│   ├── contracts.py
│   ├── predictions.py
│   ├── errors.py
│   └── models.py
│
├── mcp_server/
│   ├── server.py
│   ├── auth.py
│   ├── oauth.py
│   ├── context.py
│   └── tools/
│       ├── prediction.py
│       ├── research.py
│       ├── social.py
│       └── performance.py
│
├── skills/
│   └── ...
│
├── docs/
│   ├── prediction-api.md
│   ├── challenge-contract.md
│   ├── migration-v1-to-unified-predict.md
│   ├── compatibility-policy.md
│   ├── mcp-integration.md
│   ├── oauth.md
│   ├── workbuddy.md
│   └── troubleshooting.md
│
└── workbuddy/
    ├── connector-meta.json
    ├── mcp.json
    ├── icon.svg
    └── skills/
```

OAuth 后端本身如果位于主 HeadlineArena backend repo，则无需强行放在 Plugin repo。

---

## 33. Documentation Deliverables

文档属于正式交付物。

必须新增 / 更新：

```text
README.md

docs/prediction-api.md

docs/challenge-contract.md

docs/migration-v1-to-unified-predict.md

docs/compatibility-policy.md

docs/mcp-integration.md

docs/oauth.md

docs/workbuddy.md

docs/troubleshooting.md

CHANGELOG.md
```

---

## 34. README 要求

README 第一屏明确：

```text
Discover → Predict → Evaluate
```

即：

```text
ha_challenges
      ↓
ha_predict
      ↓
ha_results / ha_scorecard
```

新 README 不再把：

```text
forecast
macro-predict
```

作为标准教程。

只在：

```text
Legacy Compatibility
```

中说明。

---

## 35. Prediction API Guide

`docs/prediction-api.md`

必须包括：

```text
Unified ha_predict
Supported outcome shapes
Financial example
Numeric example
Binary example
Ordered example
Revision example
Stake behavior
Validation rules
Success response
Error response
```

同时提供：

```text
CLI examples
MCP examples
```

---

## 36. Challenge Contract Guide

`docs/challenge-contract.md`

重点解释：

```text
track
deadline
outcome_shape
prediction_schema
requires_stake
required_scopes
submit_tool
```

明确：

> Client MUST follow `prediction_schema`.

---

## 37. Migration Guide

`docs/migration-v1-to-unified-predict.md`

必须明确：

```text
OLD                     NEW

forecast                predict
macro-predict           predict
ha_macro_predict        ha_predict
ha_macro_challenges     ha_challenges
ha_macro_odds           ha_consensus
```

说明：

> Existing legacy calls continue to work during the compatibility period.

---

## 38. Compatibility Policy

`docs/compatibility-policy.md`

建议正式规定：

```text
Minor release:
No intentional removal of public Agent interfaces.

Major release:
May remove interfaces that were previously deprecated.
```

标准流程：

```text
replacement
↓
deprecation
↓
warning
↓
migration guide
↓
compatibility period
↓
major release
```

---

## 39. OAuth Documentation

`docs/oauth.md`

必须包括：

```text
OAuth discovery
Dynamic client registration
Authorization code flow
PKCE
Refresh token
Scopes
Agent binding
Token revocation
Signup continuation
Security model
```

---

## 40. WorkBuddy Guide

`docs/workbuddy.md`

用户安装流程：

```text
1. Install HeadlineArena Connector
2. Click Connect
3. Login or Sign Up
4. Select or Create Agent
5. Authorize
6. Return to WorkBuddy
7. Start forecasting
```

提供 Prompt：

```text
看看 HeadlineArena 现在有什么可以预测。
```

```text
分析当前黄金 Challenge 并提交一个预测。
```

```text
预测下一次 CPI。
```

---

## 41. Troubleshooting

至少覆盖：

```text
OAuth login failed
OAuth callback failed
No Agent found
Token expired
Authorization revoked
Missing scope
Challenge closed
Invalid prediction schema
Insufficient credits
Revision conflict
MCP timeout
Tool unavailable
```

每个问题按：

```text
Symptom
Cause
Resolution
```

编写。

---

## 42. Documentation CI

所有文档中的可执行 Example 尽量进入测试。

避免：

```text
代码更新
↓
文档 Example 失效
↓
无人发现
```

至少验证：

```text
CLI command parsing
ha_predict payload examples
prediction schema examples
MCP tool examples
```

---

## 43. 实施阶段

### Phase 1 — Unified Prediction Core

交付：

```text
ha_challenges
ha_predict
Prediction Contract
Prediction Router
```

完成：

- Financial
- Numeric
- Binary
- Ordered
- Revision
- Stake
- Legacy routing

同时完成：

```text
forecast compatibility shim
macro-predict compatibility shim
Hermes aliases
Backend legacy adapters
```

---

## 44. Phase 2 — Shared Client

新增：

```text
headlinearena_client/
```

迁移：

```text
HTTP
Auth
Discovery
Validation
Routing
Errors
```

目标：

```text
CLI / Hermes / MCP
```

共用同一实现。

---

## 45. Phase 3 — OAuth + User Registration

实现：

```text
OAuth discovery
Dynamic Client Registration
PKCE
Login
Signup
Signup continuation
Agent selection
Agent provisioning
Authorization
Access token
Refresh token
Revocation
```

同时实现：

```text
one connection = one agent
```

---

## 46. Phase 4 — Remote MCP

部署：

```text
https://mcp.headlinearena.com/mcp
```

实现：

```text
initialize
tools/list
tools/call
OAuth auth middleware
Agent context
error normalization
```

---

## 47. Phase 5 — WorkBuddy Connector

创建：

```text
connector-meta.json
mcp.json
icon.svg
skills/
```

上传 WorkBuddy 开放平台。

完成：

```text
Preview
OAuth
Tool discovery
E2E
Submission
```

---

## 48. Phase 6 — Documentation & Release

发布前完成：

```text
README
Prediction API
Challenge Contract
Migration Guide
Compatibility Policy
OAuth Guide
MCP Guide
WorkBuddy Guide
Troubleshooting
CHANGELOG
```

---

## 49. E2E Acceptance Tests

### New WorkBuddy user

```text
Install
→ Connect
→ Sign Up
→ Create Agent
→ Authorize
→ ha_challenges
→ ha_predict
→ ha_results
```

成功。

### Existing HA user

```text
Connect
→ Login
→ Select Agent
→ Authorize
→ Predict
```

成功。

### Existing HA user with multiple Agents

必须能够选择一个 Agent。

OAuth grant 必须固定到该 Agent。

### Token refresh

Access token 过期后：

```text
refresh token
→ new access token
→ MCP continues
```

### Revocation

HA 用户撤销 WorkBuddy 后：

```text
MCP
→ AUTH_REQUIRED / TOKEN_REVOKED
```

并要求重新 Connect。

### Financial

```text
ha_challenges
→ ha_predict
```

成功。

### Civic numeric

```text
ha_challenges
→ ha_predict(mean/std)
```

成功。

### Civic binary

```text
ha_challenges
→ ha_predict(yes_probability)
```

成功。

### Civic ordered

```text
ha_challenges
→ ha_predict(probabilities)
```

成功。

---

## 50. Compatibility Acceptance Tests

必须验证：

```bash
ha.py forecast ...
```

仍成功。

必须验证：

```bash
ha.py macro-predict ...
```

仍成功。

Hermes：

```text
ha_macro_predict
```

仍成功。

旧 REST：

```text
/macro/.../predict
```

仍成功。

旧 Human Forecast：

```text
/human-forecasts/.../forecast
```

仍成功。

同时：

```text
WorkBuddy
```

不能看到任何 legacy prediction tool。

---

## 51. Security Acceptance

必须满足：

- WorkBuddy 不接触 HA 密码
- OAuth 使用 PKCE
- OAuth state 校验
- redirect URI 严格校验
- access token 短期有效
- refresh token 可轮换
- grant 可撤销
- 一个 connection 固定一个 Agent
- Agent A 无法读写 Agent B 私有状态
- Scope 强制检查
- 不在日志记录完整 token
- Remote MCP 不依赖本地 credentials.json

---

## 52. Documentation Definition of Done

发布前必须：

- [ ] README 更新
- [ ] Prediction API Guide
- [ ] Challenge Contract Guide
- [ ] Migration Guide
- [ ] Compatibility Policy
- [ ] OAuth Guide
- [ ] MCP Integration Guide
- [ ] WorkBuddy Guide
- [ ] Troubleshooting
- [ ] CLI Help 更新
- [ ] Tool descriptions 完整
- [ ] WorkBuddy Skills 完成
- [ ] CHANGELOG 完成
- [ ] Deprecation warning 指向 Migration Guide
- [ ] Examples 通过 CI
- [ ] WorkBuddy 核心用户说明中英文完成

---

## 53. Product Definition of Done

WorkBuddy Connector v1 发布必须满足：

- [ ] `ha_challenges` 是唯一 discovery 入口
- [ ] `ha_predict` 是唯一新预测入口
- [ ] Financial 支持
- [ ] Numeric 支持
- [ ] Binary 支持
- [ ] Ordered categorical 支持
- [ ] Revision 支持
- [ ] Stake 支持
- [ ] WorkBuddy OAuth 注册成功
- [ ] WorkBuddy OAuth 登录成功
- [ ] Agent 创建成功
- [ ] Agent 选择成功
- [ ] OAuth grant 与 Agent 绑定
- [ ] Refresh token 正常
- [ ] Revocation 正常
- [ ] Remote MCP 正常
- [ ] Existing Claude / Codex / Hermes integration 不被破坏
- [ ] Legacy REST 不被破坏
- [ ] WorkBuddy 不暴露 legacy tools
- [ ] 所有关键文档完成
- [ ] 全部 E2E 测试通过

---

## 54. 最终目标架构

```text
                         HeadlineArena
                              │
                      PredictionService
                              │
                      Unified ha_predict
                              │
         ┌────────────────────┼─────────────────────┐
         │                    │                     │
      Financial             Civic              Future Types
         │                    │
         └──────── Prediction Contract ─────────────┘


Existing Agent Hosts                         WorkBuddy
        │                                       │
Claude / Codex / Hermes                   OAuth 2.1 + PKCE
        │                                       │
CLI / Native Tools                      Remote MCP
        │                                       │
        └──────── HeadlineArenaClient ──────────┘
                         │
                  PredictionService
```

兼容层：

```text
forecast
macro-predict
ha_macro_predict
legacy REST
        ↓
Compatibility Adapter
        ↓
Same PredictionService
```

---

## 55. 最终开发原则

本项目必须坚持五条原则：

**1. 一个公开预测动作**

```text
ha_predict
```

**2. Challenge 决定 Schema**

```text
ha_challenges
→ prediction_schema
```

而不是 Agent 猜参数。

**3. 新旧接口共享同一个 Prediction Core**

不是维护两套业务逻辑。

**4. WorkBuddy 使用 Human-first OAuth onboarding**

```text
Login / Signup
→ Select/Create Agent
→ Authorize
```

而 Claude / Codex / Hermes 保留 Agent-first：

```text
register
→ challenge
→ claim
```

**5. 兼容和文档属于正式功能**

任何 breaking change 都必须有：

```text
Compatibility Layer
Migration Guide
Changelog
Deprecation Warning
Documentation
Tests
```

最终目标不是单独"做一个 WorkBuddy 插件"，而是借这次接入，把 HeadlineArena 的 Agent Integration 正式统一成一套可长期支持：

```text
Claude
Codex
Hermes
WorkBuddy
MCP clients
Future Agent Platforms
```

的标准预测能力。

---

---

# 评审记录（2026-09-29）

## 结论

**方案整体合理，可以执行。** 核心决策（统一 `ha_predict`、Contract-driven schema、Human-first OAuth、分层兼容策略）方向正确，且对现状的引用经仓库核对全部属实。存在 **5 个动工前必须解决的问题** 和若干建议补充项。

## 现状核对（全部通过）

| 计划引用 | 仓库事实 | 结论 |
|---|---|---|
| `cmd_predict` / `cmd_forecast` / `cmd_macro_predict` | `scripts/ha.py:1520/1771/1617` | ✅ |
| 旧 REST `/eval/macro/.../predict`、`/eval/human-forecasts/.../forecast` | `ha.py:1632/1843/1862` | ✅ |
| Hermes 已有 `ha_macro_predict` / `ha_macro_challenges` / `ha_macro_odds` | `plugin.yaml` provides_tools | ✅ |
| `ha_paper_signals` | `cmd_paper_signals`（ha.py:1584） | ✅ |
| 当前无 `docs/` 目录 | 属实 | ✅ |

## 必须解决的问题（P0）

1. **Revision 查询缺口。** `ha_predict` 有 `expected_revision`、错误码有 `REVISION_CONFLICT`，但第 16 节工具面没有任何工具能查到"我当前预测的 revision"。`ha_results` 是挑战结果，不是"我的预测"。触发 `REVISION_CONFLICT` 后 Agent 无从恢复。需增加 `ha_predictions`（list/get 我的预测 + 当前 revision），或在 `ha_challenges` / `ha_predict` 响应中携带当前 revision。

2. **资金护栏不足。** `requires_stake` + `amount` 意味着自然语言 Agent 可直接花用户积分。现有 CLI 已有 wallet spending limits（`cmd_wallet_policy`），方案必须明确：PredictionService 强制执行 wallet policy；Contract 或工具描述暴露最大 stake 上限；超阈值时的确认/拒绝行为；并在第 51 节安全验收中增加对应测试项。消费级开放平台上这是硬要求。

3. **DCR 安全策略缺失。** 第 23 节要求开放 `POST /oauth/register`（Dynamic Client Registration）。生产环境无认证的 DCR 是已知攻击面（RFC 7591 无内建防滥用，RFC 9447 专门处理此问题）。WorkBuddy 是已知平台——建议优先预注册 client_id 白名单，DCR 仅限审核过的平台或要求 software statement，并写入第 25 节。

4. **OAuth 端点与 MCP 401 细节。** 第 25 节要求 revocation、第 49 节有撤销测试，但第 23 节端点清单缺 `POST /oauth/revoke`（RFC 7009）。另外第 26/46 节未提：MCP 端未认证时须返回 401 + `WWW-Authenticate`（指向 protected-resource metadata，RFC 9728），否则 WorkBuddy 的 MCP 客户端无法自动触发 OAuth 流程。

5. **`mcp_server/` 的仓库归属未决。** 本仓库是插件分发仓库（安装到用户机器）。把 Remote MCP Server 部署代码放在这里，部署产物与客户端分发耦合，且每次插件更新携带 server 代码。建议：`headlinearena_client/`（CLI 依赖，留下）、`workbuddy/`（分发产物，留下）留在本仓库；`mcp_server/` 放主 backend repo 或独立 repo，本仓库只引用 URL。需在方案中明确决策而非"建议结构"。

## 建议补充（P1）

6. **Phase 2 工程风险：** `ha.py` 现为 2226 行单文件，所有 skill 以 `python3 scripts/ha.py` 调用。拆出 `headlinearena_client/` 包后需处理 import 路径（marketplace 安装位置不定）。`ha_tools.py`（Hermes）目前是包 CLI 的一层，迁移为直接共用 client 应列为 Phase 2 交付物。
7. **版本纪律：** 本仓库 CLAUDE.md 规定 8 处版本号必须同步且与 git tag 一致。6 个 Phase 会产生多次 release，方案未提。建议每个 Phase 交付清单加入"版本同步 + CHANGELOG + tag"，并预先判定 bump 类型（Phase 1 属 minor：新增统一入口、保留旧接口）。
8. **幂等性：** `ha_predict` 涉及扣 stake，网络重试可能双重扣款。建议支持 `Idempotency-Key`，或明确用 `(challenge_id, agent_id, revision)` 做天然幂等键。
9. **Contract 补充字段：** civic numeric 挑战应带 `resolution`（数据源/口径/日期，如哪个 BLS release），否则 Agent reasoning 与打分口径可能错位。numeric 分布与概率分布的校验规则（std>0、samples 下限、概率和 = 1 ± ε）应在第 4 节契约中列出。
10. **简单形式二义性：** 第 6.1 节 `{direction, confidence}` 兼容形式未定义规范化语义（confidence 如何映射为三元概率）。建议 v1 只收完整概率分布，或明确映射规则。
11. **`amount` 语义：** `requires_stake=false` 的挑战应忽略还是禁止 `amount`，需写入 validation rules。
12. **Migration 表核对：** `ha_macro_odds → ha_consensus` —— 现实现中 macro odds（`/eval/macro/.../odds`）与 human-forecast consensus（`/public/human-forecasts/.../consensus`）是两个不同端点（ha.py:1873/1878），迁移文档需确认语义确实对应。
13. **非功能性内容：** 方案缺时间/人力估算、监控与审计（stake 花费日志、OAuth 授权审计）、灰度与回滚方案（Prediction Core 重构影响所有现有 host，建议 feature flag 按 host 灰度）。

## 做得好的地方

- predict（动作）/ forecast（领域概念）术语切分清晰，从命名上阻止 API 分裂。
- Contract-driven schema（"Agent 不猜参数"）直击 LLM Agent 最常见的误用模式。
- 兼容策略四层完整：CLI shim + Hermes alias + REST adapter + 明确的删除流程（deprecation → 兼容期 → major）。
- Human-first 与 Agent-first 两种 onboarding 分开保留，不强行统一。
- 三份验收清单（E2E / 兼容 / 安全）具体可执行；文档作为交付物并进 CI。
- 事实准确：对现有代码的所有引用经核对无误。
