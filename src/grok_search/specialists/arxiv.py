import re
import httpx
from . import SourceExtractor, SourceType, BROWSER_UA


_PATTERN = re.compile(r"^https?://arxiv\.org/(?:abs|pdf)/(\d+\.\d+)(?:v\d+)?$")


class ArxivExtractor(SourceExtractor):
    def match(self, url: str) -> bool:
        return bool(_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.ARXIV

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _PATTERN.match(url)
        if not m:
            return None
        paper_id = m.group(1)
        api_url = f"http://export.arxiv.org/api/query?id_list={paper_id}"
        headers = {"User-Agent": BROWSER_UA}
        try:
            resp = await client.get(api_url, headers=headers)
            if resp.status_code != 200:
                return None
            text = resp.text
        except Exception:
            return None

        entry = _extract_entry(text)
        if not entry:
            return None

        title = _extract_xml(entry, "title")
        if not title:
            return None
        authors = _extract_authors(entry)
        categories = _extract_xml(entry, "categories")
        published = _extract_xml(entry, "published")
        if published:
            published = published[:10]
        doi = _extract_xml(entry, "doi")
        summary = _extract_xml(entry, "summary")
        if summary:
            summary = re.sub(r"\s+", " ", summary).strip()

        abs_url = f"https://arxiv.org/abs/{paper_id}"
        lines = [f"# {title}", ""]
        if authors:
            lines.append(f"**作者：** {authors}")
        if categories:
            lines.append(f"**分类：** {categories}")
        if published:
            lines.append(f"**提交日期：** {published}")
        if doi:
            lines.append(f"**DOI：** {doi}")
        lines.append(f"**摘要链接：** {abs_url}")
        lines.append("")
        if summary:
            lines.append(summary)
            lines.append("")
        lines.append("---")
        lines.append(f"*更多信息：[arXiv.org]({abs_url})*")
        return "\n".join(lines)


def _extract_entry(text: str) -> str:
    m = re.search(r"<entry[^>]*>(.*?)</entry>", text, re.DOTALL)
    return m.group(1) if m else ""


def _extract_xml(text: str, tag: str) -> str:
    m = re.search(rf"<[^:]*:?\b{tag}[^>]*>(.*?)</[^:]*:?\b{tag}>", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def _extract_authors(text: str) -> str:
    names = re.findall(r"<[^:]*:?\bname>(.*?)</[^:]*:?\bname>", text, re.DOTALL)
    if not names:
        return ""
    return ", ".join(n.strip() for n in names)
