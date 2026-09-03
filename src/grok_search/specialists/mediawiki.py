import re
import httpx
from urllib.parse import quote, unquote
from . import SourceExtractor, SourceType, BROWSER_UA


# MediaWiki 通用 URL 路径约定（排除 Wikipedia 家族，后者走专用提取器）
# 匹配 {origin}/wiki/Title 或 {origin}/title/Title
_WIKI_PATH_PATTERN = re.compile(r"^https?://([^/]+)/(?:wiki|title)/(.+)$")
# 匹配 {origin}/index.php/*Title*/（mgewiki.moe 等用的旧式路径）
_INDEX_PHP_PATH_PATTERN = re.compile(r"^https?://([^/]+)/index\.php/(.+)$")
# 匹配 {origin}/ 根 + query title=... 形式
_INDEX_PHP_QUERY_PATTERN = re.compile(r"^https?://([^/]+)/index\.php\?title=(.+?)(?:&.*)?$")

WIKI_HOST_BLOCKLIST = re.compile(r"(^|\.)wikipedia\.org$|(^|\.)wikimedia\.org$")


def _is_wikipedia_family(host: str) -> bool:
    return bool(WIKI_HOST_BLOCKLIST.search(host) or host == "wikipedia.org")


def _extract_title(url: str) -> tuple[str, str] | None:
    """返回 (origin, title)。"""
    m = _WIKI_PATH_PATTERN.match(url)
    if m:
        host, title = m.groups()
        return f"https://{host}", title
    m = _INDEX_PHP_PATH_PATTERN.match(url)
    if m:
        host, title = m.groups()
        return f"https://{host}", title.replace("_", " ")
    m = _INDEX_PHP_QUERY_PATTERN.match(url)
    if m:
        host, title = m.groups()
        return f"https://{host}", unquote(title.replace("_", " "))
    return None


class MediaWikiExtractor(SourceExtractor):
    def match(self, url: str) -> bool:
        entry = _extract_title(url)
        if entry is None:
            return False
        host = entry[0].removeprefix("https://")
        if _is_wikipedia_family(host):
            return False
        return True

    def kind(self) -> SourceType:
        return SourceType.MEDIAWIKI

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        entry = _extract_title(url)
        if entry is None:
            return None
        origin, title = entry
        headers = {"User-Agent": BROWSER_UA}

        api_url = await _find_api_url(client, origin, headers)
        if api_url is None:
            return None

        # 取渲染 HTML（MediaWiki 核心 API，不依赖 TextExtracts 扩展）
        params = {
            "action": "parse",
            "page": title,
            "prop": "text",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        }
        try:
            resp = await client.get(api_url, params=params, headers=headers)
            if resp.status_code != 200:
                return None
            html = resp.json().get("parse", {}).get("text", "")
        except Exception:
            return None
        if not html:
            return None

        try:
            from trafilatura import extract
            md = extract(html, output_format="markdown", include_comments=False, fast=True)
        except ImportError:
            md = None
        if md and md.strip():
            content = md.strip()
        else:
            # trafilatura 失败时退回轻量 HTML 清洗
            content = _strip_html(html)

        page_title = title.replace("_", " ")
        lines = [f"# {page_title}", ""]
        lines.append(content[:8000])
        lines.append("")
        lines.append("---")
        lines.append(f"*来源：[{origin.split('//')[-1]}]({origin}/wiki/{quote(title.replace(' ', '_'), safe='')})*")
        return "\n".join(lines)


async def _find_api_url(client: httpx.AsyncClient, origin: str, headers: dict) -> str | None:
    candidates = [f"{origin}/api.php", f"{origin}/w/api.php"]
    for api in candidates:
        try:
            r = await client.get(
                api,
                params={"action": "query", "meta": "siteinfo", "format": "json", "formatversion": "2"},
                headers=headers,
            )
            if r.status_code == 200:
                data = r.json()
                gen = data.get("query", {}).get("general", {}).get("generator", "").lower()
                if gen.startswith("mediawiki"):
                    return api
        except Exception:
            continue
    return None


def _strip_html(text: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    import html
    return html.unescape(text).strip()