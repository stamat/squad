"""MCP/browse toolset — fetch tool, per-role tool resolution. Offline (loader stubbed)."""

import http.server
import threading

import pytest
from langchain_core.tools import tool

from squad.tools import mcp


@pytest.fixture
def local_http(tmp_path):
    (tmp_path / "page.html").write_text("<html>LangGraph 1.2.9 release notes</html>")
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp_path), **kw)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_fetch_returns_page_text(local_http):
    out = mcp.fetch.invoke({"url": f"{local_http}/page.html"})
    assert "LangGraph 1.2.9" in out


ARTICLE = """<html><head><title>Doc</title>
<style>.nav{color:red}</style><script>tracker();</script></head>
<body><nav>home about contact</nav>
<article><h1>Widgets Explained</h1>
<p>Widgets are small reusable components that solve a common problem in user
interfaces. This paragraph is deliberately long enough that the content
extractor treats it as the main body of the article and keeps it while dropping
the navigation and scripts around it. See
<a href="https://docs.example.com/widgets">the docs</a> for more details.</p>
</article><footer>copyright 2026</footer></body></html>"""


def test_fetch_extracts_markdown_not_raw_html(local_http, tmp_path):
    (tmp_path / "article.html").write_text(ARTICLE)
    out = mcp.fetch.invoke({"url": f"{local_http}/article.html"})
    assert "tracker()" not in out and "color:red" not in out   # scripts/styles stripped
    assert "Widgets are small reusable" in out                 # main content kept
    assert "https://docs.example.com/widgets" in out           # links kept


def test_fetch_truncates(local_http, tmp_path):
    (tmp_path / "big.txt").write_text("x" * 100_000)
    out = mcp.fetch.invoke({"url": f"{local_http}/big.txt", "max_chars": 500})
    assert len(out) < 1000


def test_fetch_error_is_agent_visible():
    out = mcp.fetch.invoke({"url": "http://127.0.0.1:1/nope"})
    assert "fetch failed" in out.lower()  # returns, never raises


def test_fetch_fallback_never_returns_raw_html(local_http, tmp_path):
    # non-article page (extractor returns None) must degrade to tag-stripped
    # text, not raw HTML — tag soup was the original token leak
    (tmp_path / "serp.html").write_text(
        "<html><head><script>t()</script></head><body>"
        + "".join(f'<div class="r"><a href="/{i}">result {i}</a></div>' for i in range(40))
        + "</body></html>")
    out = mcp.fetch.invoke({"url": f"{local_http}/serp.html"})
    assert "result 5" in out          # text survives
    assert "<div" not in out and "<script" not in out  # markup does not


def test_fetch_max_chars_is_clamped(local_http, tmp_path):
    # the model controls max_chars in the tool call; a huge value must not
    # blow the context cap
    (tmp_path / "huge.html").write_text("<article><p>" + "word " * 40_000 + "</p></article>")
    out = mcp.fetch.invoke({"url": f"{local_http}/huge.html", "max_chars": 999_999})
    assert len(out) <= 12_100  # hard ceiling + truncation marker


def test_search_formats_results(monkeypatch):
    class FakeDDGS:
        def text(self, q, max_results=8):
            return [{"title": "Widgets", "href": "https://a.com", "body": "about widgets"}]

    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    out = mcp.search.invoke({"query": "widgets"})
    assert "Widgets" in out and "https://a.com" in out and "about widgets" in out


def test_search_error_is_agent_visible(monkeypatch):
    class BoomDDGS:
        def text(self, *a, **k):
            raise RuntimeError("boom")

    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", BoomDDGS)
    out = mcp.search.invoke({"query": "x"})
    assert "search failed" in out.lower()  # returns, never raises


@tool
def fake_browser(action: str) -> str:
    """stub"""
    return "ok"


def test_tools_for_role_browse_render_and_named_servers(monkeypatch):
    calls = []

    def fake_loader(servers):
        calls.append(servers)
        return [fake_browser]

    monkeypatch.setattr(mcp, "load_mcp_tools", fake_loader)
    user_servers = {"github": {"command": "x"}, "linear": {"command": "y"}}

    # browse alone = cheap: search + fetch only, no Playwright, no loader call
    got = mcp.tools_for_role(["browse"], user_servers)
    assert {t.name for t in got} == {"search", "fetch"}
    assert calls == []

    # render opts into Playwright; named server loads its own MCP
    got = mcp.tools_for_role(["browse", "render", "github"], user_servers)
    assert {"search", "fetch", "fake_browser"} <= {t.name for t in got}
    assert mcp.BROWSE_SERVERS in calls                # render → playwright MCP
    assert {"github": {"command": "x"}} in calls      # only the named server, not linear
    assert {"linear": {"command": "y"}} not in calls

    assert mcp.tools_for_role(["shell", "fs"], user_servers) == []  # nothing to bind
