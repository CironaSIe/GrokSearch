import base64
import re
import httpx
from . import SourceExtractor, SourceType


_ISSUE_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)$")
_PR_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)$")
_RAW_PATTERN = re.compile(r"^https://raw\.githubusercontent\.com/")
_BLOB_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$")
_GIST_PATTERN = re.compile(r"^https://gist\.github\.com/([^/]+)/(\w+)$")
_RELEASE_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/releases(?:/tag/(.+)|/latest)?$")
_README_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/?$")


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


class GithubRawExtractor(SourceExtractor):
    def __init__(self, token: str | None = None):
        self._token = token

    def match(self, url: str) -> bool:
        return bool(_RAW_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.GITHUB_RAW

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        try:
            headers = {}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return None
            ct = resp.headers.get("content-type", "").lower()
            if ct.startswith("text/") or "json" in ct or "javascript" in ct:
                return resp.text
            return None
        except Exception:
            return None


class GithubBlobExtractor(SourceExtractor):
    def __init__(self, token: str | None = None):
        self._token = token

    def match(self, url: str) -> bool:
        return bool(_BLOB_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.GITHUB_BLOB

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _BLOB_PATTERN.match(url)
        if not m:
            return None
        owner, repo, ref_path = m.groups()
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref_path}"
        try:
            headers = {}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            resp = await client.get(raw_url, headers=headers)
            if resp.status_code != 200:
                return None
            ct = resp.headers.get("content-type", "").lower()
            if ct.startswith("text/") or "json" in ct:
                return resp.text
            return None
        except Exception:
            return None


class GithubGistExtractor(SourceExtractor):
    def __init__(self, token: str | None = None):
        self._token = token

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def match(self, url: str) -> bool:
        return bool(_GIST_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.GITHUB_GIST

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _GIST_PATTERN.match(url)
        if not m:
            return None
        _, gist_id = m.groups()
        api_url = f"https://api.github.com/gists/{gist_id}"
        try:
            resp = await client.get(api_url, headers=self._headers())
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        desc = data.get("description", "") or ""
        files = data.get("files", {})
        lines = [f"# Gist: {gist_id}"]
        if desc:
            lines.append(f"> {desc}")
        lines.append("")
        for fname, finfo in files.items():
            content = finfo.get("content", "")
            lang = finfo.get("language", "")
            lines.append(f"## `{fname}`" + (f" ({lang})" if lang else ""))
            lines.append("")
            if content:
                lines.append(content)
                lines.append("")
        return "\n".join(lines)


class GithubReleaseExtractor(SourceExtractor):
    def __init__(self, token: str | None = None):
        self._token = token

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def match(self, url: str) -> bool:
        return bool(_RELEASE_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.GITHUB_RELEASE

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _RELEASE_PATTERN.match(url)
        if not m:
            return None
        owner, repo, tag = m.groups()
        if tag:
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
        else:
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        try:
            resp = await client.get(api_url, headers=self._headers())
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        tag_name = data.get("tag_name", "")
        name = data.get("name", "") or ""
        body = data.get("body", "") or ""
        author = data.get("author", {}).get("login", "unknown")
        published = (data.get("published_at") or "")[:10]
        prerelease = data.get("prerelease", False)

        lines = [f"# {name} ({tag_name})"]
        lines.append(f"> **作者：** {author} | **发布：** {published}" + (" ⚠️ **预发布**" if prerelease else ""))
        lines.append("")
        if body:
            lines.append(body)
            lines.append("")
        lines.append("---")
        lines.append(f"*来源：[GitHub Releases]({url})*")
        return "\n".join(lines)


class GithubReadmeExtractor(SourceExtractor):
    def __init__(self, token: str | None = None):
        self._token = token

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github.v3.raw"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def match(self, url: str) -> bool:
        return bool(_README_PATTERN.match(url))

    def kind(self) -> SourceType:
        return SourceType.GITHUB_README

    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        m = _README_PATTERN.match(url)
        if not m:
            return None
        owner, repo = m.groups()
        api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
        try:
            resp = await client.get(api_url, headers=self._headers())
            if resp.status_code != 200:
                return None
            return resp.text
        except Exception:
            return None


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