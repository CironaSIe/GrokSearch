import re
import httpx
from . import SourceExtractor, SourceType, BROWSER_UA


_PATTERN = re.compile(r"^https?://crates\.io/crates/([^/]+)/?$")


class CratesExtractor(SourceExtractor):
    def match(self, url: str) -> bool:
        return bool(_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.CRATES

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _PATTERN.match(url)
        if not m:
            return None
        name = m.group(1)
        api_url = f"https://crates.io/api/v1/crates/{name}"
        try:
            resp = await client.get(
                api_url,
                headers={"User-Agent": BROWSER_UA},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        cr = data.get("crate", {})
        title = cr.get("name", name)
        versions = data.get("versions", [])

        lines = [f"# {title}"]
        meta = []
        if cr.get("description"):
            meta.append(f"**简介：** {cr['description']}")
        if cr.get("max_version"):
            meta.append(f"**最新：** {cr['max_version']}")
        meta.append(f"**总下载：** {_fmt_count(cr.get('downloads', 0))}")
        if cr.get("recent_downloads") is not None:
            meta.append(f"**近期下载：** {_fmt_count(cr['recent_downloads'])}")
        if cr.get("documentation"):
            meta.append(f"**文档：** {cr['documentation']}")
        if cr.get("repository"):
            meta.append(f"**仓库：** {cr['repository']}")
        if cr.get("homepage"):
            meta.append(f"**主页：** {cr['homepage']}")
        if meta:
            lines.append("> " + " | ".join(meta))
        lines.append("")

        if versions and isinstance(versions[0], dict):
            v = versions[0]
            vmeta = []
            if v.get("num"):
                vmeta.append(f"**版本：** {v['num']}")
            if v.get("license"):
                vmeta.append(f"**许可：** {v['license']}")
            if v.get("rust_version"):
                vmeta.append(f"**Rust：** {v['rust_version']}")
            if v.get("crate_size"):
                vmeta.append(f"**大小：** {_fmt_size(v['crate_size'])}")
            if vmeta:
                lines.append("> " + " | ".join(vmeta))
                lines.append("")

        # readme（crates.io readme 端点不稳定，跳过；文档经仓库/文档链接直达）
        lines.append("---")
        lines.append(f"*来源：[crates.io](https://crates.io/crates/{name})*")
        return "\n".join(lines)


def _fmt_count(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "N/A"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_size(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "N/A"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f}MB"
    if n >= 1_024:
        return f"{n / 1_024:.1f}KB"
    return f"{n}B"