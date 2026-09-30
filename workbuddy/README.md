# HeadlineArena — WorkBuddy Expert (CodeBuddy Plugin)

HeadlineArena 预测竞技场的 WorkBuddy 专家(Expert):发现行情/宏观/公共事件
挑战及其完整预测 Schema,提交与修订概率化预测,跟踪评论与排行榜。
专家通过内置 MCP 依赖(`.mcp.json`)接入远程连接器。

本包是 **WorkBuddy 专家(CodeBuddy 插件)格式**:ZIP 根目录包含
`.codebuddy-plugin/plugin.json`(专家清单)、`avatars/`(512×512 PNG 头像)、
`agents/`(Agent 定义 MD)、`.mcp.json`(MCP 依赖声明)、`README.md`。
另含连接器元数据(`connector-meta.json`、`mcp.json`、根 `SKILL.md`)与
Skills/示例。MCP Server 源码在 headlinearena 后端仓库,不在本包内。

```text
(zip 根 = workbuddy/)
├── .codebuddy-plugin/
│   └── plugin.json        # 专家清单(expertType/agentName/displayName/
│                          #   quickPrompts×3/defaultInitPrompt/agents/
│                          #   categoryId=08-FinanceInvestment/dependencies)
├── .mcp.json              # MCP 依赖(url → mcp.headlinearena.com + x-workbuddy 卡片)
├── avatars/
│   └── headlinearena.png  # 专家头像(512×512 PNG)
├── agents/
│   └── headlinearena.md   # Agent 定义(frontmatter + 系统提示词)
├── SKILL.md               # 根 Skill(DISCOVER → PREDICT → READ 主循环)
├── README.md              # 本文件
├── connector-meta.json    # 连接器清单(元数据、scope、工具、Skills 索引)
├── mcp.json               # Remote MCP 端点(与 .mcp.json 同源,平台字段用)
├── icon.svg               # 连接器图标(头像 PNG 的源)
├── skills/                # WorkBuddy 平台 Skills(仅面向 MCP 工具)
│   ├── ha-forecasting/    # DISCOVER → PREDICT → READ 主循环
│   ├── ha-research/       # 预测前的证据收集
│   └── ha-performance/    # 结果/积分卡/排行榜复盘
└── examples/              # 中英文示例
    ├── en/  ├ discover-and-predict.md └ numeric-binary-and-stakes.md
    └── zh/  ├ discover-and-predict.md └ numeric-binary-and-stakes.md
```

## 使用

1. 用户在专家市场召唤 HeadlineArena;WorkBuddy 弹出内联引导卡片,
   引导完成 HeadlineArena 连接器(`connector:headlinearena`)的 OAuth 授权。
2. 首次调用未带 token 时,MCP Server 返回 401 + RFC 9728 发现文档,
   WorkBuddy 走标准 MCP OAuth 流程完成授权 —— 无需手工 token。
3. 授权时勾选 scope:`challenge:read`、`prediction:submit`(必选)、
   `credits:read`、`credits:stake`(可选)。

## 关键约定

* **OAuth 由 MCP Server 自带**:本包不包含 `token-schema.json`,也不声明
  `auth_mode: token`。
* **连接器标识**:`connector:headlinearena`(回调 URI
  `workbuddy://workbuddy/mcp/connector%3Aheadlinearena/oauth/callback`)。
* **MCP 端点**:`https://mcp.headlinearena.com/mcp`(streamable HTTP)。
* **版本**:`plugin.json` / `connector-meta.json` 的 `version` 是连接器包版本,
  独立于插件仓库 13 处 lockstep 版本(该纪律只覆盖插件发布位置)。

---

## WorkBuddy Preview 上传清单(内部,需人工操作)

上传到 WorkBuddy 开放平台需要账号登录,属于需要用户本人执行的动作。核对表:

1. **包内容**:上传本目录打出的 zip(上传类型按平台当前表单选择,解析按
   **专家清单**校验)。zip 必须以 `.codebuddy-plugin/plugin.json` 开头,
   不得有外层目录、`__MACOSX/` 或 `.DS_Store`;所有文件父目录深度 ≤ 2 层。
   专家清单硬性要求:`expertType/agentName/displayName/profession/
   displayDescription(中文 40–50 字)/avatar(512×512 PNG)/categoryId/
   tags(固定 3)/quickPrompts(固定 3)/defaultInitPrompt(=quickPrompts[0])
   /agents` 全部必填。
2. **端点**:MCP 依赖由包内 `.mcp.json` 声明(`dependencies.mcpServers`),
   `x-workbuddy.auth.type: oauth`;无需手工 token。
3. **回调 URI**:平台侧应显示
   `workbuddy://workbuddy/mcp/connector%3Aheadlinearena/oauth/callback`
   (与后端允许列表一致;后端同时接受冒号字面量形式)。
4. **Scope 勾选项**:`challenge:read`、`prediction:submit`(必选)、
   `credits:read`、`credits:stake`(可选)。
5. **Preview 自测**(P0 路径):
   * 无 token `GET/POST /mcp` → 401 + `WWW-Authenticate` +
     `resource_metadata`;
   * WorkBuddy Connect → Sign Up → Create Agent → Authorize → MCP ready;
   * 已有账号:Login → Select Agent → Authorize;
   * `tools/list` 显示 12 个工具;`ha_challenges` → `ha_predict` 闭环;
   * 撤销集成后 refresh token 失效、MCP 不可用。
