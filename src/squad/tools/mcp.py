"""MCP client loader + plain fetch. `browse` = Playwright MCP + fetch, scout's toolset.
User servers from squad.yaml `mcp_servers` bind by name in a role's tools list."""

import asyncio
import urllib.request

from langchain_core.tools import StructuredTool, tool

BROWSE_SERVERS = {
    "playwright": {
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest", "--headless"],
        "transport": "stdio",
    }
}


_UA = "Mozilla/5.0 (compatible; squad-scout/1.0)"  # bare urllib UA gets 403'd a lot


@tool
def fetch(url: str, max_chars: int = 12_000) -> str:
    """Fetch a URL and return its main content as clean markdown — boilerplate,
    scripts, nav and styles stripped, links and lists kept. Far fewer tokens
    than raw HTML. For JS-rendered pages or interaction, use the browser tools."""
    import trafilatura  # lazy: heavy import, only when scout actually browses

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read(2_000_000)  # ceiling guards memory; extraction shrinks it anyway
    except Exception as e:  # agent must see why, never crash the run
        return f"fetch failed: {e}"
    html = raw.decode("utf-8", "replace")
    md = trafilatura.extract(html, output_format="markdown", include_links=True,
                             include_tables=True, include_comments=False)
    # ponytail: extraction returns None on non-article pages (SERPs, tiny/blocked);
    # degrade to raw rather than empty. A structured `search` tool is the real fix.
    md = md or html
    return md[:max_chars] + ("\n[truncated]" if len(md) > max_chars else "")


@tool
def search(query: str, max_results: int = 8) -> str:
    """Web search via DuckDuckGo. Returns a compact list of results
    (title, url, snippet) — far cheaper than fetching a search page's HTML.
    Follow up with `fetch` on the URLs worth reading."""
    from ddgs import DDGS  # lazy: heavy import, only when scout actually searches

    try:
        results = DDGS().text(query, max_results=max_results)
    except Exception as e:  # agent must see why, never crash the run
        return f"search failed: {e}"
    if not results:
        return "no results"
    return "\n".join(
        f"{i}. {r['title']}\n   {r['href']}\n   {r.get('body', '')}"
        for i, r in enumerate(results, 1)
    )


def _sync_wrap(t: StructuredTool) -> StructuredTool:
    """MCP adapter tools are async-only; agents here run sync. Wrap each in a
    fresh event loop — tool calls execute in worker threads with no loop."""
    if t.func is not None:
        return t
    return StructuredTool(
        name=t.name, description=t.description, args_schema=t.args_schema,
        func=lambda _t=t, **kwargs: asyncio.run(_t.ainvoke(kwargs)),
    )


def load_mcp_tools(servers: dict) -> list:
    if not servers:
        return []
    from langchain_mcp_adapters.client import MultiServerMCPClient  # lazy: heavy

    client = MultiServerMCPClient(servers)
    return [_sync_wrap(t) for t in asyncio.run(client.get_tools())]


def tools_for_role(role_tools: list[str], mcp_servers: dict) -> list:
    """Resolve a role's browse/render/MCP tool names to bound tool objects."""
    out = []
    if "browse" in role_tools:   # cheap: structured search + markdown fetch, no browser
        out += [search, fetch]
    if "render" in role_tools:   # heavy, opt-in: Playwright MCP for JS-rendered pages
        out += load_mcp_tools(BROWSE_SERVERS)
    named = {n: c for n, c in mcp_servers.items() if n in role_tools}
    if named:
        out += load_mcp_tools(named)
    return out
