![Image](../images/title.png)
<div align="center">

<!-- # Grok Search MCP -->

English | [简体中文](../README.md)

**Grok-with-Tavily MCP, providing enhanced web access for Claude Code**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/) [![FastMCP](https://img.shields.io/badge/FastMCP-2.0.0+-green.svg)](https://github.com/jlowin/fastmcp)

</div>

---

## 1. Overview

Grok Search MCP is an MCP server built on [FastMCP](https://github.com/jlowin/fastmcp), featuring a **multi-engine architecture**: **Grok** handles AI-driven intelligent search, **Tavily** handles high-fidelity web content extraction and site mapping, **Firecrawl** provides fallback scraping, and **Python native extraction** (trafilatura) serves as a zero-cost backend.

```
Claude --MCP--> Grok Search Server
                  ├─ web_search  ---> Grok API (AI Search)
                  ├─ web_fetch   ---> Python -> Tavily -> Firecrawl -> Grok (auto fallback)
                  ├─ web_map     ---> Tavily Map (Site Mapping)
                  ├─ get_sources ---> Retrieve cached search sources
                  ├─ plan_*      ---> Structured search planning pipeline
                  ├─ decon_*     ---> Information decontamination pipeline
                  └─ get_config_info  -> Configuration diagnostics
```

### Features

- **Multi-stage search planning** — Capture intent, assess complexity, decompose into sub-queries, design search terms, map tools, and plan execution order
- **Decontamination pipeline** — Geiger-counter style information contamination detection (source concentration, incentive asymmetry, definition drift, numerical anomalies, source laundering)
- **Multi-tier fetch fallback** — `web_fetch` tries Python native (trafilatura), then Firecrawl, Tavily, Grok — in that order
- **Provider selection** — `web_fetch` supports `provider` parameter (`auto`/`python`/`tavily`/`firecrawl`/`grok`)
- **OpenAI-compatible interface**, supports any Grok mirror endpoint; auto-detects Responses API vs Chat Completions
- **`reasoning_effort`** uses standard API params (Responses: `reasoning: {"effort": ...}`, Chat: `reasoning_effort`)
- **Search direction control** — `web_search` supports `direction` for source perspective (mainstream/diverse/critical/eyewitness/adversarial/external/comprehensive)
- **Source caching** — `web_search` results cached by `session_id`, retrieved via `get_sources`
- **Automatic time injection** (detects time-related queries, injects local time context)
- **Dual-band extra sources** — `web_search` can supplement with Tavily/Firecrawl results
- **Exa search provider** (optional, complements Grok search)
- One-click disable Claude Code's built-in WebSearch/WebFetch
- Smart retry with Retry-After header parsing + exponential backoff
- Parent process monitoring (prevents zombie processes on Windows)

### Demo

Using `cherry studio` with this MCP configured, here's how `claude-opus-4.6` leverages this project for external knowledge retrieval, reducing hallucination rates.

![](../images/wogrok.png)
As shown above, **for a fair experiment, we enabled Claude's built-in search tools**, yet Opus 4.6 still relied on its internal knowledge without consulting FastAPI's official documentation for the latest examples.

![](../images/wgrok.png)
As shown above, with `grok-search MCP` enabled under the same experimental conditions, Opus 4.6 proactively made multiple search calls to **retrieve official documentation, producing more reliable answers.**


## 2. Installation

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended Python package manager)
- Claude Code

<details>
<summary><b>Install uv</b></summary>

```bash
# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

> Windows users are **strongly recommended** to run this project in WSL.

</details>

### One-Click Install

If you have previously installed this project, remove the old MCP first:
```
claude mcp remove grok-search
```

#### GuDa Users (Recommended)

GuDa users only need to set `GUDA_API_KEY` to access all services — API URLs are automatically derived:

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

#### Custom Configuration

To use your own API endpoints, configure each service separately:

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

You can also configure additional environment variables in the `env` field:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GUDA_API_KEY` | No | - | GuDa API key (auto-derives all service URLs and keys) |
| `GUDA_BASE_URL` | No | `https://code.guda.studio` | GuDa service base URL |
| `GROK_API_URL` | No | `{GUDA_BASE_URL}/grok/v1` | Grok API endpoint (OpenAI-compatible), overrides GuDa |
| `GROK_API_KEY` | No | `{GUDA_API_KEY}` | Grok API key, overrides GuDa |
| `GROK_MODEL` | No | `grok-4.20-fast` | Default model |
| `GROK_FORCE_RESPONSES_API` | No | `false` | Force Responses API (auto-enabled for multi-agent models) |
| `GROK_ALLOW_NON_STREAM` | No | `false` | **Primary path is always stream**; if true, allow one non-stream fallback when stream is empty (alias `GROK_NON_STREAM_FALLBACK`; keep false behind console/CF 504) |
| `TAVILY_API_KEY` | No | `{GUDA_API_KEY}` | Tavily API key (for web_fetch / web_map) |
| `TAVILY_API_URL` | No | `{GUDA_BASE_URL}/tavily` | Tavily API endpoint |
| `TAVILY_ENABLED` | No | `true` | Enable Tavily |
| `FIRECRAWL_API_KEY` | No | `{GUDA_API_KEY}` | Firecrawl API key (fallback) |
| `FIRECRAWL_API_URL` | No | `{GUDA_BASE_URL}/firecrawl` | Firecrawl API endpoint |
| `FIRECRAWL_ENABLED` | No | `true` | Enable Firecrawl |
| `EXA_API_KEY` | No | - | Exa API key |
| `SSL_VERIFY` | No | `true` | Verify SSL certificates |
| `WEB_SEARCH_TOOL_ENABLED` | No | `true` | Register web_search tool |
| `GROK_DEBUG` | No | `false` | Debug mode |
| `GROK_LOG_LEVEL` | No | `INFO` | Log level |
| `GROK_LOG_DIR` | No | `logs` | Log directory |
| `GROK_FETCH_FALLBACK_ENABLED` | No | `true` | Allow Grok as final web_fetch fallback |
| `GROK_RETRY_MAX_ATTEMPTS` | No | `3` | Max retry attempts |
| `GROK_RETRY_MULTIPLIER` | No | `1` | Retry backoff multiplier |
| `GROK_RETRY_MAX_WAIT` | No | `10` | Max retry wait seconds |
| `MCP_TRANSPORT` | No | `stdio` | MCP transport (stdio/http/sse/streamable-http) |
| `DECON_THOUGHT_BUDGET` | No | `2000` | Max clues chars per decon phase |
| `SPECIALIST_HTTP_PROXY` | No | - | HTTP proxy for specialist fetchers |
| `SPECIALIST_HTTPS_PROXY` | No | - | HTTPS proxy for specialist fetchers |

> **Note**: When `GUDA_API_KEY` is set, all `GROK_API_URL`/`GROK_API_KEY`/`TAVILY_*`/`FIRECRAWL_*` variables become optional. Explicitly set variables take higher priority.


### Verify Installation

```bash
claude mcp list
```

After confirming a successful connection, we **highly recommend** typing the following in a Claude conversation:
```
Call grok-search toggle_builtin_tools to disable Claude Code's built-in WebSearch and WebFetch tools
```
This will automatically modify the project-level `.claude/settings.json` `permissions.deny`, disabling Claude Code's built-in WebSearch and WebFetch, forcing Claude Code to use this project for searches!


## 3. MCP Tools

<details>
<summary>This project provides 21 MCP tools (click to expand)</summary>

### `web_search` — AI Web Search

Executes AI-driven web search via Grok API. Returns the answer only; sources are cached server-side by `session_id` and can be fetched with `get_sources`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | - | Search query |
| `platform` | string | No | `""` | Focus platform (e.g. `"Twitter"`, `"GitHub, Reddit"`) |
| `model` | string | No | `null` | Per-request Grok model ID |
| `extra_sources` | int | No | `0` | Extra sources via Tavily/Firecrawl (0 disables) |
| `from_date` | string | No | `""` | Start date filter (YYYY-MM-DD) |
| `to_date` | string | No | `""` | End date filter (YYYY-MM-DD) |
| `allowed_domains` | string | No | `""` | Comma-separated allowed domains |
| `max_search_results` | int | No | `0` | Max search results (0=unlimited, ≤20) |
| `reasoning_effort` | string | No | `""` | Low/medium/high/xhigh. Usually unnecessary for search |
| `direction` | string | No | `""` | Source direction: mainstream/diverse/critical/eyewitness/adversarial/external/comprehensive |
| `timeout` | int | No | `0` | Timeout in seconds (0=default) |

Return value: `session_id`, `content`, `sources_count`

### `get_sources` — Retrieve Sources

Retrieves the full cached source list for a previous `web_search` call. Sources may expire from cache.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | Yes | `session_id` returned by `web_search` |

Return value: `session_id`, `sources_count`, `sources` (each with url/title/description/provider)

### `web_fetch` — Web Content Extraction

Multi-tier content extraction returning Markdown. Fallback chain: `Python (trafilatura) → Firecrawl Scrape → Tavily Extract → Grok`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | - | Target webpage URL |
| `provider` | string | No | `auto` | Provider selection: auto/python/tavily/firecrawl/grok |
| `timeout` | int | No | `0` | Timeout in seconds (0=default) |

Python native extraction requires `pip install trafilatura`; skipped automatically if not installed.

### `web_map` — Site Structure Mapping

Traverses website structure via Tavily Map API, discovering URLs and generating a site map.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | - | Starting URL |
| `instructions` | string | No | `""` | Natural language filtering instructions |
| `max_depth` | int | No | `1` | Max traversal depth (1-5) |
| `max_breadth` | int | No | `20` | Max links per page (1-500) |
| `limit` | int | No | `50` | Total link limit (1-500) |
| `timeout` | int | No | `150` | Timeout seconds (10-150) |

### `get_config_info` — Configuration Diagnostics

No parameters. Displays all configuration status, tests Grok/Tavily/Firecrawl connections, shows transport type, auth mode, and available models (API keys auto-masked).

### `switch_model` — Model Switching

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `model` | string | Yes | Model ID (e.g. `"grok-4-fast"`, `"grok-2-latest"`) |

Settings persist to `~/.config/grok-search/config.json` across sessions.

### `toggle_builtin_tools` — Tool Routing Control

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `action` | string | No | `"status"` | `"on"` disable built-in / `"off"` enable / `"status"` check |

Modifies project-level `.claude/settings.json` `permissions.deny`.

### `exa_search` / `exa_find_similar` — Exa Search

Optional Exa search provider (requires `EXA_API_KEY`). `exa_search` performs Exa web search; `exa_find_similar` finds content similar to a given URL.

### Search Planning Pipeline

Structured six-phase planning scaffold for complex searches. Auto-triggered by `plan_intent`:

1. **plan_intent** — Capture intent as core question
2. **plan_complexity** — Assess complexity (1-3)
3. **plan_sub_query** — Decompose into sub-queries
4. **plan_search_term** — Design search terms per sub-query
5. **plan_tool_mapping** — Map sub-queries to tools
6. **plan_execution** — Define parallel/serial execution order

When `contamination_suspected=true` is set, `plan_intent` response includes a `contamination_flag` to trigger the decontamination pipeline.

### Decontamination Pipeline

Purpose: Detect information contamination (data manipulation, agenda-driven narratives, definition drift, etc.) using a **Geiger counter** approach — detect signal strength, alert proportionally, never diagnose or attribute. All judgments are made by the user.

**Methodology**: "First ask IF it is, then ask WHY" — verify definitional scope and factual premises first, check numerical plausibility second, analyze motives last.

**Auto-triggered**: Set `contamination_suspected=true` in `plan_intent`. At medium/high suspicion, suggests entering the decontamination flow.

**Environment control**:
- `GROK_DECON_ENABLED=true` — set to `false` to disable the entire decontamination pipeline (tools return error, plan_intent description stripped)
- `DECON_THOUGHT_BUDGET=2000` — max clues characters per phase

Pipeline phases (called in order):

- **`decon_assess`** — Assess contamination suspicion level (low/medium/high). Auto-skips remaining phases when low
- **`decon_verify`** — Check definitional scope and numerical plausibility
- **`decon_provenance`** — Trace claim origin and propagation paths
- **`decon_motive`** — Analyze stakeholder incentives
- **`decon_synthesis`** — Cross-incentive synthesis and direction suggestions
- **`decon_patterns`** — Known manipulation pattern reference card (terminal phase, display only)

Each phase uses a `clues` parameter for concise key findings (not full reasoning), auto-truncated by `DECON_THOUGHT_BUDGET`.

</details>

## 4. FAQ

<details>
<summary>
Q: Must I configure both Grok and Tavily?
</summary>
A: Set `GUDA_API_KEY` to get full Grok + Tavily + Firecrawl service. Without GuDa, Grok (`GROK_API_URL` + `GROK_API_KEY`) is required. Tavily, Firecrawl, and Exa are all optional — the fallback chain handles missing providers automatically.
</details>

<details>
<summary>
Q: What format does the Grok API URL need?
</summary>
A: An OpenAI-compatible API endpoint (supporting `/chat/completions` and `/models` endpoints).
</details>

<details>
<summary>
Q: How to verify configuration?
</summary>
A: Say "Show grok-search configuration info" in a Claude conversation to test all configured API connections and display results.
</details>

<details>
<summary>
Q: What is `reasoning_effort` for?
</summary>
A: Controls Grok model reasoning depth. High effort (xhigh/high) consumes more tokens and latency. Usually unnecessary for simple search queries; may help with complex multi-step analysis.
</details>

<details>
<summary>
Q: What is the Decontamination Pipeline for?
</summary>
A: It helps detect information contamination (data manipulation, agenda-driven narratives, definition drift) using a **Geiger counter** approach — detect signal strength, alert proportionally, never diagnose. All judgments are made by the LLM user.

The pipeline is optional. Set `contamination_suspected=true` in `plan_intent` to trigger a flag. At medium/high suspicion, phases run in order: assess → verify → provenance → motive → synthesis → patterns. If the LLM already has the analysis in context (from conversation), it can skip directly to `decon_synthesis` or `decon_patterns` without running prior phases.

**To disable**: simply never set `contamination_suspected=true` in `plan_intent`. No env var or config change needed. Or set it to `false` (default).
</details>

## Acknowledgments

### Design & Reference

- [Episkey-G/GrokSearch-rs](https://github.com/Episkey-G/GrokSearch-rs) — Rust GrokSearch reference implementation that inspired our specialist content extractor design (GitHub Issues/PRs, arXiv, Wikipedia, HN) and naming conventions

### Community Fork Contributions

See the [Chinese README](../README.md#社区-fork-贡献) for the full list of verified community fork contributions.

## License

[MIT License](LICENSE)

---

<div align="center">

**If this project helps you, please give it a Star!**

[![Star History Chart](https://api.star-history.com/svg?repos=GuDaStudio/GrokSearch&type=date&legend=top-left)](https://www.star-history.com/#GuDaStudio/GrokSearch&type=date&legend=top-left)
</div>
