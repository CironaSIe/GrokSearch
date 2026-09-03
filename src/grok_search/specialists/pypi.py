import re
import httpx
from . import SourceExtractor, SourceType, BROWSER_UA


_PATTERN = re.compile(r"^https?://pypi\.org/project/([^/]+)/?$")


class PypiExtractor(SourceExtractor):
    def match(self, url: str) -> bool:
        return bool(_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.PYPI

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _PATTERN.match(url)
        if not m:
            return None
        name = m.group(1)
        api_url = f"https://pypi.org/pypi/{name}/json"
        try:
            resp = await client.get(api_url, headers={"User-Agent": BROWSER_UA})
            if resp.status_code != 200:
                return None
            info = resp.json().get("info", {})
        except Exception:
            return None

        title = info.get("name", name)
        version = info.get("version", "")
        lines = [f"# {title}" + (f" {version}" if version else "")]
        meta = []
        if info.get("summary"):
            meta.append(f"**简介：** {info['summary']}")
        if info.get("author"):
            meta.append(f"**作者：** {info['author']}")
        if info.get("requires_python"):
            meta.append(f"**Python：** {info['requires_python']}")
        if info.get("license"):
            meta.append(f"**许可：** {info['license']}")
        if info.get("project_urls"):
            urls = info["project_urls"]
            if isinstance(urls, dict):
                github = urls.get("Source Code") or urls.get("Homepage") or urls.get("Repository")
                if github:
                    meta.append(f"**源码：** {github}")
        if meta:
            lines.append("> " + " | ".join(meta))
        lines.append("")

        desc = info.get("description", "")
        if desc:
            # 去掉 description 顶部重复的 name/version 头（部分包有）
            lines.append(desc.strip()[:6000])
            lines.append("")

        requires = info.get("requires_dist") or []
        if requires:
            lines.append("**依赖：**")
            for d in requires[:10]:
                lines.append(f"- {d}")
            lines.append("")

        lines.append("---")
        lines.append(f"*来源：[PyPI](https://pypi.org/project/{name}/)*")
        return "\n".join(lines)