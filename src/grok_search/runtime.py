import asyncio
from .config import config
from .sources import SourcesCache, new_session_id, merge_sources, split_answer_and_sources
from .providers.grok import GrokSearchProvider
from .utils import SEARCH_FRAMINGS, format_extra_sources
import random

_SOURCES_CACHE = SourcesCache(max_size=256)


async def _call_tavily_search(query: str, max_results: int = 6) -> list[dict] | None:
    import httpx
    api_url = config.tavily_api_url
    api_key = config.tavily_api_key
    if not api_key:
        return None
    endpoint = f"{api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"query": query, "max_results": max_results, "include_answer": False}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            return results if results else None
    except Exception:
        return None


async def run_search(
    query: str,
    platform: str = "",
    model: str = "",
    extra_sources: int = 0,
    from_date: str = "",
    to_date: str = "",
    allowed_domains: str = "",
    max_search_results: int = 0,
    reasoning_effort: str = "",
) -> dict:
    session_id = new_session_id()
    try:
        api_url = config.grok_api_url
        api_key = config.grok_api_key
    except ValueError as e:
        return {"session_id": session_id, "content": f"配置错误: {e}", "sources_count": 0}

    effective_model = model or config.grok_model
    provider = GrokSearchProvider(api_url, api_key, effective_model, reasoning_effort=reasoning_effort)

    content = await provider.search(
        random.choice(SEARCH_FRAMINGS).format(query=query),
        platform=platform,
        from_date=from_date,
        to_date=to_date,
        allowed_domains=allowed_domains,
        max_search_results=max_search_results,
    )

    answer, grok_sources = split_answer_and_sources(content)
    all_sources = grok_sources

    if extra_sources > 0 and config.tavily_api_key:
        tavily_results = await _call_tavily_search(query, extra_sources)
        extra = _extra_results_to_sources(tavily_results) if tavily_results else []
        all_sources = merge_sources(grok_sources, extra)

    await _SOURCES_CACHE.set(session_id, all_sources)
    return {"session_id": session_id, "content": answer, "sources_count": len(all_sources)}


def _extra_results_to_sources(tavily_results: list[dict]) -> list[dict]:
    sources = []
    for item in tavily_results:
        source = {
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "description": item.get("content", ""),
        }
        if source["url"]:
            sources.append(source)
    return sources


async def get_session_sources(session_id: str) -> dict:
    sources = await _SOURCES_CACHE.get(session_id)
    if sources is None:
        return {
            "session_id": session_id,
            "sources": [],
            "sources_count": 0,
            "error": "session_id_not_found_or_expired",
        }
    return {"session_id": session_id, "sources": sources, "sources_count": len(sources)}


async def run_fetch(url: str) -> str:
    import httpx
    from .utils import fetch_prompt

    try:
        api_url = config.grok_api_url
        api_key = config.grok_api_key
    except ValueError as e:
        return f"配置错误: {e}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.grok_model,
        "messages": [
            {"role": "system", "content": fetch_prompt},
            {"role": "user", "content": url + "\n获取该网页内容并返回其结构化Markdown格式"},
        ],
        "stream": True,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", f"{api_url.rstrip('/')}/chat/completions", headers=headers, json=payload) as response:
                response.raise_for_status()
                content = ""
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    if line in ("data: [DONE]", "data:[DONE]"):
                        continue
                    try:
                        import json
                        data = json.loads(line[5:].lstrip())
                        choices = data.get("choices", [])
                        if choices and len(choices) > 0:
                            delta = choices[0].get("delta", {})
                            if "content" in delta:
                                content += delta["content"]
                    except (json.JSONDecodeError, IndexError):
                        continue
                return content or "获取失败: 返回内容为空"
    except Exception as e:
        return f"获取失败: {e}"
