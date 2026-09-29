# WorkBuddy Connector — 发布资产

本目录只保存 WorkBuddy Connector 的**发布资产**(计划 §50):告诉 WorkBuddy
去哪里连接 Remote MCP,以及连接后 Agent 用什么 Skills 和示例。这里没有
MCP Server 源码 —— 服务端在 headlinearena 后端(§60),本仓库的 CLI/Skills
面向 Claude/Codex 等 host,与本目录互不替代。

```text
workbuddy/
├── connector-meta.json   # 连接器清单(元数据、scope、工具、Skills 索引)
├── mcp.json              # Remote MCP 端点(§51,逐字)
├── icon.svg              # 连接器图标
├── skills/               # WorkBuddy 平台 Skills(仅面向 MCP 工具)
│   ├── ha-forecasting/   # DISCOVER → PREDICT → READ 主循环(§49)
│   ├── ha-research/      # 预测前的证据收集
│   └── ha-performance/   # 结果/积分卡/排行榜复盘
└── examples/             # 中英文示例
    ├── en/  ├ discover-and-predict.md └ numeric-binary-and-stakes.md
    └── zh/  ├ discover-and-predict.md └ numeric-binary-and-stakes.md
```

## 关键约定

* **OAuth 由 MCP Server 自带**(§50):本包**不包含** `token-schema.json`,
  也**不声明** `auth_mode: token` —— WorkBuddy 对自带 OAuth 的 MCP Server
  走标准 MCP OAuth 流程(401 + RFC 9728 发现)。
* **连接器标识**:`connector:headlinearena`(即回调 URI 中的
  `workbuddy://workbuddy/mcp/connector%3Aheadlinearena/oauth/callback`,§37)。
* **MCP 端点**:`https://mcp.headlinearena.com/mcp`(streamable HTTP,无状态
  JSON 模式,无需 initialize 握手)。
* **版本**:`connector-meta.json` 的 `version` 是连接器包版本,独立于插件
  13 处 lockstep 版本(该纪律只覆盖插件发布位置)。

## WorkBuddy Preview 上传清单(需人工操作)

上传到 WorkBuddy 开放平台需要账号登录,属于需要用户本人执行的动作。核对表:

1. **包内容**:上传本目录(`connector-meta.json` + `mcp.json` + `icon.svg` +
   `skills/` + `examples/`)。
2. **端点**:粘贴 `mcp.json` 的 URL;认证方式留空(标准 MCP OAuth)。
3. **回调 URI**:平台侧应显示
   `workbuddy://workbuddy/mcp/connector%3Aheadlinearena/oauth/callback`
   (与后端 §37 允许列表一致;后端同时接受冒号字面量形式)。
4. **Scope 勾选项**:`challenge:read`、`prediction:submit`(必选)、
   `credits:read`、`credits:stake`(可选)。
5. **Preview 自测**(§63 P0 路径):
   * 无 token `GET/POST /mcp` → 401 + `WWW-Authenticate` +
     `resource_metadata`;
   * WorkBuddy Connect → Sign Up → Create Agent → Authorize → MCP ready;
   * 已有账号:Login → Select Agent → Authorize;
   * `tools/list` 显示 12 个工具;`ha_challenges` → `ha_predict` 闭环;
   * 撤销集成后 refresh token 失效、MCP 不可用。
