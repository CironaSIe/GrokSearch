import re
import httpx
from . import SourceExtractor, SourceType


_ISSUE_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)$")
_PR_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)$")


class GithubIssueExtractor(SourceExtractor):
    def __init__(self, token: str | None = None):
        self._token = token

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def match(self, url: str) -> bool:
        return bool(_ISSUE_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.GITHUB_ISSUE

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _ISSUE_PATTERN.match(url)
        if not m:
            return None
        owner, repo, _, num = m.groups()
        api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{num}"
        try:
            resp = await client.get(api_url, headers=self._headers())
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None
        return await _render_github_item(data, client, self._headers())


class GithubPrExtractor(SourceExtractor):
    def __init__(self, token: str | None = None):
        self._token = token

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def match(self, url: str) -> bool:
        return bool(_PR_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.GITHUB_PULL

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _PR_PATTERN.match(url)
        if not m:
            return None
        owner, repo, _, num = m.groups()
        api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{num}"
        try:
            resp = await client.get(api_url, headers=self._headers())
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None
        return await _render_github_item(data, client, self._headers())


async def _render_github_item(data: dict, client: httpx.AsyncClient, headers: dict) -> str | None:
    lines = [
        f"# #{data['number']} {data['title']}",
        f"> **状态：** {data['state']} | **作者：** {data['user']['login']} | **创建：** {data.get('created_at', '')[:10]}",
    ]
    if data.get("labels"):
        labels = ", ".join(l["name"] for l in data["labels"])
        lines.append(f"> **标签：** {labels}")
    lines.append("")
    body = data.get("body", "")
    if body:
        lines.append(body)
        lines.append("")

    comments_url = data.get("comments_url", "")
    if comments_url:
        try:
            resp = await client.get(comments_url, headers=headers)
            if resp.status_code == 200:
                comments = resp.json()
                if comments:
                    lines.append("---\n")
                    lines.append("**评论：**\n")
                    for c in comments[:5]:
                        lines.append(f"> **@{c['user']['login']}** 于 {c.get('created_at', '')[:10]}：")
                        lines.append(f"> {c.get('body', '')}")
                        lines.append("")
        except Exception:
            pass

    return "\n".join(lines)
