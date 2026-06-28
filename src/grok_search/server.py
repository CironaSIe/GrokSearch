import os
import sys
from pathlib import Path

# 支持直接运行：添加 src 目录到 Python 路径
src_dir = Path(__file__).parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fastmcp import FastMCP, Context
from typing import Annotated
from pydantic import Field

# 尝试使用绝对导入（支持 mcp run）
try:
    from grok_search.providers.grok import GrokSearchProvider
    from grok_search.providers.exa import ExaSearchProvider
    from grok_search.logger import log_info
    from grok_search.config import config
    from grok_search.sources import SourcesCache, merge_sources, new_session_id, split_answer_and_sources
    from grok_search.planning import engine as planning_engine, _split_csv, decon_engine
    from grok_search.utils import SEARCH_FRAMINGS, redact_sensitive_text
except ImportError:
    from .providers.grok import GrokSearchProvider
    from .providers.exa import ExaSearchProvider
    from .logger import log_info
    from .config import config
    from .sources import SourcesCache, merge_sources, new_session_id, split_answer_and_sources
    from .planning import engine as planning_engine, _split_csv, decon_engine
    from .utils import SEARCH_FRAMINGS, redact_sensitive_text

import asyncio, random, httpx

mcp = FastMCP("grok-search")


def _register_exa_if_configured(mcp: FastMCP):
    if not config.exa_api_key:
        return
    exa_provider = ExaSearchProvider(config.exa_api_key, config.exa_base_url)

    @mcp.tool(name="exa_search")
    async def exa_search(
        query: Annotated[str, "Search query for Exa API."],
        max_results: Annotated[int, "Number of results to return."] = 5,
    ) -> str:
        results = await exa_provider.search(query, max_results)
        return "\n\n".join(
            f"## {r.title}\nURL: {r.url}\n{r.snippet}"
            for r in results
        )

    @mcp.tool(name="exa_find_similar")
    async def exa_find_similar(
        url: Annotated[str, "URL to find similar pages for."],
        max_results: Annotated[int, "Number of results to return."] = 5,
    ) -> str:
        results = await exa_provider.find_similar(url, max_results)
        return "\n\n".join(
            f"## {r.title}\nURL: {r.url}\n{r.snippet}"
            for r in results
        )


_register_exa_if_configured(mcp)

_SOURCES_CACHE = SourcesCache(max_size=256)
_AVAILABLE_MODELS_CACHE: dict[tuple[str, str], list[str]] = {}
_AVAILABLE_MODELS_LOCK = asyncio.Lock()


async def _fetch_available_models(api_url: str, api_key: str) -> list[str]:
    import httpx

    models_url = f"{api_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            models_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()

    models: list[str] = []
    for item in (data or {}).get("data", []) or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.append(item["id"])
    return models


async def _get_available_models_cached(api_url: str, api_key: str) -> list[str]:
    key = (api_url, api_key)
    async with _AVAILABLE_MODELS_LOCK:
        if key in _AVAILABLE_MODELS_CACHE:
            return _AVAILABLE_MODELS_CACHE[key]

    try:
        models = await _fetch_available_models(api_url, api_key)
    except Exception:
        models = []

    async with _AVAILABLE_MODELS_LOCK:
        _AVAILABLE_MODELS_CACHE[key] = models
    return models


def _extra_results_to_sources(
    tavily_results: list[dict] | None,
    firecrawl_results: list[dict] | None,
) -> list[dict]:
    sources: list[dict] = []
    seen: set[str] = set()

    if firecrawl_results:
        for r in firecrawl_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item: dict = {"url": url, "provider": "firecrawl"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            desc = (r.get("description") or "").strip()
            if desc:
                item["description"] = desc
            sources.append(item)

    if tavily_results:
        for r in tavily_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item: dict = {"url": url, "provider": "tavily"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            content = (r.get("content") or "").strip()
            if content:
                item["description"] = content
            sources.append(item)

    return sources


def _format_grok_error(exc: Exception, api_key: str = "") -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        reason = httpx.codes(status).name or ""
        body = redact_sensitive_text(exc.response.text or "", api_key).strip()
        detail = f": {body}" if body else ""
        return f"Grok 调用失败: HTTP {status} ({reason}){detail}"

    if isinstance(exc, httpx.RequestError):
        msg = redact_sensitive_text(str(exc), api_key).strip()
        cls_name = exc.__class__.__name__
        return f"Grok 调用失败: 网络错误 ({cls_name})" + (f": {msg}" if msg else "")

    if isinstance(exc, ValueError):
        return f"Grok 调用失败: {exc}"

    msg = redact_sensitive_text(str(exc), api_key).strip()
    return f"Grok 调用失败: {exc.__class__.__name__}" + (f": {msg}" if msg else "")


def _format_sources_markdown(sources: list[dict]) -> str:
    lines = []
    for item in sources:
        url = item.get("url", "")
        title = item.get("title") or url
        title = title.replace("[", r"\[").replace("]", r"\]").replace("\n", " ")
        lines.append(f"- [{title}]({url})")
    return "\n".join(lines)


@mcp.tool(
    name="web_search",
    output_schema=None,
    description=f"""
    Deep web search via Grok. Always prefer searching over hallucination;
    this tool returns grounded answers with source citations.

    **Query Crafting:**
    - Write keyword fragments (2-6 words), NOT full sentences or questions.
      Good: "Python asyncio event loop performance"
      Bad: "Can you tell me about how Python's asyncio event loop performs?"
    - Strip conversation context: write self-contained queries.
      Bad: "What about performance?" (references prior discussion)
    - If multiple distinct angles exist, call this tool multiple times with
      one angle per call, OR use the plan_* pipeline (start with plan_intent)
      to decompose before executing.

    **Difficulty guide:**
    - Single clear question → call web_search directly with keyword fragments.
    - Multiple facets, comparisons, or high uncertainty → use plan_* pipeline
      first to decompose, then execute each sub-query with web_search.

    **Source Direction (choose by scenario):**
    Controls which SOURCE POSITION Grok prioritizes.
    
    For real-time/ongoing events (快速三路):
    - mainstream: Official/institutional view — the establishment narrative
    - diverse: Range of perspectives across the spectrum
    - critical: Perspectives that challenge the mainstream/official view
    
    For heavy contamination / deep decontamination (深层位置):
    - eyewitness: First-hand accounts, primary documents
    - adversarial: Losing side, critics of dominant narrative
    - external: Neutral third-party observers with no stake
    - comprehensive: ALL positions simultaneously
    
    Or free text (≤15 words) for custom targeting.

    **Timeout:**
    - Set timeout to override the default ({config.search_timeout_seconds}s) for this request.
      0 = use default. Increase for complex queries on slow networks.

    **Returns:**
    - session_id: string (pass to get_sources for full source list)
    - content: string (answer only)
    - sources_count: int
    """,
    meta={"version": "2.0.0", "author": "guda.studio"},
)
async def web_search(
    query: Annotated[str, "Clear, self-contained natural-language search query."],
    platform: Annotated[str, "Target platform to focus on (e.g., 'Twitter', 'GitHub', 'Reddit'). Leave empty for general web search."] = "",
    model: Annotated[str, "Optional model ID for this request. Only used when user explicitly provides it."] = "",
    extra_sources: Annotated[int, "Number of additional reference results from secondary search providers. Set 0 to disable. Default 0."] = 0,
    from_date: Annotated[str, "YYYY-MM-DD format start date filter for search results."] = "",
    to_date: Annotated[str, "YYYY-MM-DD format end date filter for search results."] = "",
    allowed_domains: Annotated[str, "Comma-separated list of domains to restrict search to."] = "",
    max_search_results: Annotated[int, "Maximum number of search results to use (0 = no limit, max 20)."] = 0,
    reasoning_effort: Annotated[str, "Reasoning effort level (low/medium/high/xhigh)."] = "",
    direction: Annotated[str, "Source position: mainstream / eyewitness / adversarial / external / comprehensive, or free text (≤15 words). See description for details."] = "",
    timeout: Annotated[int, "Override timeout (seconds) for this request. 0 = use default."] = 0,
) -> dict:
    session_id = new_session_id()
    try:
        api_url = config.grok_api_url
        api_key = config.grok_api_key
    except ValueError as e:
        await _SOURCES_CACHE.set(session_id, [])
        return {"session_id": session_id, "content": f"配置错误: {str(e)}", "sources_count": 0}

    effective_model = config.grok_model
    if model:
        available = await _get_available_models_cached(api_url, api_key)
        if available and model not in available:
            await _SOURCES_CACHE.set(session_id, [])
            return {"session_id": session_id, "content": f"无效模型: {model}", "sources_count": 0}
        effective_model = model

    effective_timeout = timeout if timeout > 0 else None
    grok_provider = GrokSearchProvider(api_url, api_key, effective_model, reasoning_effort=reasoning_effort, timeout=effective_timeout)

    # 计算额外信源配额
    has_tavily = bool(config.tavily_api_key)
    has_firecrawl = bool(config.firecrawl_api_key)
    firecrawl_count = 0
    tavily_count = 0
    if extra_sources > 0:
        if has_firecrawl and has_tavily:
            firecrawl_count = round(extra_sources * 1)
            tavily_count = extra_sources - firecrawl_count
        elif has_firecrawl:
            firecrawl_count = extra_sources
        elif has_tavily:
            tavily_count = extra_sources

    # 并行执行搜索任务
    async def _safe_grok() -> str:
        try:
            return await grok_provider.search(
                random.choice(SEARCH_FRAMINGS).format(query=query),
                platform=platform,
                from_date=from_date,
                to_date=to_date,
                allowed_domains=allowed_domains,
                max_search_results=max_search_results,
                direction=direction,
            )
        except Exception as e:
            return _format_grok_error(e, api_key)

    async def _safe_tavily() -> list[dict] | None:
        try:
            if tavily_count:
                return await _call_tavily_search(query, tavily_count)
        except Exception:
            return None

    async def _safe_firecrawl() -> list[dict] | None:
        try:
            if firecrawl_count:
                return await _call_firecrawl_search(query, firecrawl_count)
        except Exception:
            return None

    coros: list = [_safe_grok()]
    if tavily_count > 0:
        coros.append(_safe_tavily())
    if firecrawl_count > 0:
        coros.append(_safe_firecrawl())

    gathered = await asyncio.gather(*coros, return_exceptions=True)

    grok_result: str = gathered[0] if isinstance(gathered[0], str) else ""
    tavily_results: list[dict] | None = None
    firecrawl_results: list[dict] | None = None
    idx = 1
    if tavily_count > 0:
        tavily_results = gathered[idx]
        idx += 1
    if firecrawl_count > 0:
        firecrawl_results = gathered[idx]

    answer, grok_sources = split_answer_and_sources(grok_result)
    extra = _extra_results_to_sources(tavily_results, firecrawl_results)
    all_sources = merge_sources(grok_sources, extra)

    await _SOURCES_CACHE.set(session_id, all_sources)
    return {"session_id": session_id, "content": answer, "sources_count": len(all_sources)}


@mcp.tool(
    name="get_sources",
    description="""
    Retrieve cached sources from a previous web_search call using its session_id.

    **Important: Sources may expire from cache.** If you need the source list,
    call this tool as soon as possible after the search. An empty result with
    "session_id_not_found_or_expired" means the data is no longer available
    — re-run the search if sources are still needed.

    Returns:
    - sources: list[dict] — full source objects with title, URL, etc.
    - sources_markdown: str — formatted as "- [Title](URL)" lines.
    - sources_count: int.
    """,
    meta={"version": "2.0.0", "author": "guda.studio"},
)
async def get_sources(
    session_id: Annotated[str, "Session ID from previous web_search call."]
) -> dict:
    sources = await _SOURCES_CACHE.get(session_id)
    if sources is None:
        return {
            "session_id": session_id,
            "sources": [],
            "sources_markdown": "",
            "sources_count": 0,
            "error": "session_id_not_found_or_expired",
        }
    return {
        "session_id": session_id,
        "sources": sources,
        "sources_markdown": _format_sources_markdown(sources),
        "sources_count": len(sources),
    }


_HIKARI_MCP_PATH = "/mcp"
_HIKARI_TAVILY_API_PATH = "/api/tavily"


def _normalize_tavily_api_base_url(api_url: str) -> str:
    trimmed = api_url.rstrip("/")
    if trimmed.endswith(_HIKARI_MCP_PATH):
        prefix = trimmed[: -len(_HIKARI_MCP_PATH)]
        return f"{prefix}{_HIKARI_TAVILY_API_PATH}"
    return trimmed


def _build_tavily_map_body(
    url: str, instructions: str | None = None,
    max_depth: int = 1, max_breadth: int = 20,
    limit: int = 50, timeout: int = 150,
) -> dict:
    from urllib.parse import urlparse
    body: dict = {
        "url": url, "max_depth": max_depth,
        "max_breadth": max_breadth, "limit": limit, "timeout": timeout,
    }
    if instructions:
        body["instructions"] = instructions
    host = urlparse(url).hostname
    if host:
        body["allow_external"] = False
        body["select_domains"] = [host]
    return body


async def _call_tavily_extract(url: str, timeout: int | None = None) -> str | None:
    import httpx
    api_url = config.tavily_api_url
    api_key = config.tavily_api_key
    if not api_key:
        return None
    endpoint = f"{_normalize_tavily_api_base_url(api_url)}/extract"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"urls": [url], "format": "markdown"}
    try:
        t = timeout if timeout and timeout > 0 else config.fetch_timeout_seconds
        async with httpx.AsyncClient(timeout=float(t)) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            if data.get("results") and len(data["results"]) > 0:
                content = data["results"][0].get("raw_content", "")
                return content if content and content.strip() else None
            return None
    except Exception:
        return None


async def _call_tavily_search(query: str, max_results: int = 6) -> list[dict] | None:
    import httpx
    api_key = config.tavily_api_key
    if not api_key:
        return None
    endpoint = f"{_normalize_tavily_api_base_url(config.tavily_api_url)}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_raw_content": False,
        "include_answer": False,
    }
    try:
        async with httpx.AsyncClient(timeout=float(config.search_timeout_seconds)) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", ""), "score": r.get("score", 0)}
                for r in results
            ] if results else None
    except Exception:
        return None


async def _call_firecrawl_search(query: str, limit: int = 14) -> list[dict] | None:
    import httpx
    api_key = config.firecrawl_api_key
    if not api_key:
        return None
    endpoint = f"{config.firecrawl_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"query": query, "limit": limit}
    try:
        async with httpx.AsyncClient(timeout=float(config.search_timeout_seconds)) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            results = data.get("data", {}).get("web", [])
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "description": r.get("description", "")}
                for r in results
            ] if results else None
    except Exception:
        return None


async def _call_firecrawl_scrape(url: str, ctx=None, timeout: int | None = None) -> str | None:
    import httpx
    api_url = config.firecrawl_api_url
    api_key = config.firecrawl_api_key
    if not api_key:
        return None
    endpoint = f"{api_url.rstrip('/')}/scrape"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    max_retries = config.retry_max_attempts
    for attempt in range(max_retries):
        ft = timeout if timeout and timeout > 0 else config.fetch_timeout_seconds
        body = {
            "url": url,
            "formats": ["markdown"],
            "timeout": ft * 1000,
            "waitFor": (attempt + 1) * 1500,
        }
        try:
            async with httpx.AsyncClient(timeout=float(ft)) as client:
                response = await client.post(endpoint, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
                markdown = data.get("data", {}).get("markdown", "")
                if markdown and markdown.strip():
                    return markdown
                await log_info(ctx, f"Firecrawl: markdown为空, 重试 {attempt + 1}/{max_retries}", config.debug_enabled)
        except Exception as e:
            await log_info(ctx, f"Firecrawl error: {e}", config.debug_enabled)
            return None
    return None


@mcp.tool(
    name="web_fetch",
    output_schema=None,
    description="""
    Fetches URL content as structured Markdown. Tries Tavily → Firecrawl → Grok fallback.

    **Limitations:**
        - May not capture JavaScript-rendered content.
        - URL must be publicly accessible (no auth/paywalls).
    """,
    meta={"version": "1.3.0", "author": "guda.studio"},
)
async def web_fetch(
    url: Annotated[str, "Valid HTTP/HTTPS web address."],
    timeout: Annotated[int, "Override timeout (seconds). 0 = use default."] = 0,
    ctx: Context = None
) -> str:
    ft = timeout if timeout > 0 else config.fetch_timeout_seconds

    await log_info(ctx, f"Begin Fetch: {url}", config.debug_enabled)

    result = await _call_tavily_extract(url, ft)
    if result:
        await log_info(ctx, "Fetch Finished (Tavily)!", config.debug_enabled)
        return result

    await log_info(ctx, "Tavily unavailable or failed, trying Firecrawl...", config.debug_enabled)
    result = await _call_firecrawl_scrape(url, ctx, ft)
    if result:
        await log_info(ctx, "Fetch Finished (Firecrawl)!", config.debug_enabled)
        return result

    # Grok fallback
    if config.grok_fetch_fallback_enabled:
        await log_info(ctx, "Firecrawl unavailable or failed, trying Grok...", config.debug_enabled)
        try:
            api_url = config.grok_api_url
            api_key = config.grok_api_key
        except ValueError as e:
            await log_info(ctx, f"Grok fetch fallback: {e}", config.debug_enabled)
            return "提取失败: 所有提取服务均未能获取内容"
        provider = GrokSearchProvider(api_url, api_key, config.grok_model, timeout=ft)
        result = await provider.fetch(url, ctx)
        if result:
            await log_info(ctx, "Fetch Finished (Grok)!", config.debug_enabled)
            return result

    await log_info(ctx, "Fetch Failed!", config.debug_enabled)
    if not config.tavily_api_key and not config.firecrawl_api_key:
        return "配置错误: TAVILY_API_KEY 和 FIRECRAWL_API_KEY 均未配置"
    return "提取失败: 所有提取服务均未能获取内容"


async def _call_tavily_map(url: str, instructions: str = None, max_depth: int = 1,
                           max_breadth: int = 20, limit: int = 50, timeout: int = 150) -> str:
    import httpx
    import json
    api_url = config.tavily_api_url
    api_key = config.tavily_api_key
    if not api_key:
        return "配置错误: TAVILY_API_KEY 未配置，请设置环境变量 TAVILY_API_KEY"
    endpoint = f"{_normalize_tavily_api_base_url(api_url)}/map"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = _build_tavily_map_body(url, instructions, max_depth, max_breadth, limit, timeout)
    try:
        async with httpx.AsyncClient(timeout=float(timeout + 10)) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            return json.dumps({
                "base_url": data.get("base_url", ""),
                "results": data.get("results", []),
                "response_time": data.get("response_time", 0)
            }, ensure_ascii=False, indent=2)
    except httpx.TimeoutException:
        return f"映射超时: 请求超过{timeout}秒"
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400 and "allow_external" not in (e.response.text or ""):
            raise
        # allow_external/select_domains 不被 Tavily 版本支持，回退到无此参数
        legacy_body = {k: v for k, v in body.items() if k not in ("allow_external", "select_domains")}
        try:
            async with httpx.AsyncClient(timeout=float(timeout + 10)) as client:
                resp = await client.post(endpoint, headers=headers, json=legacy_body)
                resp.raise_for_status()
                data = resp.json()
                return json.dumps({
                    "base_url": data.get("base_url", ""),
                    "results": data.get("results", []),
                    "response_time": data.get("response_time", 0)
                }, ensure_ascii=False, indent=2)
        except httpx.TimeoutException:
            return f"映射超时: 请求超过{timeout}秒"
        except Exception as e2:
            return f"映射错误: {e2}"
    except Exception as e:
        return f"映射错误: {str(e)}"


@mcp.tool(
    name="web_map",
    description="""
    Maps a website's structure by traversing it like a graph, discovering URLs and generating a comprehensive site map.

    **Key Features:**
        - **Graph Traversal:** Explores website structure starting from root URL.
        - **Depth & Breadth Control:** Configure traversal limits to balance coverage and performance.
        - **Instruction Filtering:** Use natural language to focus crawler on specific content types.

    **Edge Cases & Best Practices:**
        - Start with low max_depth (1-2) for initial exploration, increase if needed.
        - Use instructions to filter for specific content (e.g., "only documentation pages").
        - Large sites may hit timeout limits; adjust timeout and limit parameters accordingly.
    """,
    meta={"version": "1.3.0", "author": "guda.studio"},
)
async def web_map(
    url: Annotated[str, "Root URL to begin the mapping (e.g., 'https://docs.example.com')."],
    instructions: Annotated[str, "Natural language instructions for the crawler to filter or focus on specific content."] = "",
    max_depth: Annotated[int, Field(description="Maximum depth of mapping from the base URL.", ge=1, le=5)] = 1,
    max_breadth: Annotated[int, Field(description="Maximum number of links to follow per page.", ge=1, le=500)] = 20,
    limit: Annotated[int, Field(description="Total number of links to process before stopping.", ge=1, le=500)] = 50,
    timeout: Annotated[int, Field(description="Maximum time in seconds for the operation.", ge=10, le=150)] = 150
) -> str:
    result = await _call_tavily_map(url, instructions, max_depth, max_breadth, limit, timeout)
    return result


@mcp.tool(
    name="get_config_info",
    output_schema=None,
    description="""
    Returns current Grok Search MCP server configuration and tests API connectivity.

    **Key Features:**
        - **Configuration Check:** Verifies environment variables and current settings.
        - **Connection Test:** Sends request to /models endpoint to validate API access.
        - **Model Discovery:** Lists all available models from the API.

    **Edge Cases & Best Practices:**
        - Use this tool first when debugging connection or configuration issues.
        - API keys are automatically masked for security in the response.
        - Connection test timeout is 10 seconds; network issues may cause delays.
    """,
    meta={"version": "1.3.0", "author": "guda.studio"},
)
async def get_config_info() -> str:
    import json
    import httpx
    import time
    import asyncio

    config_info = config.get_config_info()

    transport = "responses" if (
        config.force_responses_api or "multi-agent" in config.grok_model.lower()
    ) else "chat_completions"

    async def _probe_grok():
        try:
            api_url = config.grok_api_url
            api_key = config.grok_api_key
            models_url = f"{api_url.rstrip('/')}/models"
            start = time.time()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    models_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                )
                ms = round((time.time() - start) * 1000, 2)
                models = []
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        if "data" in data and isinstance(data["data"], list):
                            models = [m["id"] for m in data["data"] if isinstance(m, dict) and "id" in m]
                    except Exception:
                        pass
                return {
                    "api_url": config_info.get("GROK_API_URL", ""),
                    "model": config.grok_model,
                    "auth_mode": "api_key",
                    "web_search_enabled": config.web_search_tool_enabled,
                    "x_search_enabled": transport == "responses",
                    "reachable": resp.status_code == 200,
                    "detail": "ok" if resp.status_code == 200 else f"HTTP {resp.status_code}",
                    "response_time_ms": ms,
                    "available_models": models,
                }
        except httpx.TimeoutException:
            return {"api_url": config_info.get("GROK_API_URL", ""), "model": config.grok_model, "auth_mode": "api_key", "reachable": False, "detail": "timeout", "response_time_ms": 5000}
        except Exception as e:
            return {"api_url": config_info.get("GROK_API_URL", ""), "model": config.grok_model, "auth_mode": "api_key", "reachable": False, "detail": str(e)[:200]}

    async def _probe_tavily():
        if not config.tavily_api_key:
            return {"api_url": config.tavily_api_url, "configured": False, "reachable": None, "detail": "skipped (not configured)"}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(config.tavily_api_url)
                return {"api_url": config.tavily_api_url, "configured": True, "reachable": True, "detail": "ok"}
        except Exception as e:
            return {"api_url": config.tavily_api_url, "configured": True, "reachable": False, "detail": str(e)[:100]}

    async def _probe_firecrawl():
        if not config.firecrawl_api_key:
            return {"api_url": config.firecrawl_api_url, "configured": False, "reachable": None, "detail": "skipped (not configured)"}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(config.firecrawl_api_url)
                return {"api_url": config.firecrawl_api_url, "configured": True, "reachable": True, "detail": "ok"}
        except Exception as e:
            return {"api_url": config.firecrawl_api_url, "configured": True, "reachable": False, "detail": str(e)[:100]}

    grok_status, tavily_status, firecrawl_status = await asyncio.gather(
        _probe_grok(), _probe_tavily(), _probe_firecrawl()
    )

    connection_ok = grok_status.get("reachable", False)

    return json.dumps({
        "config": config_info,
        "transport": transport,
        "status": "✅ 连接成功" if connection_ok else "⚠️ 部分服务不可用",
        "grok": grok_status,
        "tavily": tavily_status,
        "firecrawl": firecrawl_status,
        "connection_test": {
            "status": "✅ 连接成功" if connection_ok else "❌ 连接失败",
            "message": f"Grok: {'可达' if connection_ok else '不可达'}, "
                       f"Tavily: {tavily_status.get('detail', '未知')}, "
                       f"Firecrawl: {firecrawl_status.get('detail', '未知')}",
            "response_time_ms": grok_status.get("response_time_ms", 0),
            "available_models": grok_status.get("available_models", []),
        },
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="switch_model",
    output_schema=None,
    description="""
    Switches the default Grok model used for search and fetch operations, persisting the setting.

    **Key Features:**
        - **Model Selection:** Change the AI model for web search and content fetching.
        - **Persistent Storage:** Model preference saved to ~/.config/grok-search/config.json.
        - **Immediate Effect:** New model used for all subsequent operations.

    **Edge Cases & Best Practices:**
        - Use get_config_info to verify available models before switching.
        - Invalid model IDs may cause API errors in subsequent requests.
        - Model changes persist across sessions until explicitly changed again.
    """,
    meta={"version": "1.3.0", "author": "guda.studio"},
)
async def switch_model(
    model: Annotated[str, "Model ID to switch to (e.g., 'grok-4-fast', 'grok-2-latest', 'grok-vision-beta')."]
) -> str:
    import json

    try:
        previous_model = config.grok_model
        config.set_model(model)
        current_model = config.grok_model

        result = {
            "status": "✅ 成功",
            "previous_model": previous_model,
            "current_model": current_model,
            "message": f"模型已从 {previous_model} 切换到 {current_model}",
            "config_file": str(config.config_file)
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except ValueError as e:
        result = {
            "status": "❌ 失败",
            "message": f"切换模型失败: {str(e)}"
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        result = {
            "status": "❌ 失败",
            "message": f"未知错误: {str(e)}"
        }
        return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="toggle_builtin_tools",
    output_schema=None,
    description="""
    Toggle Claude Code's built-in WebSearch and WebFetch tools on/off.

    **Key Features:**
        - **Tool Control:** Enable or disable Claude Code's native web tools.
        - **Project Scope:** Changes apply to current project's .claude/settings.json.
        - **Status Check:** Query current state without making changes.

    **Edge Cases & Best Practices:**
        - Use "on" to block built-in tools when preferring this MCP server's implementation.
        - Use "off" to restore Claude Code's native tools.
        - Use "status" to check current configuration without modification.
    """,
    meta={"version": "1.3.0", "author": "guda.studio"},
)
async def toggle_builtin_tools(
    action: Annotated[str, "Action to perform: 'on' (block built-in), 'off' (allow built-in), or 'status' (check current state)."] = "status"
) -> str:
    import json

    # Locate project root
    root = Path.cwd()
    while root != root.parent and not (root / ".git").exists():
        root = root.parent

    settings_path = root / ".claude" / "settings.json"
    tools = ["WebFetch", "WebSearch"]

    # Load or initialize
    if settings_path.exists():
        with open(settings_path, 'r', encoding='utf-8') as f:
            settings = json.load(f)
    else:
        settings = {"permissions": {"deny": []}}

    deny = settings.setdefault("permissions", {}).setdefault("deny", [])
    blocked = all(t in deny for t in tools)

    # Execute action
    if action in ["on", "enable"]:
        for t in tools:
            if t not in deny:
                deny.append(t)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        msg = "官方工具已禁用"
        blocked = True
    elif action in ["off", "disable"]:
        deny[:] = [t for t in deny if t not in tools]
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        msg = "官方工具已启用"
        blocked = False
    else:
        msg = f"官方工具当前{'已禁用' if blocked else '已启用'}"

    return json.dumps({
        "blocked": blocked,
        "deny_list": deny,
        "file": str(settings_path),
        "message": msg
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_intent",
    output_schema=None,
    description="""
    Phase 1/6: Capture user intent into a core question.
    Call this FIRST — returns session_id for subsequent phases.
    Previous: (none — start here)
    Next: plan_complexity

    **Key parameters (fill these):**
    - core_question: Distill the search into ONE clear sentence.
    - query_type: factual | comparative | exploratory | analytical.
    - time_sensitivity: realtime | recent | historical | irrelevant.

    **Additional parameters (use when relevant):**
    - ambiguities: note unresolved uncertainties to address in later phases.
    - unverified_terms: flag terms that may need verification before search.
    - domain: narrow the search context if the domain is identifiable.
    - premise_valid: set to false if the question rests on a flawed assumption.
    - contamination_suspected: set true if the topic involves high power asymmetry,
      concentrated voice, known data contamination history, or strong incentive
      asymmetry. When true, a decontamination flag will appear in the response.
    - confidence, is_revision: internal bookkeeping — not needed for initial creation.

    Full pipeline: plan_intent → plan_complexity → plan_sub_query(×N) →
    plan_search_term(×N) → plan_tool_mapping(×N, skip if all web_search) → plan_execution

    **Contamination side-pipeline** (skippable, see contamination_flag in response):
    decon_assess → decon_verify → decon_provenance →
    decon_motive → decon_synthesis → decon_patterns
    """,
)
async def plan_intent(
    thought: Annotated[str, "Reasoning for this phase"],
    core_question: Annotated[str, "Distilled core question in one sentence"],
    query_type: Annotated[str, "factual | comparative | exploratory | analytical"],
    time_sensitivity: Annotated[str, "realtime | recent | historical | irrelevant"],
    session_id: Annotated[str, "Empty for new session, or existing ID to revise"] = "",
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    domain: Annotated[str, "Specific domain if identifiable"] = "",
    premise_valid: Annotated[bool, "False if the question contains a flawed assumption"] = True,
    ambiguities: Annotated[str, "Comma-separated unresolved ambiguities"] = "",
    unverified_terms: Annotated[str, "Comma-separated external terms to verify"] = "",
    contamination_suspected: Annotated[bool, "True if topic likely has data contamination"] = False,
    is_revision: Annotated[bool, "True to overwrite existing intent"] = False,
) -> str:
    import json
    data = {"core_question": core_question, "query_type": query_type, "time_sensitivity": time_sensitivity}
    if domain:
        data["domain"] = domain
    if not premise_valid:
        data["premise_valid"] = premise_valid
    if ambiguities:
        data["ambiguities"] = _split_csv(ambiguities)
    if unverified_terms:
        data["unverified_terms"] = _split_csv(unverified_terms)
    if contamination_suspected:
        data["contamination_suspected"] = True
    result = planning_engine.process_phase(
        phase="intent_analysis", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence, phase_data=data,
    )
    if contamination_suspected:
        result["contamination_flag"] = {
            "detected": True,
            "level": "pending_assessment",
            "phases_available": [
                "decon_verify", "decon_provenance",
                "decon_motive", "decon_synthesis",
            ],
            "can_skip": True,
            "note": "Call decon_assess to begin, or skip and continue with plan_complexity",
        }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_complexity",
    output_schema=None,
    description="""
    Phase 2/6: Assess search complexity (1-3).
    Previous: plan_intent
    Next: plan_sub_query

    **Level guide:**
    - L1 (simple): Single-facet factual/quick question → ~1-2 sub-queries, skip tool_mapping.
    - L2 (moderate): Multi-facet comparison or exploration → ~3-5 sub-queries.
    - L3 (complex): Cross-tool / multi-round / high ambiguity → 5+ sub-queries,
      may need web_fetch or multi-round iteration.

    The level determines how many sub-queries you'll create — pick the
    lowest that fits. estimated_sub_queries and estimated_tool_calls are
    planning estimates, not strict limits.
    """,
)
async def plan_complexity(
    session_id: Annotated[str, "Session ID from plan_intent"],
    thought: Annotated[str, "Reasoning for complexity assessment"],
    level: Annotated[int, "Complexity 1-3"],
    estimated_sub_queries: Annotated[int, "Expected number of sub-queries"],
    estimated_tool_calls: Annotated[int, "Expected total tool calls"],
    justification: Annotated[str, "Why this complexity level"],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    is_revision: Annotated[bool, "True to overwrite"] = False,
) -> str:
    import json
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    return json.dumps(planning_engine.process_phase(
        phase="complexity_assessment", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence,
        phase_data={"level": level, "estimated_sub_queries": estimated_sub_queries,
                     "estimated_tool_calls": estimated_tool_calls, "justification": justification},
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_sub_query",
    output_schema=None,
    description="""
    Phase 3/6: Add one sub-query. Call once per sub-query; data accumulates.
    Previous: plan_complexity
    Next: plan_search_term (or plan_execution if all terms are known)

    **Key parameters:**
    - id: Unique label (sq1, sq2, ...).
    - goal: What this sub-query aims to find.
    - expected_output: What success looks like.
    - boundary: What this excludes — prevents overlap with sibling queries.

    **Optional:**
    - depends_on: comma-separated IDs of sub-queries that must complete first.
    - tool_hint: defaults to "web_search". Only set to "web_fetch" or
      "web_map" if the sub-query specifically needs a different tool.
    """,
)
async def plan_sub_query(
    session_id: Annotated[str, "Session ID from plan_intent"],
    thought: Annotated[str, "Reasoning for this sub-query"],
    id: Annotated[str, "Unique ID (e.g., 'sq1')"],
    goal: Annotated[str, "Sub-query goal"],
    expected_output: Annotated[str, "What success looks like"],
    boundary: Annotated[str, "What this excludes — mutual exclusion with siblings"],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    depends_on: Annotated[str, "Comma-separated prerequisite IDs"] = "",
    tool_hint: Annotated[str, "web_search | web_fetch | web_map"] = "",
    is_revision: Annotated[bool, "True to replace all sub-queries"] = False,
) -> str:
    import json
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    item = {"id": id, "goal": goal, "expected_output": expected_output, "boundary": boundary}
    if depends_on:
        item["depends_on"] = _split_csv(depends_on)
    if tool_hint:
        item["tool_hint"] = tool_hint
    return json.dumps(planning_engine.process_phase(
        phase="query_decomposition", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence, phase_data=item,
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_search_term",
    output_schema=None,
    description="""
    Phase 4/6: Add one search term. Call once per term; data accumulates.
    Previous: plan_sub_query
    Next: plan_tool_mapping (or plan_execution if skipping mapping)

    **Key parameters:**
    - term: Search keyword (max 8 words) — keep tight and specific.
    - purpose: Which sub-query this serves (e.g., 'sq1').

    **Crafting Guide:**
    - Write from the *target article's perspective*: what title or phrase
      would a paper about this use? (Not your question, but its answer.)
    - Decompose the sub-query into 2-3 concrete concepts, then pick the
      most distinctive term for each. Combine only the essential ones.
    - Use domain vocabulary: academic sources use formal terminology,
      forums use slang, news uses plain language — match the genre.
    - Round 1 = broad discovery with generic terms.
      Round 2+ = include specific names, terms, or sources found in
      round 1 results. Iterate.

    **Optional:**
    - approach: broad_first | narrow_first | targeted — only needed on
      the first call per sub-query; skip if unsure.
    - round: 1=broad discovery, 2+=targeted follow-up.
    - fallback_plan: backup term if primary fails.
    """,
)
async def plan_search_term(
    session_id: Annotated[str, "Session ID from plan_intent"],
    thought: Annotated[str, "Reasoning for this search term"],
    term: Annotated[str, "Search query (max 8 words)"],
    purpose: Annotated[str, "Sub-query ID this serves (e.g., 'sq1')"],
    round: Annotated[int, "Execution round: 1=broad, 2+=targeted follow-up"],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    approach: Annotated[str, "broad_first | narrow_first | targeted (required on first call)"] = "",
    fallback_plan: Annotated[str, "Fallback if primary searches fail"] = "",
    is_revision: Annotated[bool, "True to replace all search terms"] = False,
) -> str:
    import json
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    data = {"search_terms": [{"term": term, "purpose": purpose, "round": round}]}
    if approach:
        data["approach"] = approach
    if fallback_plan:
        data["fallback_plan"] = fallback_plan
    return json.dumps(planning_engine.process_phase(
        phase="search_strategy", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence, phase_data=data,
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_tool_mapping",
    output_schema=None,
    description="""
    Phase 5/6 (optional): Map a sub-query to a tool. Call once per mapping.
    Previous: plan_search_term
    Next: plan_execution

    **Skip this phase entirely** if all sub-queries use web_search (the default).
    Only needed when a sub-query specifically requires web_fetch or web_map.

    Parameters:
    - sub_query_id: Which sub-query to map.
    - tool: web_search | web_fetch | web_map.
    - reason: Why this tool is needed instead of the default.
    - params_json: optional tool-specific parameters (JSON string).
    """,
)
async def plan_tool_mapping(
    session_id: Annotated[str, "Session ID from plan_intent"],
    thought: Annotated[str, "Reasoning for this mapping"],
    sub_query_id: Annotated[str, "Sub-query ID to map"],
    tool: Annotated[str, "web_search | web_fetch | web_map"],
    reason: Annotated[str, "Why this tool for this sub-query"],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    params_json: Annotated[str, "Optional JSON string for tool-specific params"] = "",
    is_revision: Annotated[bool, "True to replace all mappings"] = False,
) -> str:
    import json
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    item = {"sub_query_id": sub_query_id, "tool": tool, "reason": reason}
    if params_json:
        try:
            item["params"] = json.loads(params_json)
        except json.JSONDecodeError:
            pass
    return json.dumps(planning_engine.process_phase(
        phase="tool_selection", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence, phase_data=item,
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_execution",
    output_schema=None,
    description=f"""
    Phase 6/6: Define execution order.
    Previous: plan_tool_mapping (or plan_search_term if mapping skipped)
    Next: execute searches

    **CRITICAL — Parallelism rules:**
    - parallel_groups: semicolon = groups, comma = IDs (e.g., 'sq1,sq2;sq3').
      ALL sub-queries in the SAME group must be fired SIMULTANEOUSLY
      in a single message via concurrent async calls — do NOT do them
      sequentially one by one.
    - If one sub-query times out, do NOT block other group members.
      If the result is still needed, immediately re-issue it — optionally
      with a larger timeout value (default {config.search_timeout_seconds}s).
    - sequential: comma-separated IDs that depend on earlier results
      (rare — most sub-queries are independent).

    **Tip:** When executing, use web_search's `direction` parameter for
    source perspective control on controversial or multi-faceted topics.
    """,
)
async def plan_execution(
    session_id: Annotated[str, "Session ID from plan_intent"],
    thought: Annotated[str, "Reasoning for execution order"],
    parallel_groups: Annotated[str, "Parallel batches: 'sq1,sq2;sq3,sq4' (semicolon=groups, comma=IDs)"],
    sequential: Annotated[str, "Comma-separated IDs that must run in order"],
    estimated_rounds: Annotated[int, "Estimated execution rounds"],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    is_revision: Annotated[bool, "True to overwrite"] = False,
) -> str:
    import json
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    parallel = [_split_csv(g) for g in parallel_groups.split(";") if g.strip()] if parallel_groups else []
    seq = _split_csv(sequential)
    return json.dumps(planning_engine.process_phase(
        phase="execution_order", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence,
        phase_data={"parallel": parallel, "sequential": seq, "estimated_rounds": estimated_rounds},
    ), ensure_ascii=False, indent=2)


# ── Decontamination Pipeline Tools ──────────────────────────────────────


@mcp.tool(
    name="decon_assess",
    output_schema=None,
    description="""
    Phase 0/6: Assess contamination suspicion level.
    Call after plan_intent if contamination may be present — determines
    whether to proceed with decontamination or skip to normal plan flow.

    Previous: plan_intent (when contamination_flag.detected is true)
    Next: low → skip (continue plan); medium/high → decon_verify

    **Assessment (internal reasoning, no search needed):**
    Look for observable clues that suggest information may be managed:
    - How much power or money is at stake for key actors?
    - Are sources concentrated in one direction?
    - Does this topic have known history of data manipulation?
    - Do stakeholders have clear incentive to distort?
    - Is there a clean data baseline, or was the foundation laid under biased conditions?

    Output contamination_level (low/medium/high) and list which dimensions were observed.
    """,
)
async def decon_assess(
    session_id: Annotated[str, "Session ID from plan_intent"],
    clues: Annotated[str, "Concise key findings (max 2000 chars). Not a full reasoning transcript — just the conclusions and detected patterns the next phase needs."],
    domain: Annotated[str, "Domain/topic being assessed"],
    contamination_level: Annotated[str, "low | medium | high"] = "low",
    contamination_dimensions: Annotated[str, "Comma-separated detected dimensions (e.g. 'source_concentration,incentive_asymmetry')"] = "",
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
) -> str:
    import json
    budget = int(os.environ.get("GROK_SEARCH_DECON_THOUGHT_BUDGET", "2000"))
    if len(clues) > budget:
        clues = clues[:budget] + "..."
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    data = {
        "contamination_level": contamination_level,
        "contamination_dimensions": [c.strip() for c in contamination_dimensions.split(",") if c.strip()],
        "domain": domain,
    }
    return json.dumps(decon_engine.process_phase(
        phase="decon_assess", thought=clues, session_id=session_id,
        confidence=confidence, phase_data=data,
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="decon_motive",
    output_schema=None,
    description="""
    Phase 3/6: Incentive analysis.
    Previous: decon_provenance
    Next: decon_synthesis

    For dominant narratives in the topic:
    1. Who benefits from this narrative being widely accepted?
    2. Can the beneficiaries control information production or distribution?
    3. Does a counter-narrative exist, and if not, what might explain its absence?

    List findings without drawing final conclusions — the user decides.
    """,
)
async def decon_motive(
    session_id: Annotated[str, "Session ID from decon_assess"],
    clues: Annotated[str, "Concise key findings (max 2000 chars). Not full reasoning — just conclusions and detected incentive patterns."],
    key_claims: Annotated[str, "Comma-separated core claims to analyze"] = "",
    narrative_analysis: Annotated[str, "JSON array: [{narrative, beneficiaries, incentive_asymmetry}]"] = "",
    overall_assessment: Annotated[str, "Summary of incentive analysis findings"] = "",
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
) -> str:
    import json
    budget = int(os.environ.get("GROK_SEARCH_DECON_THOUGHT_BUDGET", "2000"))
    if len(clues) > budget:
        clues = clues[:budget] + "..."
    sess = decon_engine.get_session(session_id)
    if not sess or "decon_assess" not in sess.phases:
        return json.dumps({"error": "Call decon_assess first."})
    data = {}
    if key_claims:
        data["key_claims"] = [c.strip() for c in key_claims.split(",") if c.strip()]
    if narrative_analysis:
        try:
            data["stakeholder_narrative_map"] = json.loads(narrative_analysis)
        except json.JSONDecodeError:
            pass
    if overall_assessment:
        data["overall_assessment"] = overall_assessment
    return json.dumps(decon_engine.process_phase(
        phase="decon_motive", thought=clues, session_id=session_id,
        confidence=confidence, phase_data=data,
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="decon_verify",
    output_schema=None,
    description="""
    Phase 1/6: Factual verification — definitions first, then numbers.
    Previous: decon_assess
    Next: decon_provenance

    **Protocol: definitions first, numbers second.**
    Start by checking whether core concepts in the topic have stable definitions
    or carry hidden framing. Only after clarifying definitions, check numerical claims.

    **Step 1 — Definition check:**
    - Do key terms contain built-in assumptions or slanted framing?
      (e.g., "为什么X这么差" assumes X IS差 before any evidence)
    - Has the definition of core concepts shifted over time?
    - Are different sources using the same term to mean different things?

    **Step 2 — Numerical check:**
    - Do stated numbers exceed physical/mathematical bounds?
      (population × time, area × density, rate × duration)
    - Are trends suspiciously uniform or perfect?
    - Could definitional differences explain numerical discrepancies?

    If you need baseline data, call web_search first with domain-specific terms,
    then pass results via check_results. This tool does NOT auto-search.
    """,
)
async def decon_verify(
    session_id: Annotated[str, "Session ID from decon_assess"],
    clues: Annotated[str, "Concise key findings (max 2000 chars). Conclusions and detected definitional/numerical anomalies."],
    key_concepts: Annotated[str, "Comma-separated core concepts to check for definitional bias"] = "",
    statistical_claims: Annotated[str, "Comma-separated numerical claims to verify"] = "",
    search_baselines: Annotated[bool, "Set true if you will search for external baseline data via web_search first"] = False,
    check_results: Annotated[str, "JSON array: [{claim, baseline, actual, severity}] from web_search"] = "",
    overall_assessment: Annotated[str, "Summary of verification findings"] = "",
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
) -> str:
    import json
    budget = int(os.environ.get("GROK_SEARCH_DECON_THOUGHT_BUDGET", "2000"))
    if len(clues) > budget:
        clues = clues[:budget] + "..."
    sess = decon_engine.get_session(session_id)
    if not sess or "decon_assess" not in sess.phases:
        return json.dumps({"error": "Call decon_assess first."})
    data = {}
    if key_concepts:
        data["key_concepts"] = [c.strip() for c in key_concepts.split(",") if c.strip()]
    if statistical_claims:
        data["statistical_claims"] = [c.strip() for c in statistical_claims.split(",") if c.strip()]
    if search_baselines:
        data["search_baselines"] = True
    if check_results:
        try:
            data["checks"] = json.loads(check_results)
        except json.JSONDecodeError:
            pass
    if overall_assessment:
        data["overall"] = overall_assessment
    result = decon_engine.process_phase(
        phase="decon_verify", thought=clues, session_id=session_id,
        confidence=confidence, phase_data=data,
    )
    if search_baselines and not check_results:
        result["warning"] = "search_baselines=true but no check_results provided. Call web_search first."
    if not search_baselines and not check_results and statistical_claims:
        result["note"] = "No baseline search performed. Numerical checks rely on LLM internal knowledge."
    if key_concepts and not statistical_claims:
        result["note"] = "Only definitional check performed. No numerical claims to verify."
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="decon_provenance",
    output_schema=None,
    description="""
    Phase 2/6: Claim provenance and propagation tracing.
    Previous: decon_verify
    Next: decon_motive

    **Two-phase protocol (Option C):**
    1. COLLECT: Call web_search for EACH claim in claims_to_trace. Save the resulting
       session_id from each search call. Do NOT skip this — your training data may
       contain the contamination itself, making it unreliable for provenance tracing.
    2. ANALYZE: Call this tool with those session_ids in search_evidence PLUS your
       provenance_chains analysis based on what the search actually found.

    **CRITICAL: Without search_evidence, provenance_chains may be fabricated from
    training data. Always search first.**

    Traces:
    1. Earliest known origin of key claims — search for which source first made the claim.
    2. Propagation path and changes at each hop — search for how it was repeated/modified.
    3. Citation cascade detection (self-reinforcing citation loops).
    4. Information laundering pattern recognition.
    
    **Important: distinguish "real fragment" from "exaggerated claim."**
    A claim often has a kernel of truth (a real event/person) that gets inflated
    into something far larger. Identify both: what actually happened AND how
    it was amplified.

    Parameters:
    - session_id: From decon_assess.
    - claims_to_trace: Comma-separated specific claims to trace.
    - search_evidence: Comma-separated web_search session_ids as proof of search (REQUIRED).
    - max_searches: Maximum search attempts per claim (default 5).
    - clues: Concise key findings from this phase.
    """,
)
async def decon_provenance(
    session_id: Annotated[str, "Session ID from decon_assess"],
    claims_to_trace: Annotated[str, "Comma-separated claims to trace"],
    clues: Annotated[str, "Concise key findings (max 2000 chars). Tracking results and laundering detection patterns."],
    search_evidence: Annotated[str, "Comma-separated web_search session_ids as proof of search"] = "",
    max_searches: Annotated[int, "Max search attempts per claim"] = 5,
    provenance_chains: Annotated[str, "JSON array: [{claim, origin, laundering_path, verification_added, conclusion, confidence}]"] = "",
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
) -> str:
    import json
    budget = int(os.environ.get("GROK_SEARCH_DECON_THOUGHT_BUDGET", "2000"))
    if len(clues) > budget:
        clues = clues[:budget] + "..."
    sess = decon_engine.get_session(session_id)
    if not sess or "decon_assess" not in sess.phases:
        return json.dumps({"error": "Call decon_assess first."})
    data = {
        "claims_to_trace": [c.strip() for c in claims_to_trace.split(",") if c.strip()],
        "max_searches": max_searches,
    }
    if search_evidence:
        data["search_evidence"] = [s.strip() for s in search_evidence.split(",") if s.strip()]
    if provenance_chains:
        try:
            data["provenance_chains"] = json.loads(provenance_chains)
        except json.JSONDecodeError:
            pass
    result = decon_engine.process_phase(
        phase="decon_provenance", thought=clues, session_id=session_id,
        confidence=confidence, phase_data=data,
    )
    if not search_evidence and provenance_chains:
        result["warning"] = "No search_evidence provided. Provenance chains may be fabricated from training data, which may contain the contamination itself."
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="decon_synthesis",
    output_schema=None,
    description="""
    Phase 4/6: Cross-source synthesis and direction suggestions.
    Previous: decon_motive
    Next: decon_patterns (if contamination was medium/high)

    **Protocol:**
    1. COLLECT: Call web_search with direction=comprehensive to gather sources
       from ALL positions on this topic. Save session_ids. For deeper coverage,
       also search with direction=adversarial and direction=eyewitness separately.
    2. ANALYZE: Compare findings from different search directions.
    3. OUTPUT corrected search direction suggestions for the user to consider.

    Produces:
    1. What different search directions returned — summarize, don't adjudicate.
    2. Where sources agree and disagree — describe the pattern, don't judge it.
    3. Corrected search direction suggestions — alternative terms or angles
       the user might want to try based on what wasn't found, not what was.

    **source_reliability should follow this A-G tiering framework (domain-agnostic):**
    A = primary eyewitness (weight ~0.85, direct first-hand accounts, no known bias incentive)
    B = compiled eyewitness (~0.80, collections of primary accounts)
    C = contemporary official (~0.50, records from the period, carries establishment bias)
    D = external observer with interests (~0.35, foreign reporters/NGOs/missionaries, may have ulterior motives)
    E = independent third-party (~0.65, external records with no stake in the outcome)
    F = winning side's official records (~0.15, victor's historiography, known tampering motive)
    G = modern secondary (~0.10, reference only, must trace original source)

    Parameters:
    - session_id: From decon_assess.
    - search_evidence: Comma-separated web_search session_ids from cross-incentive searches.
    - clues: Concise key findings from this phase.
    """,
)
async def decon_synthesis(
    session_id: Annotated[str, "Session ID from decon_assess"],
    clues: Annotated[str, "Concise key findings (max 2000 chars). Cross-incentive patterns and corrected directions."],
    search_evidence: Annotated[str, "Comma-separated web_search session_ids from cross-incentive searches"] = "",
    corrected_directions: Annotated[str, "JSON array: [{original_term, corrected_term, bias_correction}]"] = "",
    source_reliability: Annotated[str, "JSON object mapped to A-G tiering: {tier_A: {weight, caution, example_sources}}. See description for tier definitions."] = "",
    contamination_summary: Annotated[str, "Final summary of contamination findings"] = "",
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
) -> str:
    import json
    budget = int(os.environ.get("GROK_SEARCH_DECON_THOUGHT_BUDGET", "2000"))
    if len(clues) > budget:
        clues = clues[:budget] + "..."
    sess = decon_engine.get_session(session_id)
    if not sess or "decon_assess" not in sess.phases:
        return json.dumps({"error": "Call decon_assess first."})
    data = {}
    if search_evidence:
        data["search_evidence"] = [s.strip() for s in search_evidence.split(",") if s.strip()]
    if corrected_directions:
        try:
            data["corrected_search_directions"] = json.loads(corrected_directions)
        except json.JSONDecodeError:
            pass
    if source_reliability:
        try:
            data["source_reliability"] = json.loads(source_reliability)
        except json.JSONDecodeError:
            pass
    if contamination_summary:
        data["contamination_summary"] = contamination_summary
    result = decon_engine.process_phase(
        phase="decon_synthesis", thought=clues, session_id=session_id,
        confidence=confidence, phase_data=data,
    )
    if not search_evidence and (corrected_directions or contamination_summary):
        result["warning"] = "No search_evidence provided. Cross-incentive analysis may reflect only the LLM's training data, not actual opposing-incentive sources."
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="decon_patterns",
    output_schema=None,
    description="""
    Phase 5/6: Reference card — known information manipulation patterns.
    Previous: decon_synthesis (call when contamination was medium/high)
    Next: (terminal)

    Call this after decon_synthesis when contamination was detected.
    Your job is ONLY to present this reference card to the user.
    Do NOT analyze, match, or diagnose — just relay the information.

    ---

    Known patterns observed across domains (reference only):

    1. Source concentration — one voice overwhelmingly dominates,
       counter-narratives are absent from the information channels searched.

    2. Incentive asymmetry — one party has strong motivation to distort,
       the other side lacks resources or access to correct the record.

    3. Definitional slant — the question or framing itself embeds
       an unverified assumption. "Why is X so bad?" assumes X IS bad.
       Always check "先问是不是，再问为什么".

    4. Numerical/scope anomaly — numbers exceed physical bounds,
       or definition drift changed what's being counted.
       Compare like-with-like across sources.

    5. Source laundering — information originates from a biased source,
       then gets repeated by neutral-looking intermediaries without
       attribution, gaining false credibility.

    6. Truth kernel inflation — a real event or person forms the core,
       but the claim has been expanded far beyond original evidence.

    How to use: if any of these resonate with what you're seeing,
    follow that intuition in your own investigation.
    The pipeline does not diagnose — this is for your reference.
    """,
)
async def decon_patterns(
    session_id: Annotated[str, "Session ID from decon_assess"],
    clues: Annotated[str, "Concise key findings (max 2000 chars). Patterns that resonated with the investigation."],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
) -> str:
    import json
    budget = int(os.environ.get("GROK_SEARCH_DECON_THOUGHT_BUDGET", "2000"))
    if len(clues) > budget:
        clues = clues[:budget] + "..."
    sess = decon_engine.get_session(session_id)
    if not sess or "decon_assess" not in sess.phases:
        return json.dumps({"error": "Call decon_assess first."})
    result = decon_engine.process_phase(
        phase="decon_patterns", thought=clues, session_id=session_id,
        confidence=confidence, phase_data={},
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


def main():
    import signal
    import os
    import threading

    # 信号处理（仅主线程）
    if threading.current_thread() is threading.main_thread():
        def handle_shutdown(signum, frame):
            os._exit(0)
        signal.signal(signal.SIGINT, handle_shutdown)
        if sys.platform != 'win32':
            signal.signal(signal.SIGTERM, handle_shutdown)

    # Windows 父进程监控
    if sys.platform == 'win32':
        import time
        import ctypes
        parent_pid = os.getppid()

        def is_parent_alive(pid):
            """Windows 下检查进程是否存活"""
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return True
            exit_code = ctypes.c_ulong()
            result = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return result and exit_code.value == STILL_ACTIVE

        def monitor_parent():
            while True:
                if not is_parent_alive(parent_pid):
                    os._exit(0)
                time.sleep(2)

        threading.Thread(target=monitor_parent, daemon=True).start()

    try:
        mcp.run(transport="stdio", show_banner=False)
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)


if __name__ == "__main__":
    main()
