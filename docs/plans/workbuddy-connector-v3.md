# HeadlineArena × WorkBuddy Connector 最终开发实施方案 v3.0

> 记录日期：2026-09-29
> 状态：**开发基线**——v2 评审及 v3 遗留问题已全部并入正文（见文末「评审记录 · 修订记录」），作为 Phase 0 冻结基线输入
> 取代：v2.0（`docs/plans/workbuddy-connector-v2.md`，其评审记录是本版的主要输入）
> 现状核对补充：v3 §16 的前提已证实——`cmd_wallet_policy` docstring（ha.py:1056）明确 `per_tx_limit` 是单次充值上限且 "the platform has no separate per-prediction credit limit today"；v3 §21 的撤回已证实——`cmd_macro_odds`（ha.py:1871）为 404 后 fallback 到 consensus 的兼容行为。

## 0. 本文用途

本文是开发实施基线。

目标不是单独"做一个 WorkBuddy 插件"，而是借 WorkBuddy 接入完成 HeadlineArena Agent Integration 的一次标准化，使未来：

- Claude Code
- Codex
- GitHub Copilot
- Hermes
- WorkBuddy
- 标准 MCP Client
- 未来其他 Agent 平台

能够建立在同一个预测核心之上。

开发过程中如实现与本文冲突，应优先保持以下原则：

1. 不破坏现有用户。
2. 新接口只使用统一 `ha_predict`。
3. 资金操作必须服务端强制保护。
4. 所有写操作必须具备幂等能力。
5. Agent 必须能够读取自己的当前预测状态并从冲突中恢复。
6. WorkBuddy 使用 Human-first OAuth，不使用服务器端共享 credentials 文件。
7. Remote MCP 属于 HeadlineArena Backend，不属于 Plugin Repo。
8. 文档、兼容、迁移和测试属于正式交付物。

---

# 1. 项目目标

WorkBuddy 用户安装 HeadlineArena Connector 后，可以：

```text
注册 / 登录 HeadlineArena
        ↓
选择或创建 HeadlineArena Agent
        ↓
Authorize WorkBuddy
        ↓
发现预测 Challenge
        ↓
研究证据
        ↓
提交 / 修改预测
        ↓
查看自己的预测
        ↓
查看最终结果与表现
```

第一阶段产品形态：

**WorkBuddy Connector**

技术方式：

**Remote MCP + Skill + OAuth**

WorkBuddy 官方对于已有网络 API 的服务推荐 MCP + Skill，远程 MCP 使用 HTTPS，并支持 `streamableHttp`；当 MCP Server 自带 OAuth 时，WorkBuddy 会执行标准 OAuth discovery、DCR、PKCE 和 token refresh。

---

# 2. 最终架构

整体：

```text
                         HeadlineArena Backend
                                │
                 ┌──────────────┼──────────────┐
                 │              │              │
              REST API         MCP           OAuth
                 │              │              │
                 └──────────────┼──────────────┘
                                │
                        Domain Services
                                │
                  ┌─────────────┴─────────────┐
                  │                           │
          PredictionService             Other Services
                  │
             Domain / DB


Existing Plugin Hosts                         WorkBuddy
        │                                         │
Claude / Codex / Hermes                   Connector Package
        │                                         │
CLI / Native Tools                         Remote MCP
        │                                         │
        └──────────────── Backend ────────────────┘
```

重点：

**REST 和 MCP 都是 Backend 的 transport layer。**

不要：

```text
MCP
↓
ha.py
↓
REST
↓
Backend
```

应该：

```text
REST ─────┐
          ├→ PredictionService
MCP ──────┘
```

---

# 3. Repository Ownership —— 已正式决定

## 3.1 HeadlineArena Backend Repo

Remote MCP 和 OAuth 代码必须放 Backend Repo。

建议：

```text
backend/
├── api/
│   └── ...
│
├── oauth/
│   ├── metadata.py
│   ├── registration.py
│   ├── authorize.py
│   ├── token.py
│   ├── revoke.py
│   ├── models.py
│   └── security.py
│
├── mcp/
│   ├── server.py
│   ├── auth.py
│   ├── context.py
│   ├── errors.py
│   └── tools/
│       ├── prediction.py
│       ├── research.py
│       ├── performance.py
│       └── account.py
│
├── services/
│   ├── prediction_service.py
│   ├── stake_policy_service.py
│   └── ...
│
└── models/
```

不要在：

```text
headlinearena-agent-plugin/
```

建立生产运行用的：

```text
mcp_server/
```

## 3.2 Agent Plugin Repo

继续承担：

```text
headlinearena-agent-plugin/

├── plugin.yaml
├── ha_tools.py
├── scripts/
│   ├── ha.py
│   └── ...
├── skills/
├── docs/
└── workbuddy/
```

其中：

```text
workbuddy/
```

仅保存 **WorkBuddy Connector 发布资产**：

```text
workbuddy/
├── connector-meta.json
├── mcp.json
├── icon.svg
└── skills/
```

`mcp.json` 只是：

> 告诉 WorkBuddy 到哪里连接 Remote MCP。

不是 MCP Server 源码。

---

# 4. Prediction 产品模型统一

新 Agent 世界只保留三个核心动作：

```text
DISCOVER
ha_challenges

READ MY STATE
ha_predictions

PREDICT
ha_predict
```

然后：

```text
EVALUATE
ha_results
ha_scorecard
```

核心流程：

```text
ha_challenges
       ↓
Challenge Contract
       ↓
Agent research / reasoning
       ↓
ha_predict
       ↓
ha_predictions
       ↓
ha_results
```

---

# 5. `ha_predict` 是唯一新的预测动作

以后新 integration 不再使用：

```text
ha_forecast
ha_macro_predict
```

统一：

```text
ha_predict
```

定义：

> `predict` = Agent 执行动作
> `forecast` = domain object / prediction distribution

所以内部仍然允许：

```text
forecast_schema
forecast_revision
forecast_consensus
forecast_distribution
```

这不与：

```text
ha_predict
```

冲突。

---

# 6. Unified Challenge Contract

`ha_challenges` 成为唯一 Discovery API。

每个 Challenge 至少返回：

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

  "resolution": {
    "criteria": "...",
    "authority": "...",
    "reference_series": "...",
    "observation_period": "...",
    "publication_policy": "...",
    "evidence_policy_version": "...",
    "primary_publication_required": true,
    "verification_classes": []
  },

  "submit_tool": "ha_predict"
}
```

---

# 7. Resolution Contract —— P0

这是本次必须新增/规范化的部分。

Prediction Contract 不只是：

```text
Question
+
Prediction Schema
```

而是：

```text
Question
+
Prediction Schema
+
Resolution Contract
```

例如 CPI 必须能明确：

```text
Headline / Core
MoM / YoY
哪个 observation period
季调 / 非季调
哪个 authority
哪个 series
first release / revised value
settlement evidence policy
```

否则 Agent 可能在预测一个概念，而平台最终 settle 另一个概念。

Financial Challenge 现有：

```text
resolution_criteria
```

应投影为：

```text
resolution.criteria
```

Civic 已有的：

```text
settlement_authority
evidence_policy_version
primary_publication_required
verification_classes
```

应完整进入统一 Contract。

原则：

> Agent 不得根据 asset 名称猜 settlement definition。

---

# 8. Unified `ha_predict`

MCP 输入：

```json
{
  "challenge_id": "xxx",

  "prediction": {},

  "reasoning": "...",

  "summary": "...",

  "amount": 100,

  "expected_revision": 2,

  "idempotency_key": "..."
}
```

必填：

```text
challenge_id
prediction
```

其他字段根据 contract 决定。

---

# 9. Prediction Shapes

## 9.1 Financial ternary

```json
{
  "prediction": {
    "probabilities": {
      "bearish": 0.20,
      "neutral": 0.25,
      "bullish": 0.55
    }
  }
}
```

Legacy 简单形式——仅保留给旧 CLI `ha.py predict --direction --confidence` 路径；MCP `ha_predict` 一律拒绝并返回 `INVALID_PREDICTION_SCHEMA`：

```json
{
  "prediction": {
    "direction": "bullish",
    "confidence": 0.70
  }
}
```

服务端按显式公式规范化（不留给实现猜测空间）：

```text
p(direction)        = confidence
remaining           = 1 - confidence
p(其余两个 outcome)  = remaining / 2（均分）
```

即上例规范化为：

```json
{
  "probabilities": {
    "bearish": 0.15,
    "neutral": 0.15,
    "bullish": 0.70
  }
}
```

规范化只发生在 Compatibility Adapter 层；进入 `PredictionService` 的 prediction 永远是完整概率分布，且三项之和必须为 1（±1e-6 容差）、任何一项不得为负。

## 9.2 Numeric distribution

```json
{
  "prediction": {
    "mean": 3.1,
    "std": 0.2
  }
}
```

或者：

```json
{
  "prediction": {
    "samples": [
      3.0,
      3.1,
      3.15,
      3.2
    ]
  }
}
```

## 9.3 Binary

```json
{
  "prediction": {
    "yes_probability": 0.63
  }
}
```

## 9.4 Ordered categorical

```json
{
  "prediction": {
    "probabilities": {
      "cut": 0.55,
      "hold": 0.35,
      "raise": 0.10
    }
  }
}
```

---

# 10. PredictionService

所有新旧入口最终进入：

```text
PredictionService.predict()
```

建议：

```python
PredictionService.predict(
    agent_id,
    challenge_id,
    prediction,
    reasoning=None,
    summary=None,
    amount=None,
    expected_revision=None,
    idempotency_key=None,
)
```

内部：

```text
load contract
      ↓
validate deadline
      ↓
validate schema
      ↓
validate scopes
      ↓
validate stake policy
      ↓
check idempotency
      ↓
check revision
      ↓
route execution
      ↓
atomic transaction
      ↓
persist
      ↓
return canonical response
```

---

# 11. 内部 Prediction Router

可以内部存在：

```text
_predict_financial()
_predict_civic()
_predict_legacy_macro()
```

但调用方看不到。

Canonical route：

```text
ha_predict
     ↓
PredictionService
     ↓
internal router
```

---

# 12. `ha_predictions` —— P0

新增：

```text
ha_predictions
```

用途：

> 查询当前 Agent 自己已经提交的预测及 revision 状态。

这是 `expected_revision` 能真正工作的必要 read path。

支持：

```json
{
  "challenge_id": "optional",
  "status": "optional",
  "limit": 20,
  "cursor": "optional"
}
```

单 Challenge 返回建议：

```json
{
  "challenge_id": "xxx",

  "prediction_id": "pred_xxx",

  "revision_number": 3,

  "prediction": {
    "mean": 3.1,
    "std": 0.2
  },

  "stake": {
    "amount": 100,
    "status": "locked"
  },

  "created_at": "...",

  "updated_at": "..."
}
```

---

# 13. Revision Recovery —— P0

所有 `ha_predict` 成功响应都必须返回：

```text
prediction_id
revision_number
```

例如：

```json
{
  "prediction_id": "pred_123",
  "revision_number": 4,
  "status": "accepted"
}
```

发生并发冲突：

```json
{
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "Prediction has been modified.",
    "recoverable": true,
    "current_revision": 5,
    "action": "Call ha_predictions for the latest state before retrying."
  }
}
```

Agent 恢复流程：

```text
REVISION_CONFLICT
        ↓
ha_predictions(challenge_id)
        ↓
读取 revision_number
        ↓
重新判断是否仍要修改
        ↓
ha_predict(expected_revision=N)
```

---

# 14. Idempotency —— P0

所有会修改 prediction / stake 的操作必须支持幂等。

原因：

```text
Client POST
↓
Server success
↓
Response lost
↓
MCP retries
```

不能产生第二次写入或第二次 stake。

统一使用：

```text
Idempotency-Key
```

MCP schema 中也允许：

```text
idempotency_key
```

Backend 唯一约束建议：

```text
(agent_id, idempotency_key)
```

相同 key + 相同 request：

```text
return original response
```

相同 key + 不同 request body：

```text
IDEMPOTENCY_KEY_REUSED
```

禁止重新执行。

---

# 15. Idempotency 与 Revision 的区别

必须同时存在。

```text
Idempotency-Key
```

解决：

> 同一个逻辑请求被网络重复发送。

而：

```text
expected_revision
```

解决：

> 两个不同客户端同时修改同一个 prediction。

二者不能互相替代。

---

# 16. Stake / Credit Safety —— P0

现有：

```text
wallet_policy
```

不能被视为 prediction stake policy。

当前 wallet policy 控制的是：

```text
max_balance

per_tx_limit
```

其中 `per_tx_limit` 是：

> owner → agent wallet top-up limit

不是：

> per prediction stake limit

因此必须新增真正的：

```text
StakePolicy
```

---

# 17. StakePolicy

建议至少支持：

```json
{
  "max_stake_per_prediction": 100,

  "max_total_locked_stake": 500,

  "daily_stake_limit": 1000,

  "require_confirmation_above": 50
}
```

字段语义：

### max_stake_per_prediction

单个 prediction/revision 最大可锁定 credits。

### max_total_locked_stake

该 Agent 同时处于 locked 状态的 stake 总量上限。

### daily_stake_limit

一个自然日（固定 UTC 日界）内允许新建/增加的 stake 总量。

时区固定为 UTC，避免用户本地时区造成边界漂移。StakePolicy 查询与 `STAKE_POLICY_EXCEEDED` 错误响应中必须携带 `daily_window: {"timezone": "UTC", "reset_at": "<下一个 UTC 日界，ISO8601>"}`，让 Agent 与用户能判断剩余额度。

### require_confirmation_above

如果平台未来支持 interactive confirmation，可用于决定何时必须 human confirmation。

---

# 18. Stake Policy 必须由 Backend 强制

不能只写到 Skill：

```text
Don't stake more than 100.
```

必须：

```text
ha_predict
     ↓
PredictionService
     ↓
StakePolicyService.evaluate()
```

超限：

```json
{
  "error": {
    "code": "STAKE_POLICY_EXCEEDED",
    "requested": 300,
    "max_stake_per_prediction": 100,
    "recoverable": true
  }
}
```

---

# 19. WorkBuddy 权限模型

WorkBuddy Agent 不允许自己调用：

```text
ha_scope(add=["credits:stake"])
```

提升自己的资金权限。

因此：

**WorkBuddy v1 不暴露 `ha_scope`。**

资金相关 Scope：

```text
credits:stake
```

必须由用户在 OAuth consent 时明确授权。

流程：

```text
Human
↓
OAuth Consent
↓
Allow prediction stake?
↓
grant credits:stake
```

然后即使拥有 scope：

```text
credits:stake
```

仍必须经过：

```text
StakePolicyService
```

所以是双重保护：

```text
OAuth Scope
+
Stake Policy
```

---

# 20. WorkBuddy Tool Surface

建议 v1：

## Prediction

```text
ha_challenges
ha_predictions
ha_predict
ha_results
ha_paper_signals
```

`ha_paper_signals` 定义（依据现有实现 `cmd_paper_signals`，ha.py:1584）：

> 读取当前 Agent**自己**在某个 Challenge（金融 ternary）收盘后的 paper-trade 信号，`GET /eval/challenges/{id}/paper-signals`，分页（limit ≤ 100 / cursor）。Agent 私有数据：须认证，且只能读自己的，用于收盘后复盘。

## Research

```text
ha_events
ha_comments
ha_feed
```

## Performance

```text
ha_leaderboard
ha_scorecard
```

## Account

```text
ha_status
ha_credits
```

---

# 21. `ha_consensus` 暂不强行上线

当前：

```text
legacy macro odds
```

与：

```text
canonical forecast consensus
```

语义并不相同。

现有兼容逻辑：

```text
/eval/macro/.../odds
```

404 后 fallback：

```text
/public/human-forecasts/.../consensus
```

只是兼容行为。

它不意味着：

```text
odds == consensus
```

所以禁止在 Migration Guide 中写：

```text
ha_macro_odds → ha_consensus
```

作为简单 rename。

v1 推荐：

- 保留 legacy `ha_macro_odds`
- WorkBuddy 暂不暴露
- 等 backend 定义统一 Consensus Contract 后再增加 `ha_consensus`

如果未来需要 pool odds，则另外设计：

```text
ha_odds
```

不要把 Odds 和 Consensus 混为一谈。

---

# 22. Backward Compatibility

必须保护：

```text
Claude Code
Codex
GitHub Copilot
Hermes
Old REST clients
```

原则：

> 新接口统一，旧入口兼容。

---

# 23. CLI Compatibility

继续支持：

```bash
ha.py forecast ...
ha.py macro-predict ...
```

它们内部：

```text
Legacy argument conversion
        ↓
PredictionService / unified predict
```

并输出：

```text
Deprecated: `forecast` is retained for compatibility.
Use `predict` for new integrations.
```

---

# 24. Hermes Compatibility

继续注册：

```text
ha_macro_predict
ha_macro_challenges
ha_macro_odds
```

避免已有 Hermes Agent 出现：

```text
Tool not found
```

但描述必须标记：

```text
Deprecated compatibility alias.
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

---

# 25. Backend REST Compatibility

继续支持旧 endpoint：

```text
POST /eval/macro/challenges/{id}/predict

POST /eval/human-forecasts/challenges/{id}/forecast
```

内部转换：

```text
legacy payload
        ↓
Compatibility Adapter
        ↓
PredictionService.predict()
```

旧 Plugin 即使长期不升级也不能因为 Backend 改造突然坏掉。

---

# 26. Compatibility Lifecycle

### Current major version

旧入口：

```text
supported
+
deprecated
```

### 新 Minor Release

所有：

```text
README
Skills
Examples
WorkBuddy
MCP
```

只教授：

```text
ha_predict
```

### 下一个 Major Release

再评估是否删除 legacy。

原则：

> 如果 shim 的维护成本低，允许长期保留。

---

# 27. `ha.py` 拆包策略 —— P1

当前 `scripts/ha.py` 已经是大型单文件，而且所有 Skill 都依赖：

```bash
python3 scripts/ha.py
```

因此不得进行 big-bang rewrite。

保持：

```text
scripts/ha.py
```

作为稳定入口。

推荐结构：

```text
scripts/
├── ha.py
└── ha_client/
    ├── __init__.py
    ├── transport.py
    ├── auth.py
    ├── contracts.py
    ├── prediction.py
    └── errors.py
```

这样 `ha.py` 直接：

```python
from ha_client.prediction import ...
```

不会因为 Plugin 安装路径不同导致 import 问题。

---

# 28. 拆包顺序

不要一次拆完。

### Step 1

提取：

```text
prediction validation
prediction routing
```

### Step 2

提取：

```text
challenge contract parsing
```

### Step 3

提取：

```text
HTTP / auth
```

### Step 4

再整理：

```text
legacy compatibility
```

每一步都必须保证：

```bash
python3 scripts/ha.py ...
```

仍然兼容。

---

# 29. WorkBuddy 用户认证模型

因为使用 HeadlineArena 服务必须注册账号，WorkBuddy 正式方案使用：

**OAuth + PKCE**

不用 Connector Token 作为主要 UX。

WorkBuddy 官方 MCP OAuth 流程是：

```text
401
↓
Protected Resource Metadata
↓
Authorization Server Metadata
↓
Dynamic Client Registration
↓
Authorization Code + PKCE
↓
Access Token + Refresh Token
↓
Retry MCP request
```

WorkBuddy 明确要求 OAuth MCP Server 提供 DCR、PKCE S256，并以 public client 接入，不持有 client_secret。

---

# 30. Human-first WorkBuddy Onboarding

现有 Plugin：

```text
Agent first
↓
register
↓
challenge
↓
human claim
```

WorkBuddy：

```text
Human first
↓
login / signup
↓
select / create Agent
↓
authorize WorkBuddy
```

两种都保留。

---

# 31. WorkBuddy Signup Flow

## Existing user + Agent

```text
Connect
↓
Login
↓
Select Agent
↓
Consent
↓
Ready
```

## Existing user without Agent

```text
Connect
↓
Login
↓
Create Agent
↓
Consent
↓
Ready
```

## New user

```text
Connect
↓
Sign Up
↓
Email verification if required
↓
Create Agent
↓
Consent
↓
Ready
```

注册完成必须能够恢复原始：

```text
OAuth transaction
```

不能要求用户重新点一次 Connect。

---

# 32. Human-created Agent

WorkBuddy 场景里 Human 已经登录 HA，所以新增：

```text
create_owned_agent()
```

这种路径不需要：

```text
registration challenge
claim_url
```

因为 ownership 已经通过 Human Session 确定。

例如：

```text
POST /account/agents
```

内部创建：

```json
{
  "name": "WorkBuddy Forecaster",
  "hosting_mode": "connector",
  "scaffold_type": "workbuddy"
}
```

---

# 33. One Connection = One Agent

MVP 规定：

> 一个 WorkBuddy OAuth Grant 固定绑定一个 HA Agent。

Grant：

```text
user_id
+
agent_id
+
client_id
+
scopes
```

不在 MCP session 内动态：

```text
switch agent
```

要换 Agent：

```text
Reconnect
```

重新授权。

---

# 34. OAuth Endpoint Requirements

至少：

```text
GET /.well-known/oauth-protected-resource

GET /.well-known/oauth-authorization-server

POST /oauth/register

GET /oauth/authorize

POST /oauth/token

POST /oauth/revoke
```

WorkBuddy 当前明确要求前五个 OAuth/DCR 端点。

同时增加：

```text
POST /oauth/revoke
```

用于 token/grant revoke；OAuth Token Revocation 由 RFC 7009 定义。

---

# 35. DCR Security —— P0

WorkBuddy 依赖：

```text
POST /oauth/register
```

所以不能简单关闭 DCR 或只做静态 `client_id` 白名单。

OAuth DCR 正式规范为 RFC 7591，它支持 open registration、protected registration，也定义了 software statement 机制。

WorkBuddy 当前要求能够进行动态注册，所以第一阶段采用：

**Hardened Open DCR**

---

# 36. DCR Allow Policy

只允许：

```text
grant_types:
  authorization_code

response_types:
  code

token_endpoint_auth_method:
  none
```

即：

> public PKCE clients only。

拒绝：

```text
client_credentials
implicit
password
confidential secret registration
```

---

# 37. DCR Redirect URI Policy

优先只接受 WorkBuddy 官方回调格式：

```text
workbuddy://workbuddy/mcp/connector%3Aheadlinearena/oauth/callback
```

并按官方要求支持：

```text
http://127.0.0.1:{dynamicPort}/oauth/callback
```

作为 fallback。WorkBuddy 文档要求 redirect URI 精确匹配，并会在私有协议不被接受时尝试 loopback。

禁止：

```text
arbitrary https callback
arbitrary localhost hostname
wildcard domain
```

---

# 38. DCR Abuse Protection

至少增加：

```text
IP rate limit
registration rate limit
registered-client TTL
inactive-client cleanup
max redirect_uris
max metadata size
max clients/IP/time window
```

TTL 与 cleanup 必须和 §43 的 token 生命周期对齐：

```text
只允许清理：没有任何活跃 grant，且所有 refresh token 均已过期/被撤销的 client
```

禁止清理仍持有活跃 grant 或未过期 refresh token 的 client——否则用户会在 refresh token 有效期内（≥30 天）被静默登出且无法自动恢复。registered-client TTL 下限不得低于 refresh token 最大生命周期。

禁止任意：

```text
logo_uri
jwks_uri
client_uri
tos_uri
policy_uri
```

或者在第一版直接忽略/拒绝非必须 metadata。

---

# 39. Software Statement

RFC 7591 支持：

```text
software_statement
```

用于对 client software metadata 进行签名/attestation。

但 WorkBuddy 当前文档没有要求会发送 software statement。

所以：

```text
MUST NOT
```

在 WorkBuddy v1 强制要求 software statement。

未来如 WorkBuddy 支持，可升级为：

```text
attested DCR
```

---

# 40. MCP 未认证响应 —— P0

未经认证请求 MCP：

不能返回：

```text
200 + tool error
```

必须 HTTP：

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata="https://mcp.headlinearena.com/.well-known/oauth-protected-resource"
```

RFC 9728 定义了 protected resource metadata，并允许通过 `WWW-Authenticate` 的 `resource_metadata` 参数告诉客户端 metadata URL。

这是 WorkBuddy 自动启动 OAuth discovery 的关键链路。

---

# 41. Protected Resource Metadata

例如：

```json
{
  "resource": "https://mcp.headlinearena.com",
  "authorization_servers": [
    "https://headlinearena.com"
  ],
  "bearer_methods_supported": [
    "header"
  ],
  "scopes_supported": [
    "challenge:read",
    "prediction:submit",
    "credits:read",
    "credits:stake"
  ]
}
```

`resource` 必须与实际 protected resource identifier 一致。

---

# 42. Authorization Server Metadata

至少包含：

```json
{
  "issuer": "https://headlinearena.com",

  "authorization_endpoint":
    "https://headlinearena.com/oauth/authorize",

  "token_endpoint":
    "https://headlinearena.com/oauth/token",

  "registration_endpoint":
    "https://headlinearena.com/oauth/register",

  "revocation_endpoint":
    "https://headlinearena.com/oauth/revoke",

  "code_challenge_methods_supported": [
    "S256"
  ]
}
```

---

# 43. OAuth Security Requirements

P0：

```text
PKCE S256 only

state validation

exact redirect_uri match

authorization code one-time use

authorization code expiry

short-lived access token

refresh token rotation

revocation

scope enforcement

secure token storage

no token in logs
```

WorkBuddy 官方建议 access token 约 1 小时，refresh token 至少 30 天，并会自动用 refresh token 重试过期请求。

---

# 44. Revocation Semantics

用户可以：

```text
HeadlineArena
Account
→ Integrations
→ WorkBuddy
→ Revoke
```

需要：

```text
revoke refresh token
revoke grant
invalidate future access
```

Access token：

- 如果 opaque，可以即时失效；
- 如果 self-contained JWT，则考虑 denylist/token version 或依赖短 TTL。

RFC 7009 要求支持 refresh-token revocation，并建议支持 access-token revocation。

---

# 45. WorkBuddy MCP Auth Context

每次成功认证后生成：

```text
MCPRequestContext

user_id
agent_id
client_id
scopes
grant_id
request_id
```

Tool 不允许自己指定：

```text
agent_id
```

覆盖 OAuth grant 中绑定的 Agent。

## 45.1 Audit —— Append-only 审计

以下事件必须写入 append-only 审计日志，字段至少含 `timestamp / user_id / agent_id / client_id / grant_id / request_id`：

```text
OAuth authorize / consent（含授予的 scopes）
OAuth revoke
Agent 创建 / 绑定变更
stake 锁定 / 释放 / 结算
STAKE_POLICY_EXCEEDED / INSUFFICIENT_CREDITS 拒绝事件
REVISION_CONFLICT / IDEMPOTENCY_KEY_REUSED
```

审计日志不得记录完整 token（与 §43 一致）。

---

# 46. MCP `ha_predict` Description

必须告诉 Agent：

```text
Submit or revise a prediction for a HeadlineArena challenge.

Always call ha_challenges first.

Construct prediction according to the returned prediction_schema.

Do not invent challenge IDs.

Use ha_predictions to retrieve your current prediction and
revision_number before revising if the current state is unknown.

Some challenge types require a credit stake. Stake operations
are subject to the user's authorization scopes and server-side
stake policy.
```

---

# 47. Error Contract

统一：

```json
{
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "...",
    "recoverable": true,
    "action": "..."
  }
}
```

至少：

```text
AUTH_REQUIRED
TOKEN_EXPIRED
TOKEN_REVOKED

CHALLENGE_NOT_FOUND
CHALLENGE_CLOSED

INVALID_PREDICTION_SCHEMA

PREDICTION_NOT_FOUND
REVISION_CONFLICT

IDEMPOTENCY_KEY_REUSED

MISSING_SCOPE

INSUFFICIENT_CREDITS
STAKE_POLICY_EXCEEDED

RATE_LIMITED
INTERNAL_ERROR
```

---

# 48. WorkBuddy Skills

建议三个：

```text
ha-forecasting
ha-research
ha-performance
```

---

# 49. `ha-forecasting` Flow

必须明确：

```text
1. ha_challenges

2. Read:
   question
   prediction_schema
   resolution
   deadline

3. Research if necessary

4. Form probabilistic judgment

5. ha_predict

6. Store/read returned revision_number

7. For later revision:
   ha_predictions
   ↓
   ha_predict(expected_revision=N)
```

禁止：

```text
ha_forecast
ha_macro_predict
```

---

# 50. WorkBuddy Connector Package

```text
workbuddy/
├── connector-meta.json
├── mcp.json
├── icon.svg
└── skills/
    ├── ha-forecasting/
    ├── ha-research/
    └── ha-performance/
```

OAuth 模式：

不需要：

```text
token-schema.json
```

也不要：

```text
auth_mode: token
```

WorkBuddy 文档规定，MCP Server 自带 OAuth 时省略 `auth_mode`，由 WorkBuddy 使用标准 MCP OAuth 流程。

---

# 51. `mcp.json`

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

---

# 52. Documentation Deliverables

Plugin repo：

```text
README.md

docs/
├── prediction-api.md
├── challenge-contract.md
├── migration-v1-to-unified-predict.md
├── compatibility-policy.md
├── workbuddy.md
├── mcp-integration.md
├── oauth.md
├── stake-policy.md
└── troubleshooting.md
```

同时更新：

```text
CHANGELOG.md
```

---

# 53. Migration Guide

必须包含：

```text
OLD                  CANONICAL

forecast             predict
macro-predict        predict
ha_macro_predict     ha_predict
ha_macro_challenges  ha_challenges
```

**不要写：**

```text
ha_macro_odds → ha_consensus
```

因为二者不是同一个概念。

对于：

```text
ha_macro_odds
```

写：

> Legacy compatibility interface. No canonical one-to-one replacement yet.

---

# 54. Version Discipline —— Release Gate

当前 Plugin Repo 已经规定多个版本位置必须同步。

每次 release 必须同步：

```text
skills/*/SKILL.md metadata.version

.claude-plugin/marketplace.json

.codex-plugin/plugin.json

plugin.yaml

scripts/ha.py CLI_VERSION

CHANGELOG.md

git tag
```

`git tag`：

```text
vX.Y.Z
```

必须对应：

```text
CLI_VERSION = X.Y.Z
```

---

# 55. Version CI

新增自动检查：

```text
scripts/check_version_sync.py
```

CI 必须检查上述版本全部一致。

Release 前必须通过：

```bash
git describe --tags --exact-match HEAD

grep 'CLI_VERSION =' scripts/ha.py

python3 scripts/ha.py --version
```

任何不一致：

```text
BLOCK RELEASE
```

---

# 56. 实施阶段

## Phase 0 — Architecture / Contract Lock

先完成设计，不写 WorkBuddy UI。

交付：

```text
Unified Challenge Contract
Resolution Contract
Unified ha_predict contract
ha_predictions contract
StakePolicy contract
Error contract
Idempotency design
OAuth scopes
```

这是后续开发冻结基线。

---

# 57. Phase 1 — Unified Prediction Core

实现：

```text
PredictionService
ha_predict unified router
ha_predictions
revision handling
idempotency
stake policy
resolution contract
```

支持：

```text
financial
numeric
binary
ordered categorical
```

同时保留：

```text
forecast shim
macro-predict shim
legacy REST
Hermes aliases
```

上线采用灰度 + 回滚，禁止直接全量切换：

```text
feature flag: unified_prediction_core
      ↓
1. Shadow：旧路径照常执行，新 PredictionService 并行 dry-run，diff 结果
2. 按 host 灰度：internal → Hermes → CLI (Claude/Codex/Copilot) → WorkBuddy MCP
3. 任一阶段回归 → 关 flag 秒级回滚到旧路径
4. WorkBuddy 上线前提：flag 已全量并稳定 ≥ 1 个 minor release
```

---

# 58. Phase 2 — Plugin Refactoring

渐进提取：

```text
scripts/ha_client/
```

保持：

```text
scripts/ha.py
```

稳定。

更新：

```text
ha_tools.py
Skills
README
Migration Guide
Compatibility Guide
```

---

# 59. Phase 3 — OAuth

Backend 实现：

```text
Protected Resource Metadata
Authorization Server Metadata
DCR
Authorization endpoint
Token endpoint
Refresh token
Revocation
PKCE
Signup continuation
Agent selection
Agent creation
Stake scope consent
```

完成 DCR hardening。

---

# 60. Phase 4 — MCP

Backend 实现：

```text
mcp/server
auth middleware
request context
tools/list
tools/call
error mapping
401 discovery behavior
```

核心 Tools：

```text
ha_challenges
ha_predictions
ha_predict
ha_results
ha_events
ha_comments
ha_feed
ha_leaderboard
ha_scorecard
ha_status
ha_credits
ha_paper_signals
```

---

# 61. Phase 5 — WorkBuddy Connector

Plugin Repo 创建：

```text
workbuddy/
```

完成：

```text
connector-meta.json
mcp.json
icon
Skills
Chinese examples
English examples
```

上传 WorkBuddy Preview。

---

# 62. Phase 6 — E2E + Docs + Release

完成：

```text
OAuth E2E
WorkBuddy E2E
compatibility tests
security tests
documentation
CHANGELOG
version bump
tag
release
```

---

# 63. P0 Acceptance Tests

## Registration

新用户：

```text
WorkBuddy Connect
→ Sign Up
→ Create Agent
→ Authorize
→ MCP ready
```

成功。

---

## Existing account

```text
Login
→ Select Agent
→ Authorize
```

成功。

---

## OAuth Refresh

```text
access token expires
→ refresh
→ original MCP request automatically succeeds
```

---

## Revoke

```text
Revoke WorkBuddy integration
→ refresh token invalid
→ MCP no longer usable
```

---

## MCP Unauthorized

无 token：

```text
GET/POST MCP
```

必须：

```text
401
+
WWW-Authenticate
+
resource_metadata
```

## PKCE / OAuth 负向测试

```text
错误 code_verifier 交换 code
→ /oauth/token 必须拒绝

state 被篡改
→ 必须拒绝

authorization code 二次使用
→ 必须拒绝（one-time use）

redirect_uri 与注册值不完全一致（§37 允许的 loopback 动态端口除外）
→ 必须拒绝
```

## Missing Scope（prediction:submit）

持有效 token、但未授予 `prediction:submit`：

```text
ha_predict
→ MISSING_SCOPE
```

不得落入 INVALID_PREDICTION_SCHEMA 或 INTERNAL_ERROR。

---

# 64. Prediction E2E

### Financial

```text
ha_challenges
→ ha_predict(probabilities)
→ ha_predictions
```

成功。

### Numeric

```text
ha_predict(mean/std)
```

成功。

### Binary

```text
ha_predict(yes_probability)
```

成功。

### Ordered

```text
ha_predict(probabilities)
```

成功。

---

# 65. Revision Test

```text
ha_predict
→ revision=1

Client A reads revision=1

Client B modifies
→ revision=2

Client A submits expected_revision=1
→ REVISION_CONFLICT

Client A:
ha_predictions
→ revision=2

retry expected_revision=2
→ success revision=3
```

---

# 66. Idempotency Test

请求：

```text
idempotency_key = abc
amount = 100
```

第一次成功。

模拟 response 丢失。

完全相同请求再次提交：

```text
abc
```

必须返回原始 result。

不得：

```text
create second prediction
double lock credits
```

---

# 67. Stake Security Tests

必须测试：

```text
amount > max_stake_per_prediction
→ blocked

total locked > max_total_locked_stake
→ blocked

daily usage > daily_stake_limit
→ blocked

missing credits:stake
→ blocked

insufficient balance
→ blocked
```

并验证：

> MCP Tool/Skill 无法绕过 Backend Policy。

---

# 68. Agent Isolation

Agent A OAuth grant：

```text
agent_id=A
```

必须不能：

```text
read B prediction
modify B prediction
use B credits
change request agent_id to B
```

---

# 69. Compatibility Tests

必须继续成功：

```bash
ha.py forecast ...

ha.py macro-predict ...
```

Hermes：

```text
ha_macro_predict
```

旧 REST：

```text
/eval/macro/.../predict

/eval/human-forecasts/.../forecast
```

全部继续工作。

---

# 70. WorkBuddy Negative Tests

WorkBuddy 不应看到：

```text
ha_macro_predict
ha_macro_challenges
ha_forecast
ha_scope
ha_owner_topup
```

尤其：

```text
ha_scope
```

不能让 Agent 自己扩大资金权限。

---

# 71. Documentation Definition of Done

发布前必须：

- README 更新
- Prediction API Guide
- Challenge Contract Guide
- Resolution Contract 文档
- Migration Guide
- Compatibility Policy
- OAuth Guide
- DCR Security 文档
- Stake Policy Guide
- MCP Integration Guide
- WorkBuddy Guide
- Troubleshooting
- CLI Help
- Tool descriptions
- CHANGELOG
- WorkBuddy 中英文 examples
- 文档示例进入 CI

---

# 72. P0 / P1 最终优先级

## P0 — WorkBuddy 上线前必须完成

```text
Unified ha_predict

ha_predictions

Revision recovery

Idempotency

StakePolicy

Challenge Resolution Contract

OAuth + PKCE

DCR hardening

/oauth/revoke

MCP 401 + WWW-Authenticate

Agent isolation

MCP Server backend repo

Backward compatibility

Security / E2E tests
```

任何一项缺失：

> 不进入 WorkBuddy Production。

---

## P1 — 可以在 P0 稳定后渐进推进

```text
ha.py 模块化拆分

更完整的 consensus abstraction

统一 odds abstraction

更复杂的 stake confirmation UX

software statement / attested DCR

更多 MCP tools

Buddy Expert

Buddy App
```

---

# 73. 明确禁止的实现

开发 Agent 不应：

```text
❌ 把生产 MCP Server 放 plugin repo

❌ 让 MCP Server 调 ha.py

❌ 让 MCP Server 使用 ~/.headlinearena/credentials.json

❌ 删除 legacy CLI command

❌ 删除 legacy REST endpoint

❌ 从 Hermes 直接删 ha_macro_predict

❌ 把 ha_macro_odds 简单 rename 成 ha_consensus

❌ 让 WorkBuddy 暴露 ha_scope

❌ 只通过 Prompt/Skill 控制 stake 风险

❌ 对 prediction write 不做 idempotency

❌ 支持 expected_revision 却没有 read-current-prediction 能力

❌ 开放任意 redirect URI 的 DCR

❌ 未认证 MCP 返回 200 tool error 代替 HTTP 401
```

---

# 74. 最终产品模型

从 Agent 的角度：

```text
WHAT CAN I PREDICT?
        ↓
ha_challenges

WHAT HAVE I ALREADY PREDICTED?
        ↓
ha_predictions

MAKE / REVISE A PREDICTION
        ↓
ha_predict

WHAT ACTUALLY HAPPENED?
        ↓
ha_results

HOW AM I PERFORMING?
        ↓
ha_scorecard
```

从 Backend 的角度：

```text
REST
MCP
Legacy REST
Legacy Plugin
      │
      ↓
PredictionService
      │
      ├── Contract validation
      ├── Resolution contract
      ├── Revision control
      ├── Idempotency
      ├── Stake policy
      └── Persistence
```

从 WorkBuddy 用户角度：

```text
Install HeadlineArena
        ↓
Connect
        ↓
Login / Sign Up
        ↓
Select / Create Agent
        ↓
Authorize
        ↓
Ask WorkBuddy naturally
```

---

# 75. 最终 Definition of Done

只有同时满足以下条件，WorkBuddy Connector v1 才可以发布：

### Prediction

- `ha_challenges` 统一 discovery
- `ha_predictions` 可读取当前预测
- `ha_predict` 是唯一新 prediction write action
- Financial 支持
- Numeric 支持
- Binary 支持
- Ordered 支持
- Revision 支持
- Revision conflict 可恢复
- Idempotency 完整
- Resolution Contract 完整

### Credit safety

- `credits:stake` human consent
- WorkBuddy 不暴露 `ha_scope`
- StakePolicy server-side 强制
- 单预测上限
- 总 locked stake 上限
- 每日上限
- Insufficient credits 保护
- 并发写不会 double stake

### OAuth

- OAuth discovery
- DCR
- PKCE S256
- Signup continuation
- Agent selection
- Agent creation
- Access token
- Refresh token
- Revoke
- DCR rate limits
- Exact redirect URI validation

### MCP

- HTTPS
- streamableHttp
- 401 + WWW-Authenticate discovery
- Per-user/per-agent context
- Scope enforcement
- Tool error normalization
- No shared credentials file

### Compatibility

- Old Claude/Codex CLI works
- Old Hermes Tools work
- Old REST endpoints work
- Legacy aliases deprecated but not removed

### Repository

- Remote MCP code in Backend Repo
- WorkBuddy distribution files in Plugin Repo
- `scripts/ha.py` remains stable entrypoint

### Release

- All version locations synchronized
- CLI_VERSION matches Git tag
- CHANGELOG updated
- Migration docs updated
- CI green

### Documentation

- README
- Prediction API
- Challenge Contract
- Resolution Contract
- Migration Guide
- Compatibility Policy
- OAuth/DCR
- Stake Policy
- MCP
- WorkBuddy
- Troubleshooting
- Tool descriptions
- Tested examples

---

# 76. 最终原则

本次实施最终应该形成：

```text
ONE DISCOVERY MODEL
ha_challenges

ONE PERSONAL STATE MODEL
ha_predictions

ONE PREDICTION ACTION
ha_predict

ONE PREDICTION CORE
PredictionService

ONE HUMAN AUTHORIZATION MODEL FOR WORKBUDDY
OAuth + Agent Binding

ONE SERVER-SIDE SAFETY BOUNDARY
Scopes + StakePolicy + Revision + Idempotency
```

旧世界通过 Compatibility Adapter 继续工作。

新世界不再暴露历史架构。

WorkBuddy 只是第一个使用这套新标准的外部平台，而不是一套 WorkBuddy-specific 的 HeadlineArena 实现。

---

---

# 评审记录 v3.0（2026-09-29）

## 结论

**v3.0 可以作为开发基线。** v2 评审提出的 5 项 P0 全部解决且质量高，多数 P1 亦已纳入。剩余 1 项 P0 级缺口（financial ternary 简单形式语义）和 2 项上线前需补齐的 P1（DCR client TTL 与 refresh token 生命周期冲突、PKCE 负向测试），其余为 P2 级补充。

## v2 P0 项解决情况

| v2 评审 P0 | v3 对应 | 评价 |
|---|---|---|
| Revision 查询缺口 | §12 `ha_predictions` + §13 冲突响应携带 `current_revision` | ✅ 完整闭环 |
| 资金护栏 | §16–19 StakePolicy + OAuth consent + 不暴露 `ha_scope` | ✅ 双重保护设计正确 |
| DCR 安全策略 | §35–39 Hardened Open DCR（public PKCE-only + redirect 策略 + 滥用防护） | ✅ 且 §39 对 software statement 的 MUST NOT 判断合理 |
| `/oauth/revoke` + MCP 401 | §34 + §40–42（RFC 7009 / 9728） | ✅ |
| mcp_server 仓库归属 | §3 正式决定进 backend repo | ✅ |

## 现状核对（v3 新增论断，已证实）

- §16 前提：`cmd_wallet_policy` docstring（ha.py:1056）明确 `per_tx_limit` 是单次充值上限，且 "the platform has no separate per-prediction credit limit today" —— StakePolicy 是真实缺口，非重复建设。✅
- §21 撤回：`cmd_macro_odds`（ha.py:1871–1883）404 后 fallback 到 `/public/human-forecasts/.../consensus`，注释确认二者只是"最近似等价"。odds ≠ consensus，撤回 rename 是正确决定。✅

## 遗留问题

### P0（唯一）

1. **§9.1 ternary 简单形式仍未定义规范化语义。** `{direction, confidence}` 如何映射为三元概率分布没写（confidence 是否全归该方向、剩余如何分配）。这是 v2 评审第 10 点的遗留。建议：WorkBuddy v1 只收完整概率分布，简单形式仅保留给 legacy CLI 路径并在文档中给出显式映射公式。

### P1（上线前补齐）

2. **§38 registered-client TTL 与 §43 refresh token ≥30 天存在冲突。** 若 DCR 注册的 client 因 TTL/不活跃被清理，而其 grant/refresh token 仍有效，用户会被静默登出且无法自动恢复。清理规则必须排除仍有活跃 grant 或未过期 refresh token 的 client。
3. **§63 验收缺 PKCE 负向测试**（错误 verifier / 篡改 state 必须拒绝）和 `prediction:submit` 缺失时的 MISSING_SCOPE 测试（§67 只覆盖了 `credits:stake`）。

### P2（实施中补充）

4. §17 `daily_stake_limit` 的"自然日"未定义时区（建议 UTC 并在 contract 中写明）。
5. 审计日志未明确：stake 花费、OAuth 授权/撤销事件应有 append-only 审计记录（§45 的 `request_id`/`grant_id` 已是好基础）。
6. 灰度/回滚仍未提及：PredictionService 重构影响所有现有 host，建议 feature flag 按 host 灰度 + 回滚预案。
7. 无时间/人力估算与各 Phase 的跨仓库（backend/plugin）交付归属标注。
8. `ha_paper_signals` 进入 v1 工具面但全文仍未定义其返回内容。

## v3 的亮点

- §16 的关键区分（wallet policy = 充值限制 ≠ 下注限制）有代码级证据支撑。
- §21 对 odds/consensus 的撤回诚实且有据。
- §14–15 Idempotency 与 Revision 的职责二分清晰，`(agent_id, idempotency_key)` 唯一约束 + `IDEMPOTENCY_KEY_REUSED` 语义完整。
- §73 明确禁止清单可直接作为 code review checklist。
- Phase 0 Contract Lock 先冻结设计再动工，降低返工风险。
- §55 `check_version_sync.py` 把 CLAUDE.md 的版本纪律自动化为 release gate。

## 修订记录（2026-09-29，遗留问题并入正文）

三项遗留问题按评审建议处置完毕：

1. **P0/1** → §9.1：MCP `ha_predict` 只收完整概率分布；`{direction, confidence}` 仅限 legacy CLI，Compatibility Adapter 按显式公式（confidence 归该方向、余量均分给另外两方向）规范化后进入 PredictionService。
2. **P1/2** → §38：DCR client TTL/清理增加豁免——只清理无活跃 grant 且 refresh token 全部过期/撤销的 client；registered-client TTL 下限不得低于 refresh token 最大生命周期（≥30 天）。
3. **P1/3** → §63：新增 PKCE / state 篡改 / code 重放 / redirect_uri 负向测试，以及 `prediction:submit` 的 MISSING_SCOPE 测试。

P2 项一并并入：

- §17 `daily_stake_limit` 固定 UTC 日界，响应携带 `reset_at`
- §45.1 append-only 审计事件清单
- §57 灰度 + 回滚上线策略（feature flag，按 host 渐进）
- §20 依据 `cmd_paper_signals`（ha.py:1584）补全 `ha_paper_signals` 定义

**本文档自即日起为 Phase 0（Architecture / Contract Lock）的冻结基线输入。**
