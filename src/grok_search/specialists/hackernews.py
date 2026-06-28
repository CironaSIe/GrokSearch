import re
import html
import httpx
from . import SourceExtractor, SourceType


_PATTERN = re.compile(r"^https://news\.ycombinator\.com/item\?id=(\d+)(?:&.*)?$")


class HackerNewsExtractor(SourceExtractor):
    def match(self, url: str) -> bool:
        return bool(_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.HACKER_NEWS

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _PATTERN.match(url)
        if not m:
            return None
        item_id = m.group(1)
        api_url = f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
        try:
            resp = await client.get(api_url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data:
                return None
        except Exception:
            return None

        title = data.get("title", "")
        by = data.get("by", "unknown")
        score = data.get("score", 0)
        descendants = data.get("descendants", 0)
        text = data.get("text", "")

        if text:
            text = html.unescape(text)
            text = text.replace("<p>", "\n\n")
            text = re.sub(r"<[^>]+>", "", text)
            text = text.strip()

        lines = [f"# {title}", ""]
        lines.append(f"**作者：** {by} | **分数：** {score} | **评论：** {descendants}")
        lines.append("")
        if text:
            lines.append(text)
            lines.append("")
        lines.append("---")
        lines.append(f"*来源：[Hacker News](https://news.ycombinator.com/item?id={item_id})*")
        return "\n".join(lines)
