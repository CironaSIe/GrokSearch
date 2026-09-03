from enum import StrEnum
from abc import ABC, abstractmethod
from urllib.parse import urlparse
import httpx


class SourceType(StrEnum):
    GITHUB_ISSUE = "github_issue"
    GITHUB_PULL = "github_pull"
    GITHUB_RAW = "github_raw"
    GITHUB_BLOB = "github_blob"
    GITHUB_GIST = "github_gist"
    GITHUB_RELEASE = "github_release"
    GITHUB_README = "github_readme"
    HUGGINGFACE = "huggingface"
    PYPI = "pypi"
    NPM = "npm"
    STACK_OVERFLOW = "stack_overflow"
    CRATES = "crates"
    NUGET = "nuget"
    ARXIV = "arxiv"
    WIKIPEDIA = "wikipedia"
    HACKER_NEWS = "hacker_news"
    MEDIAWIKI = "mediawiki"


BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class SourceExtractor(ABC):
    @abstractmethod
    def match(self, url: str) -> bool:
        ...

    @abstractmethod
    def kind(self) -> SourceType:
        ...

    @abstractmethod
    async def fetch_render(self, client: httpx.AsyncClient, url: str) -> str | None:
        ...


class SourceRouter:
    def __init__(self, github_token: str | None = None,
                 hf_token: str | None = None,
                 http_proxy: str | None = None,
                 https_proxy: str | None = None):
        from .github import (
            GithubIssueExtractor, GithubPrExtractor,
            GithubRawExtractor, GithubBlobExtractor,
            GithubGistExtractor, GithubReleaseExtractor,
            GithubReadmeExtractor,
        )
        from .arxiv import ArxivExtractor
        from .wikipedia import WikipediaExtractor
        from .hackernews import HackerNewsExtractor
        from .huggingface import HuggingFaceExtractor
        from .pypi import PypiExtractor
        from .npm import NpmExtractor
        from .stack_overflow import StackOverflowExtractor
        from .crates import CratesExtractor
        from .nuget import NugetExtractor
        from .mediawiki import MediaWikiExtractor

        self._extractors: list[SourceExtractor] = [
            GithubIssueExtractor(github_token),
            GithubPrExtractor(github_token),
            GithubRawExtractor(github_token),
            GithubBlobExtractor(github_token),
            GithubGistExtractor(github_token),
            GithubReleaseExtractor(github_token),
            GithubReadmeExtractor(github_token),
            HuggingFaceExtractor(hf_token),
            PypiExtractor(),
            NpmExtractor(),
            StackOverflowExtractor(),
            CratesExtractor(),
            NugetExtractor(),
            ArxivExtractor(),
            WikipediaExtractor(),
            MediaWikiExtractor(),
            HackerNewsExtractor(),
        ]
        self._http_proxy = http_proxy
        self._https_proxy = https_proxy

    def _clean_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None
        cleaned = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            cleaned += f"?{parsed.query}"
        return cleaned

    def resolve(self, url: str) -> SourceExtractor | None:
        cleaned = self._clean_url(url)
        if cleaned is None:
            return None
        for ex in self._extractors:
            if ex.match(cleaned):
                return ex
        return None

    async def fetch(self, url: str, timeout: int | float) -> str | None:
        cleaned = self._clean_url(url)
        if cleaned is None:
            return None
        extractor = self.resolve(url)
        if extractor is None:
            return None
        try:
            proxy = None
            if self._http_proxy:
                proxy = self._http_proxy
            if self._https_proxy:
                proxy = self._https_proxy

            async with httpx.AsyncClient(
                timeout=timeout,
                proxy=proxy,
                follow_redirects=True,
            ) as client:
                content = await extractor.fetch_render(client, cleaned)
                if content and content.strip():
                    return content
        except Exception:
            pass
        return None
