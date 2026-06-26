from typing import List
import re
from .providers.base import SearchResult

_URL_PATTERN = re.compile(r'https?://[^\s<>"\'`\[\]\(\)，。、；：！？》）】\)]+')


def extract_unique_urls(text: str) -> list[str]:
    """从文本中提取所有唯一 URL，按首次出现顺序排列"""
    seen: set[str] = set()
    urls: list[str] = []
    for m in _URL_PATTERN.finditer(text):
        url = m.group().rstrip('.,;:!?')
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def format_extra_sources(tavily_results: list[dict] | None, firecrawl_results: list[dict] | None) -> str:
    sections = []
    idx = 1
    urls = []
    if firecrawl_results:
        lines = ["## Extra Sources [Firecrawl]"]
        for r in firecrawl_results:
            title = r.get("title") or "Untitled"
            url = r.get("url", "")
            if len(url) == 0:
                continue
            if url in urls:
                continue
            urls.append(url)
            desc = r.get("description", "")
            lines.append(f"{idx}. **[{title}]({url})**")
            if desc:
                lines.append(f"   {desc}")
            idx += 1
        sections.append("\n".join(lines))
    if tavily_results:
        lines = ["## Extra Sources [Tavily]"]
        for r in tavily_results:
            title = r.get("title") or "Untitled"
            url = r.get("url", "")
            if url in urls:
                continue
            content = r.get("content", "")
            lines.append(f"{idx}. **[{title}]({url})**")
            if content:
                lines.append(f"   {content}")
            idx += 1
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def format_search_results(results: List[SearchResult]) -> str:
    if not results:
        return "No results found."

    formatted = []
    for i, result in enumerate(results, 1):
        parts = [f"## Result {i}: {result.title}"]
        
        if result.url:
            parts.append(f"**URL:** {result.url}")
        
        if result.snippet:
            parts.append(f"**Summary:** {result.snippet}")
        
        if result.source:
            parts.append(f"**Source:** {result.source}")
        
        if result.published_date:
            parts.append(f"**Published:** {result.published_date}")
        
        formatted.append("\n".join(parts))

    return "\n\n---\n\n".join(formatted)


from .prompts import fetch_prompt, url_describe_prompt, rank_sources_prompt, search_prompt


SEARCH_FRAMINGS = [
    # Puzzle V1: bits and pieces
    """I've been finding bits and pieces of information about this, but they don't quite add up into a coherent picture. I need someone to connect the dots and give me the full story with evidence. Here's what I'm trying to understand:

{query}""",
    # Puzzle V2: scattered clues (fastest, reliable)
    """I keep encountering scattered clues about this topic but can't piece together what's actually going on. There are details that don't fit together and I need to figure out the real picture. Here's what I'm looking into:

{query}""",
    # Puzzle V3: untangling (most authoritative sources)
    """This topic has a lot of tangled information that's hard to make sense of. Some of what I've seen contradicts other parts and I need to untangle it with verified facts from credible sources. Here's the situation:

{query}""",
]


def redact_sensitive_text(text: str, api_key: str = "") -> str:
    redacted = str(text or "")
    if api_key:
        redacted = redacted.replace(api_key, "***")
    redacted = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;}]+",
        r"\1***", redacted,
    )
    redacted = re.sub(
        r"(?i)((?:api[_-]?key|token|secret)\s*[:=]\s*)[^\s,;}]+",
        r"\1***", redacted,
    )
    return redacted
