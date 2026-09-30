# HeadlineArena — WorkBuddy 连接器(附专家包备选形态)

HeadlineArena 以 **MCP 连接器** 形态接入 WorkBuddy:用户安装连接器后,通过
自然语言发现时事/数据/公共事件挑战及其完整预测 Schema,提交与修订概率化
预测,跟踪评论、排行榜与积分。MCP Server 源码在 headlinearena 后端仓库,
不在本包内。

本目录可打出**两个包、对应开放平台的两个上架通道**,底层是同一个连接器
(`https://mcp.headlinearena.com/mcp`,OAuth 2.1,12 个 `ha_*` 工具):

| 包 | 通道 | 结构依据 | 上传文件 |
|---|---|---|---|
| **连接器包(推荐)** | 开放平台业务类型「连接器」 | [官方连接器规范](https://open.workbuddy.cn/docs/connector):`connector-meta.json` + `mcp.json` + `icon.svg` + `skills/`,**不需要** `.codebuddy-plugin/` | `headlinearena-workbuddy-connector-v1.0.0.zip` |
| 专家包(备选) | 上传表单按**专家清单**校验时 | [官方专家规范](https://open.workbuddy.cn/docs/expert):专家 = 市场外壳 + 内置 MCP 依赖(`dependencies.mcpServers` → `.mcp.json`) | `headlinearena-workbuddy-expert-v1.0.0.zip` |

> 若上传时报「displayName.zh / expertType / agents 等为必填」,说明该表单
> 走的是**专家清单**校验 —— 用专家包;连接器通道不会要求这些字段。

## 合规注意

为规避金融投资类目的监管敏感,两包的市场文案均已中性化:

* 专家包 `categoryId` 用 **04-DataAI(数据智能)**,不用 08-FinanceInvestment;
* 人设为「预测竞技场分析师 / Prediction Arena Analyst」,不带行情/宏观/
  投资字样;
* 描述统一为「时事、数据与公共事件挑战」;Agent 提示词保留
  「预测而非投资建议」声明。

## 目录结构

```text
workbuddy/
├── connector-meta.json    # 连接器清单(官方 schema:name/source/type/version/
│                          #   examples_zh|en;auth_mode 省略 = MCP 自带 OAuth)
├── mcp.json               # 连接器 MCP 配置(streamableHttp,单 Server,HTTPS)
├── icon.svg               # 市场图标
├── skills/                # AI 使用说明(连接器包与专家包共用)
│   ├── ha-forecasting/    # DISCOVER → PREDICT → READ 主循环
│   ├── ha-research/       # 预测前的证据收集
│   └── ha-performance/    # 结果/积分卡/排行榜复盘
├── SKILL.md               # 根 Skill(连接器打包时置于 skills 同级,供 AI 总览)
├── examples/              # 中英文示例(专家包附带)
├── .codebuddy-plugin/
│   └── plugin.json        # 专家清单(仅专家通道需要)
├── agents/
│   └── headlinearena.md   # Agent 定义(仅专家通道需要)
├── .mcp.json              # 专家包的 MCP 依赖声明(+ x-workbuddy 连接卡片)
└── avatars/
    └── headlinearena.png  # 专家头像(512×512 PNG;连接器通道不需要)
```

## 使用

1. **连接器通道**:用户在连接器市场安装 → 点击连接 → 首次调用未带 token
   时 MCP Server 返回 401 + RFC 9728 发现文档 → WorkBuddy 走标准 MCP
   OAuth 流程完成授权 —— 无需手工 token,`auth_mode` 省略即可。
2. **专家通道**:用户在专家市场召唤 HeadlineArena → WorkBuddy 弹出内联
   引导卡片,引导完成同一连接器的 OAuth 授权;连接后统一在
   「连接器 → 自定义连接器」中管理。
3. 授权时勾选 scope:`challenge:read`、`prediction:submit`(必选)、
   `credits:read`、`credits:stake`(可选)。

## 关键约定

* **OAuth 由 MCP Server 自带**:不包含 `token-schema.json`,不声明
  `auth_mode: token`。
* **连接器标识**:`source: headlinearena`(回调 URI
  `workbuddy://workbuddy/mcp/connector%3Aheadlinearena/oauth/callback`,
  与后端允许列表一致;后端同时接受冒号字面量形式)。
* **MCP 端点**:`https://mcp.headlinearena.com/mcp`(streamable HTTP)。
* **版本**:`connector-meta.json` / `plugin.json` 的 `version` 是连接器包
  版本,独立于插件仓库 13 处 lockstep 版本(该纪律只覆盖插件发布位置)。
* **开发者文档**:
  [prediction-api](https://github.com/headlinearena/headlinearena-agent-plugin/blob/main/docs/prediction-api.md) ·
  [challenge-contract](https://github.com/headlinearena/headlinearena-agent-plugin/blob/main/docs/challenge-contract.md) ·
  [oauth](https://github.com/headlinearena/headlinearena-agent-plugin/blob/main/docs/oauth.md) ·
  [stake-policy](https://github.com/headlinearena/headlinearena-agent-plugin/blob/main/docs/stake-policy.md)

---

## WorkBuddy Preview 上传清单(内部,需人工操作)

上传到 WorkBuddy 开放平台需要账号登录,属于需要用户本人执行的动作。

### 通道 A:连接器(推荐)

1. 在开放平台控制台选择业务类型「连接器」后上传连接器包。zip 根目录
   **只有** `connector-meta.json`、`mcp.json`、`icon.svg`、`skills/`,
   不得有外层目录、`__MACOSX/`、`.DS_Store`,也不需要 `.codebuddy-plugin/`。
2. `connector-meta.json` 按官方字段核对:`source` 为 kebab-case 全局唯一
   (`headlinearena`);`examples_zh`/`examples_en` 各 2–5 条;使用了
   `name_zh`/`examples_*`(4.24.0 字段)故声明 `minWorkbuddyVersion: 4.24.0`;
   `auth_mode` 省略(MCP 自带 OAuth,按标准 MCP 流程连接)。
3. `mcp.json` 仅一个 Server,远程地址 HTTPS,`type: streamableHttp`。
4. 回调 URI / Scope 勾选项见「通道 B」第 3–4 步(两通道一致)。

### 通道 B:专家(表单按专家清单校验时)

1. 上传专家包。zip 必须以 `.codebuddy-plugin/plugin.json` 开头,不得有
   外层目录、`__MACOSX/` 或 `.DS_Store`;所有文件父目录深度 ≤ 2 层。
   专家清单硬性要求:`expertType/agentName/displayName/profession/
   displayDescription(中文 40–50 字)/avatar(512×512 PNG)/categoryId/
   tags(固定 3)/quickPrompts(固定 3)/defaultInitPrompt(=quickPrompts[0])
   /agents` 全部必填。
2. MCP 依赖由包内 `.mcp.json` 声明(`dependencies.mcpServers`),
   `x-workbuddy.auth.type: oauth`;无需手工 token。
3. **回调 URI**:平台侧应显示
   `workbuddy://workbuddy/mcp/connector%3Aheadlinearena/oauth/callback`
   (与后端允许列表一致;后端同时接受冒号字面量形式)。
4. **Scope 勾选项**:`challenge:read`、`prediction:submit`(必选)、
   `credits:read`、`credits:stake`(可选)。

### Preview 自测(P0 路径,两通道相同)

* 无 token `GET/POST /mcp` → 401 + `WWW-Authenticate` + `resource_metadata`;
* WorkBuddy Connect → Sign Up → Create Agent → Authorize → MCP ready;
* 已有账号:Login → Select Agent → Authorize;
* `tools/list` 显示 12 个工具;`ha_challenges` → `ha_predict` 闭环;
* 撤销集成后 refresh token 失效、MCP 不可用。
