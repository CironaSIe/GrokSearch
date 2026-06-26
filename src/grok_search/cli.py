import asyncio
import argparse
from .runtime import run_search, get_session_sources, run_fetch


def main():
    parser = argparse.ArgumentParser(prog="grok-search")
    sub = parser.add_subparsers(dest="command")

    p_search = sub.add_parser("search", help="Web search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--platform", help="Platform filter")
    p_search.add_argument("--model", help="Model override")
    p_search.add_argument("--extra-sources", type=int, default=0, help="Extra Tavily sources")
    p_search.add_argument("--from-date", help="Start date YYYY-MM-DD")
    p_search.add_argument("--to-date", help="End date YYYY-MM-DD")
    p_search.add_argument("--allowed-domains", help="Comma-separated domain whitelist")
    p_search.add_argument("--max-search-results", type=int, default=0, help="Max results")
    p_search.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh"], help="Reasoning effort")
    p_search.add_argument("--content-only", action="store_true", help="Print only answer text")

    p_sources = sub.add_parser("sources", help="Get session sources")
    p_sources.add_argument("session_id", help="Session ID from search command")

    p_fetch = sub.add_parser("fetch", help="Fetch URL content")
    p_fetch.add_argument("url", help="Target URL")

    args = parser.parse_args()

    if args.command == "search":
        result = asyncio.run(run_search(
            args.query, args.platform or "", args.model or "",
            args.extra_sources, args.from_date or "", args.to_date or "",
            args.allowed_domains or "", args.max_search_results,
            args.reasoning_effort or "",
        ))
        if args.content_only:
            print(result["content"])
        else:
            print(f"Session: {result['session_id']}")
            print(f"Sources: {result['sources_count']}")
            print()
            print(result["content"])

    elif args.command == "sources":
        result = asyncio.run(get_session_sources(args.session_id))
        for s in result.get("sources", []):
            title = s.get("title", "") or s.get("url", "")
            url = s.get("url", "")
            print(f"- {title}")
            if url:
                print(f"  {url}")

    elif args.command == "fetch":
        content = asyncio.run(run_fetch(args.url))
        print(content)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
