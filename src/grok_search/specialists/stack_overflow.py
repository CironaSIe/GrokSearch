import re
import html as html_mod
import httpx
from . import SourceExtractor, SourceType, BROWSER_UA


_PATTERN = re.compile(
    r"^https?://stackoverflow\.com/questions/(\d+)(?:/[^?]*)?(?:[?].*)?$"
)


class StackOverflowExtractor(SourceExtractor):
    def match(self, url: str) -> bool:
        return bool(_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.STACK_OVERFLOW

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _PATTERN.match(url)
        if not m:
            return None
        qid = m.group(1)
        headers = {"User-Agent": BROWSER_UA}

        q_url = f"https://api.stackexchange.com/2.3/questions/{qid}?site=stackoverflow&filter=withbody"
        try:
            resp = await client.get(q_url, headers=headers)
            if resp.status_code != 200:
                return None
            items = resp.json().get("items", [])
            if not items:
                return None
            q = items[0]
        except Exception:
            return None

        lines = [f"# {q.get('title', '')}"]
        meta = [f"**分数：** {q.get('score', 0)}"]
        meta.append(f"**浏览：** {q.get('view_count', 0)}")
        tags = q.get("tags", [])
        if tags:
            meta.append("**标签：** " + ", ".join(tags))
        lines.append("> " + " | ".join(meta))
        lines.append("")
        body = q.get("body", "")
        if body:
            lines.append(_strip_html(body)[:4000])
            lines.append("")

        # 最高票回答
        a_url = f"https://api.stackexchange.com/2.3/questions/{qid}/answers?site=stackoverflow&filter=withbody&sort=votes&pagesize=3"
        try:
            resp = await client.get(a_url, headers=headers)
            if resp.status_code == 200:
                answers = resp.json().get("items", [])
                if answers:
                    lines.append("---")
                    lines.append("**回答：**\n")
                    for a in answers:
                        accepted = " ✅" if a.get("is_accepted") else ""
                        lines.append(f"> **分数 {a.get('score', 0)}{accepted}**（{a.get('owner', {}).get('display_name', 'unknown')}）")
                        ans_body = _strip_html(a.get("body", ""))
                        lines.append(ans_body[:2500])
                        lines.append("")
        except Exception:
            pass

        lines.append("---")
        lines.append(f"*来源：[Stack Overflow 问题 {qid}](https://stackoverflow.com/questions/{qid})*")
        return "\n".join(lines)


def _strip_html(text: str) -> str:
    # 保留代码块
    text = text.replace("<pre><code>", "\n```\n").replace("</code></pre>", "\n```\n")
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    return text.strip()