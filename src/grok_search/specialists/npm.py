import re
import httpx
from . import SourceExtractor, SourceType, BROWSER_UA


_PATTERN = re.compile(r"^https?://(?:www\.)?npmjs\.com/package/([^/]+)/?$")


class NpmExtractor(SourceExtractor):
    def match(self, url: str) -> bool:
        return bool(_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.NPM

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _PATTERN.match(url)
        if not m:
            return None
        name = m.group(1)
        api_url = f"https://registry.npmjs.org/{name}"
        try:
            resp = await client.get(api_url, headers={"User-Agent": BROWSER_UA})
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        pkg_name = data.get("name", name)
        dist_tags = data.get("dist-tags", {})
        latest = dist_tags.get("latest", "")
        versions = data.get("versions", {})
        lv = versions.get(latest, {})
        readme = data.get("readme", "")

        lines = [f"# {pkg_name}"]
        meta = []
        if lv.get("version"):
            meta.append(f"**版本：** {lv['version']}")
        if lv.get("description"):
            meta.append(f"**简介：** {lv['description']}")
        if lv.get("license"):
            meta.append(f"**许可：** {lv['license']}")
        author = lv.get("author") or {}
        if isinstance(author, dict) and author.get("name"):
            meta.append(f"**作者：** {author['name']}")
        if lv.get("homepage"):
            meta.append(f"**主页：** {lv['homepage']}")
        repo = lv.get("repository") or {}
        if isinstance(repo, dict) and repo.get("url"):
            meta.append(f"**仓库：** {repo['url']}")
        if meta:
            lines.append("> " + " | ".join(meta))
        lines.append("")

        deps = lv.get("dependencies") or {}
        if deps:
            lines.append("**依赖：**")
            for k, v in list(deps.items())[:10]:
                lines.append(f"- `{k}` {v}")
            lines.append("")

        if readme:
            lines.append(readme.strip()[:6000])
            lines.append("")

        lines.append("---")
        lines.append(f"*来源：[npm](https://www.npmjs.com/package/{name})*")
        return "\n".join(lines)