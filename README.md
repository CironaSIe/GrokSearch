![这是图片](./images/title.png)
<div align="center">

<!-- # Grok Search MCP -->

[English](./docs/README_EN.md) | 简体中文

**Grok-with-Tavily MCP，为 Claude Code 提供更完善的网络访问能力**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/) [![FastMCP](https://img.shields.io/badge/FastMCP-2.0.0+-green.svg)](https://github.com/jlowin/fastmcp)

</div>

---

## 一、概述

Grok Search MCP 是一个基于 [FastMCP](https://github.com/jlowin/fastmcp) 构建的 MCP 服务器，采用**多引擎架构**：**Grok** 负责 AI 驱动的智能搜索，**Tavily** 负责高保真网页抓取与站点映射，**Firecrawl** 托底抓取，**Python 原生抓取** (trafilatura) 作为零成本后备，各取所长为 Claude Code / Cherry Studio 等 LLM Client 提供完整的实时网络访问能力。

```
Claude ──MCP──► Grok Search Server
                  ├─ web_search  ───► Grok API（AI 搜索）
                  ├─ web_fetch   ───► Python → Tavily → Firecrawl → Grok（自动降级）
                  ├─ web_map     ───► Tavily Map（站点映射）
                  ├─ get_sources ───► 检索缓存的搜索信源
                  ├─ plan_*      ───► 结构化搜索规划管线
                  ├─ decon_*     ───► 信息去污管线
                  └─ get_config_info  → 配置诊断
```

### 功能特性

- **响应式搜索规划** — 结构化多阶段搜索规划管线（意图捕获 → 复杂度评估 → 子查询分解 → 搜索词策略 → 工具选择 → 执行排序），自动处理污染检测旁路
- **信息去污管线** — 以盖革计数器方式检测信息污染（信源集中、激励不对称、定义漂移、数字异常、信源洗白），自动触发并与搜索规划联动
- **原生抓取 + 多级降级** — `web_fetch` 优先走 Python trafilatura 原生抓取（零成本），失败时自动降级到 Firecrawl → Tavily → Grok
- **提供者选择** — `web_fetch` 支持 `provider` 参数（`auto`/`python`/`tavily`/`firecrawl`/`grok`），LLM 可按需指定
- **reasoning_effort API 参数** — Responses API 路径发送标准的 `reasoning: {"effort": ...}`，Chat Completions 路径发送 `reasoning_effort` 参数 + 推理提示兜底
- **信源缓存** — `web_search` 结果按 `session_id` 缓存，`get_sources` 随时拉取
- **OpenAI 兼容接口** — 支持任意 Grok 镜像站，自动探测 Responses API / Chat Completions API
- **自动时间注入** — 检测时间相关查询，注入本地时间上下文
- **双端带外信源** — `web_search` 支持 Tavily/Firecrawl 作为额外信源补充
- **Exa 搜索提供者** — 可选的 Exa 搜索作为 Grok 搜索的补充
- **搜索方向控制** — `web_search` 支持 `direction` 参数（主流/多元/批判/目击者/对抗性/外部/全面）
- 一键禁用 Claude Code 官方 WebSearch/WebFetch，强制路由到本工具
- 智能重试（支持 Retry-After 头解析 + 指数退避）
- 父进程监控（Windows 下自动检测父进程退出，防止僵尸进程）

### 效果展示
我们以在`cherry studio`中配置本MCP为例，展示了`claude-opus-4.6`模型如何通过本项目实现外部知识搜集，降低幻觉率。
![](./images/wogrok.png)
如上图，**为公平实验，我们打开了claude模型内置的搜索工具**，然而opus 4.6仍然相信自己的内部常识，不查询FastAPI的官方文档，以获取最新示例。
![](./images/wgrok.png)
如上图，当打开`grok-search MCP`时，在相同的实验条件下，opus 4.6主动调用多次搜索，以**获取官方文档，回答更可靠。**


## 二、安装

### 前置条件

- Python 3.10+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)（推荐的 Python 包管理器）
- Claude Code

<details>
<summary><b>安装 uv</b></summary>

```bash
# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

> Windows 用户**强烈推荐**在 WSL 中运行本项目。

</details>

### 一键安装

若之前安装过本项目，使用以下命令卸载旧版 MCP：
```
claude mcp remove grok-search
```

#### GuDa 用户（推荐）

GuDa 用户只需设置 `GUDA_API_KEY` 即可访问所有服务——API URL 和 Key 自动推导：

```bash
claude mcp add-json grok-search --scope user '{
  "type": "stdio",
  "command": "uvx",
  "args": [
    "--from",
    "git+https://github.com/GuDaStudio/GrokSearch@grok-with-tavily",
    "grok-search"
  ],
  "env": {
    "GUDA_API_KEY": "your-guda-api-key"
  }
}'
```

#### 自定义配置

自行指定各 API 端点：

```bash
claude mcp add-json grok-search --scope user '{
  "type": "stdio",
  "command": "uvx",
  "args": [
    "--from",
    "git+https://github.com/GuDaStudio/GrokSearch@grok-with-tavily",
    "grok-search"
  ],
  "env": {
    "GROK_API_URL": "https://your-api-endpoint.com/v1",
    "GROK_API_KEY": "your-grok-api-key",
    "TAVILY_API_KEY": "tvly-your-tavily-key",
    "TAVILY_API_URL": "https://api.tavily.com"
  }
}'
```

<details> <summary>如果遇到 SSL / 证书验证错误</summary>

在部分企业网络或代理环境中，可能会出现类似错误：

certificate verify failed
self signed certificate in certificate chain

可以在 uvx 参数中添加 --native-tls，使其使用系统证书库：

claude mcp add-json grok-search --scope user '{
  "type": "stdio",
  "command": "uvx",
  "args": [
    "--native-tls",
    "--from",
    "git+https://github.com/GuDaStudio/GrokSearch@grok-with-tavily",
    "grok-search"
  ],
  "env": {
    "GROK_API_URL": "https://your-api-endpoint.com/v1",
    "GROK_API_KEY": "your-grok-api-key",
    "TAVILY_API_KEY": "tvly-your-tavily-key",
    "TAVILY_API_URL": "https://api.tavily.com"
  }
}'
</details>

除此之外，你可以在 `env` 字段中配置更多环境变量：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `GUDA_API_KEY` | ❌ | - | GuDa API Key（设置后自动推导所有服务 URL 和 Key） |
| `GUDA_BASE_URL` | ❌ | `https://code.guda.studio` | GuDa 服务基地址 |
| `GROK_API_URL` | ❌ | `{GUDA_BASE_URL}/grok/v1` | Grok API 地址（OpenAI 兼容格式），覆盖 GuDa 推导值 |
| `GROK_API_KEY` | ❌ | `{GUDA_API_KEY}` | Grok API 密钥，覆盖 GuDa 推导值 |
| `GROK_MODEL` | ❌ | `grok-4.20-fast` | 默认模型（设置后优先于 `~/.config/grok-search/config.json`） |
| `GROK_FORCE_RESPONSES_API` | ❌ | `false` | 强制使用 Responses API（multi-agent 模型自动启用） |
| `GROK_ALLOW_NON_STREAM` | ❌ | `false` | **默认始终流式**；仅当为 true 时，流式结果为空才允许一次非流回退（别名 `GROK_NON_STREAM_FALLBACK`；console/CF 504 场景请保持 false） |
| `TAVILY_API_KEY` | ❌ | `{GUDA_API_KEY}` | Tavily API 密钥（用于 web_fetch / web_map） |
| `TAVILY_API_URL` | ❌ | `{GUDA_BASE_URL}/tavily` | Tavily API 地址 |
| `TAVILY_ENABLED` | ❌ | `true` | 是否启用 Tavily |
| `FIRECRAWL_API_KEY` | ❌ | `{GUDA_API_KEY}` | Firecrawl API 密钥（Tavily 失败时托底） |
| `FIRECRAWL_API_URL` | ❌ | `{GUDA_BASE_URL}/firecrawl` | Firecrawl API 地址 |
| `FIRECRAWL_ENABLED` | ❌ | `true` | 是否启用 Firecrawl |
| `EXA_API_KEY` | ❌ | - | Exa API 密钥 |
| `SSL_VERIFY` | ❌ | `true` | 是否验证 SSL 证书 |
| `WEB_SEARCH_TOOL_ENABLED` | ❌ | `true` | 是否注册 web_search 工具 |
| `GROK_DEBUG` | ❌ | `false` | 调试模式 |
| `GROK_LOG_LEVEL` | ❌ | `INFO` | 日志级别 |
| `GROK_LOG_DIR` | ❌ | `logs` | 日志目录 |
| `GROK_FETCH_FALLBACK_ENABLED` | ❌ | `true` | 是否允许 Grok 作为 web_fetch 的最后兜底 |
| `GROK_RETRY_MAX_ATTEMPTS` | ❌ | `3` | 最大重试次数 |
| `GROK_RETRY_MULTIPLIER` | ❌ | `1` | 重试退避乘数 |
| `GROK_RETRY_MAX_WAIT` | ❌ | `10` | 重试最大等待秒数 |
| `MCP_TRANSPORT` | ❌ | `stdio` | MCP 传输协议（stdio/http/sse/streamable-http） |
| `DECON_THOUGHT_BUDGET` | ❌ | `2000` | 去污管线每阶段 clues 最大字符数 |
| `GROK_DECON_ENABLED` | ❌ | `true` | 是否启用去污管线（设为 false 关闭） |
| `GITHUB_TOKEN` | ❌ | - | GitHub API 令牌（未设时自动尝试 `gh auth token`） |
| `GH_TOKEN` | ❌ | - | 同上，gh 兼容变量名 |
| `SPECIALIST_HTTP_PROXY` | ❌ | - | Specialist 抓取的 HTTP 代理（独立于系统代理） |
| `SPECIALIST_HTTPS_PROXY` | ❌ | - | Specialist 抓取的 HTTPS 代理 |

> **说明**：设置 `GUDA_API_KEY` 后，`GROK_API_URL`/`GROK_API_KEY`/`TAVILY_*`/`FIRECRAWL_*` 等变量均变为可选（自动推导），显式设置的变量优先级更高。


### 验证安装

```bash
claude mcp list
```

🍟 显示连接成功后，我们**十分推荐**在 Claude 对话中输入
```
调用 grok-search toggle_builtin_tools，关闭Claude Code's built-in WebSearch and WebFetch tools
```
工具将自动修改**项目级** `.claude/settings.json` 的 `permissions.deny`，一键禁用 Claude Code 官方的 WebSearch 和 WebFetch，从而迫使 claude code 调用本项目实现搜索！



## 三、MCP 工具介绍

<details>
<summary>本项目提供 21 个 MCP 工具（展开查看）</summary>

### `web_search` — AI 网络搜索

通过 Grok API 执行 AI 驱动的网络搜索，默认仅返回回答正文 + `session_id`；信源按 `session_id` 缓存在服务端，可用 `get_sources` 拉取。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | ✅ | - | 搜索查询语句 |
| `platform` | string | ❌ | `""` | 聚焦平台（如 `"Twitter"`, `"GitHub, Reddit"`） |
| `model` | string | ❌ | `null` | 按次指定 Grok 模型 ID |
| `extra_sources` | int | ❌ | `0` | 额外补充信源数量（Tavily/Firecrawl，0 关闭） |
| `from_date` | string | ❌ | `""` | 起始日期过滤（YYYY-MM-DD） |
| `to_date` | string | ❌ | `""` | 结束日期过滤（YYYY-MM-DD） |
| `allowed_domains` | string | ❌ | `""` | 限制搜索域名列表（逗号分隔） |
| `max_search_results` | int | ❌ | `0` | 最大搜索结果数（0=无限，≤20） |
| `reasoning_effort` | string | ❌ | `""` | 推理力度（low/medium/high/xhigh）。通常搜索不需要设高，仅在需要深度多步分析时使用 |
| `direction` | string | ❌ | `""` | 信源方向：mainstream / diverse / critical / eyewitness / adversarial / external / comprehensive |
| `timeout` | int | ❌ | `0` | 超时秒数（0=使用默认） |

自动检测查询中的时间相关关键词（如"最新""今天""recent"等），注入本地时间上下文以提升时效性搜索的准确度。

返回值：`session_id`, `content`, `sources_count`

### `get_sources` — 获取信源

通过 `session_id` 获取对应 `web_search` 的全部信源。信源可能因缓存过期而不可用。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | ✅ | `web_search` 返回的 `session_id` |

返回值：`session_id`, `sources_count`, `sources`（每项含 url/title/description/provider）

### `web_fetch` — 网页内容抓取

多级降级抓取，返回 Markdown 格式。fallback 链：`Python 原生 (trafilatura) → Firecrawl Scrape → Tavily Extract → Grok`。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `url` | string | ✅ | - | 目标网页 URL |
| `provider` | string | ❌ | `auto` | 抓取提供者：auto / python / tavily / firecrawl / grok |
| `timeout` | int | ❌ | `0` | 超时秒数（0=使用默认） |

Python 原生抓取需要安装 trafilatura（`pip install trafilatura`），未安装时自动跳过。

### `web_map` — 站点结构映射

通过 Tavily Map API 遍历网站结构，发现 URL 并生成站点地图。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `url` | string | ✅ | - | 起始 URL |
| `instructions` | string | ❌ | `""` | 自然语言过滤指令 |
| `max_depth` | int | ❌ | `1` | 最大遍历深度（1-5） |
| `max_breadth` | int | ❌ | `20` | 每页最大跟踪链接数（1-500） |
| `limit` | int | ❌ | `50` | 总链接处理数上限（1-500） |
| `timeout` | int | ❌ | `150` | 超时秒数（10-150） |

### `get_config_info` — 配置诊断

无需参数。显示所有配置状态、测试 Grok/Tavily/Firecrawl 连接、返回可用模型列表、传输协议、认证模式（API Key 自动脱敏）。

### `switch_model` — 模型切换

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model` | string | ✅ | 模型 ID（如 `"grok-4-fast"`, `"grok-2-latest"`） |

切换后配置持久化到 `~/.config/grok-search/config.json`，跨会话保持。

### `toggle_builtin_tools` — 工具路由控制

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `action` | string | ❌ | `"status"` | `"on"` 禁用官方工具 / `"off"` 启用 / `"status"` 查看 |

修改项目级 `.claude/settings.json` 的 `permissions.deny`，一键禁用 Claude Code 官方的 WebSearch 和 WebFetch。

### `exa_search` / `exa_find_similar` — Exa 搜索

可选的 Exa 搜索提供者，需配置 `EXA_API_KEY`。`exa_search` 执行 Exa 网络搜索；`exa_find_similar` 查找与给定 URL 相似的内容。

### 搜索规划管线（Search Planning Pipeline）

结构化六阶段规划脚手架，用于在执行复杂搜索前先生成可执行的搜索计划。由 `plan_intent` 自动触发：

1. **plan_intent** — 捕获用户意图为核心问题，识别搜索类型和时效性
2. **plan_complexity** — 评估搜索复杂度（1-3 级）
3. **plan_sub_query** — 分解为多个子查询
4. **plan_search_term** — 为每个子查询设计搜索词
5. **plan_tool_mapping** — 映射子查询到搜索工具
6. **plan_execution** — 定义执行顺序（并行/串行）

当 `contamination_suspected=true` 时，`plan_intent` 响应包含 `contamination_flag`，提示进入去污流程。

### 信息去污管道（Decontamination Pipeline）

设计目的：检测信息污染（数据操纵、议程驱动叙述、定义漂移等），以**盖革计数器**方式工作——探测信号强度、按比例告警，不做诊断和归因。所有判断由用户做出。

**方法论**：「先问是不是，再问为什么」——先验证定义范围和事实前提，再检查数字合理性，最后分析动机。事实在动机之前。

**自动触发**：在 `plan_intent` 中设置 `contamination_suspected=true` 时触发。检测到 medium/high 污染后自动建议进入去污流程。

**环境变量控制**：
- `GROK_DECON_ENABLED=true` — 设为 `false` 停止整个去污管线（工具返回错误、plan_intent 描述移除）
- `DECON_THOUGHT_BUDGET=2000` — 控制每阶段 `clues` 最大字符数

包含以下工具（按顺序调用）：

- **`decon_assess`** — 评估信息污染可疑程度（low/medium/high），基于领域能量集中度、历史污染记录、激励不对称等维度。低可疑度自动结束管线
- **`decon_verify`** — 核验核心概念的**定义范围**和**数字合理性**。先问定义是否漂移，再问数字是否违反物理/数学边界
- **`decon_provenance`** — 追踪关键主张的起源和传播路径，检测引用级联和信源洗白模式
- **`decon_motive`** — 分析利益相关方的激励机制：谁受益、谁能控制数据生产、反叙述是否存在
- **`decon_synthesis`** — 跨激励综合：收集所有立场的信源，定位共识锚点和分歧根源
- **`decon_patterns`** — 已知操纵模式参考卡（终端阶段，仅展示不做诊断）

每个阶段的 `clues` 参数仅需输出关键发现（非完整推理），服务端根据 `DECON_THOUGHT_BUDGET` 自动截断。

</details>

## 四、常见问题

<details>
<summary>
Q: 必须同时配置 Grok 和 Tavily 吗？
</summary>
A: 设置 `GUDA_API_KEY` 即可获得完整的 Grok + Tavily + Firecrawl 服务。不使用 GuDa 时，Grok（`GROK_API_URL` + `GROK_API_KEY`）为必填，提供核心搜索能力。Tavily、Firecrawl、Exa 均为可选——配置后 `web_fetch` 自动走降级链，未配置时跳过对应提供者。
</details>

<details>
<summary>
Q: Grok API 地址需要什么格式？
</summary>
A: 需要 OpenAI 兼容格式的 API 地址（支持 `/chat/completions` 和 `/models` 端点）。如使用官方 Grok，需通过兼容 OpenAI 格式的镜像站访问。
</details>

<details>
<summary>
Q: 如何验证配置？
</summary>
A: 在 Claude 对话中说"显示 grok-search 配置信息"，将自动测试所有已配置 API 的连接并显示结果。
</details>

<details>
<summary>
Q: `reasoning_effort` 在搜索中有什么用？
</summary>
A: 控制 Grok 模型的推理深度。高推理（xhigh/high）会消耗更多 token 和延迟。对简单搜索无关紧要；对需要深度多步推理的复杂查询（比较分析、溯源追踪）可能有帮助。
</details>

<details>
<summary>
Q: 去污染管线是干什么的？怎么关？
</summary>
A: 用于检测信息污染（数据操纵、议程驱动叙事、定义漂移等），采用 **盖革计数器** 方式——检测信号强度、按比例告警、不做诊断。所有判断由 LLM 用户自行做出。

管线是可选的。在 `plan_intent` 中设置 `contamination_suspected=true` 即可触发污染标记。中/高怀疑级别时按顺序执行：assess → verify → provenance → motive → synthesis → patterns。

如果 LLM 已在对话上下文中掌握了污染分析，可以直接跳到 `decon_synthesis` 或 `decon_patterns`，无需跑完前置阶段。

**关闭方法**：不在 `plan_intent` 中设置 `contamination_suspected=true` 即可（默认就是 false）。无需环境变量或配置变更。
</details>

## 致谢

### 设计与参考

- [Episkey-G/GrokSearch-rs](https://github.com/Episkey-G/GrokSearch-rs) — Rust 版 GrokSearch 参考实现，为我们设计 specialist content extractor（GitHub Issues/PRs、arXiv、Wikipedia、HN）和命名方式提供了重要启发

### 社区 Fork 贡献

本项目整合了以下社区 fork 的贡献（已验证 fork 分支上的独立提交）：

- [Techd81](https://github.com/Techd81) — 搜索参数增强（from_date/to_date、reasoning_effort）、错误信息脱敏、URL 正则改进、streaming HTTP 错误处理、multi-agent Responses API 适配
- [QianFuv](https://github.com/QianFuv) — 运行时模块拆分重构、search_prompt 简化优化、Tavily Hikari URL 兼容
- [konbakuyomu](https://github.com/konbakuyomu) — Exa 搜索提供者、SSL_VERIFY 配置支持
- [Flutter233PM](https://github.com/Flutter233PM) — 流式空内容自动降级非流式请求
- [Huan-zhaojun](https://github.com/Huan-zhaojun) — get_sources Markdown 输出格式
- [MoonWeSif](https://github.com/MoonWeSif) — premise_valid JSON Schema 兼容修复
- [ynlea](https://github.com/ynlea) — search_prompt 净化（防 jailbreak 检测误杀）
- [Maomaoxion](https://github.com/Maomaoxion) — 默认模型更新为 grok-4.20-fast
- [wu452148993](https://github.com/wu452148993) — web_search_tool 开关配置
- [jayhchen](https://github.com/jayhchen) — switch_model 工具始终注册
- [shengnan-Luo](https://github.com/shengnan-Luo) — FIRECRAWL_API_URL 配置支持
- [handsomelong922](https://github.com/handsomelong922) — SEARCH_TIMEOUT 超时配置、MCP 传输协议支持、Tavily API Key 轮换

### 已评估但未整合的 Fork 特性

以下为社区 fork 中存在但本分支尚未整合的特性：

- **HTTP/SSE 传输 + Docker 部署** — [handsomelong922](https://github.com/handsomelong922/GrokSearch/commit/969760558c695c80f80f7213782ead79805fb971) 实现 Dockerfile + docker-compose.yml + `MCP_TRANSPORT` 环境变量支持（http/streamable-http/sse/stdio）
- **Tavily API Key 轮换** — [handsomelong922](https://github.com/handsomelong922/GrokSearch/commit/87b8581d43a9863b73a80e67fe3ff69e16baa379) 实现逗号分隔多 key + round-robin 轮换 + 错误自动故障转移

## 许可证

[MIT License](LICENSE)

---

<div align="center">

**如果这个项目对您有帮助，请给个 Star！**

[![Star History Chart](https://api.star-history.com/svg?repos=GuDaStudio/GrokSearch&type=date&legend=top-left)](https://www.star-history.com/#GuDaStudio/GrokSearch&type=date&legend=top-left)
</div>
