import re
import httpx
from urllib.parse import quote
from . import SourceExtractor, SourceType, BROWSER_UA


_PATTERN = re.compile(r"^https?://([a-z-]+)\.wikipedia\.org/wiki/(.+)$")


class WikipediaExtractor(SourceExtractor):
    def match(self, url: str) -> bool:
        return bool(_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.WIKIPEDIA

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _PATTERN.match(url)
        if not m:
            return None
        lang, title = m.groups()
        api_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title, safe='')}"
        headers = {"User-Agent": BROWSER_UA}
        try:
            resp = await client.get(api_url, headers=headers)
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        page_title = data.get("title", "")
        extract = data.get("extract", "")
        page_url = ""
        urls = data.get("content_urls", {})
        desktop = urls.get("desktop", {})
        if desktop:
            page_url = desktop.get("page", "")

        lines = [f"# {page_title}", ""]
        if extract:
            lines.append(extract)
            lines.append("")
        if page_url:
            lines.append("---")
            lines.append(f"*来源：[Wikipedia]({page_url})*")
        return "\n".join(lines)
