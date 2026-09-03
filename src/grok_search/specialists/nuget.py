import re
import httpx
from . import SourceExtractor, SourceType, BROWSER_UA


_PATTERN = re.compile(r"^https?://(?:www\.)?nuget\.org/packages/([^/]+)/?$")

_REGISTRATION_URL = "https://api.nuget.org/v3/registration5-semver1/{}/index.json"


class NugetExtractor(SourceExtractor):
    def match(self, url: str) -> bool:
        return bool(_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.NUGET

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _PATTERN.match(url)
        if not m:
            return None
        name = m.group(1)
        try:
            resp = await client.get(
                _REGISTRATION_URL.format(name.lower()),
                headers={"User-Agent": BROWSER_UA},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        candidates = _collect_entries(data)
        if not candidates:
            return None

        stables = [ce for ce in candidates if "-" not in (ce.get("version", "") or "")]
        prereleases = [ce for ce in candidates if "-" in (ce.get("version", "") or "")]

        stable = max(stables, key=lambda ce: _version_key(ce.get("version", ""))) if stables else None
        prerelease = max(prereleases, key=lambda ce: _version_key(ce.get("version", ""))) if prereleases else None
        primary = stable or prerelease
        if primary is None:
            return None

        title = primary.get("id") or name
        lines = [f"# {title}"]
        meta = []
        if primary.get("description"):
            meta.append(f"**简介：** {primary['description'][:120]}")
        if primary.get("authors"):
            meta.append(f"**作者：** {primary['authors']}")
        if primary.get("licenseExpression"):
            meta.append(f"**许可：** {primary['licenseExpression']}")
        if primary.get("projectUrl"):
            meta.append(f"**项目：** {primary['projectUrl']}")
        repo = primary.get("repository") or {}
        if isinstance(repo, dict) and repo.get("url"):
            meta.append(f"**仓库：** {repo['url']}")
        if meta:
            lines.append("> " + " | ".join(meta))
        lines.append("")

        if stable and prerelease:
            lines.append(f"最新稳定版：**{stable['version']}** ｜ 最新预览版：**{prerelease['version']}**")
        elif stable:
            lines.append(f"最新版本：**{stable['version']}**")
        elif prerelease:
            lines.append(f"最新版本（预览）：**{prerelease['version']}**")
        lines.append("")

        tags = primary.get("tags")
        if isinstance(tags, list) and tags:
            lines.append(f"**标签：** {', '.join(str(t) for t in tags[:12])}")
            lines.append("")

        dg = primary.get("dependencyGroups") or []
        deps = []
        for group in dg:
            for d in group.get("dependencies", []):
                deps.append((d.get("id", ""), d.get("range", ""), group.get("targetFramework", "")))
        if deps:
            lines.append("**依赖：**")
            for dep_id, dep_range, tf in deps[:10]:
                tf_str = f" [{tf}]" if tf else ""
                lines.append(f"- `{dep_id}` {dep_range}{tf_str}")
            lines.append("")

        desc = primary.get("description", "")
        if desc:
            lines.append(desc.strip()[:3000])
            lines.append("")

        lines.append("---")
        lines.append(f"*来源：[NuGet](https://www.nuget.org/packages/{name}/)*")
        return "\n".join(lines)


def _collect_entries(data: dict) -> list[dict]:
    pages = data.get("items") or data.get("pages") or []
    entries = []
    for page in pages:
        subs = page.get("items", [])
        if not subs:
            continue
        for sub in subs:
            ce = sub.get("catalogEntry")
            if isinstance(ce, dict) and ce.get("version") and ce.get("listed", True) is not False:
                entries.append(ce)
    return entries


def _version_key(v: str) -> tuple:
    parts = v.split("-", 1)
    nums = []
    for seg in parts[0].split("."):
        try:
            nums.append(int(seg))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums), 0 if len(parts) == 1 else 1, v