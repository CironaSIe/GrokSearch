import httpx
from typing import List
from .base import BaseSearchProvider, SearchResult


class ExaSearchProvider(BaseSearchProvider):
    def __init__(self, api_key: str, base_url: str = "https://api.exa.ai"):
        super().__init__(base_url, api_key)
        self.base_url = base_url.rstrip("/")

    def get_provider_name(self) -> str:
        return "Exa"

    async def search(self, query: str, max_results: int = 5,
                     include_domains: List[str] | None = None,
                     exclude_domains: List[str] | None = None,
                     category: str | None = None) -> List[SearchResult]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: dict = {"query": query, "num_results": max_results}
        if include_domains:
            body["include_domains"] = include_domains
        if exclude_domains:
            body["exclude_domains"] = exclude_domains
        if category:
            body["category"] = category

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/search",
                headers=headers, json=body,
            )
            response.raise_for_status()
            data = response.json()

        results = []
        for item in (data.get("results", []) or []):
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("text", "") or item.get("snippet", ""),
                source="exa",
                published_date=item.get("publishedDate", ""),
            ))
        return results

    async def find_similar(self, url: str, num_results: int = 5) -> List[SearchResult]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {"url": url, "num_results": num_results}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/findSimilar",
                headers=headers, json=body,
            )
            response.raise_for_status()
            data = response.json()
        results = []
        for item in (data.get("results", []) or []):
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("text", "") or item.get("snippet", ""),
                source="exa",
                published_date=item.get("publishedDate", ""),
            ))
        return results
