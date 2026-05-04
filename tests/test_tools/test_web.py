"""Tests for web tools."""

import ipaddress
import urllib.parse

from code_agent.tools.web import WebFetchTool, WebSearchTool


def test_web_fetch_blocks_localhost() -> None:
    """Web fetch must not allow localhost SSRF targets."""
    tool = WebFetchTool()
    safe, reason = tool._is_safe_url(urllib.parse.urlparse("http://localhost:8000"))

    assert safe is False
    assert "local" in reason.lower()


def test_web_fetch_blocks_private_ip() -> None:
    """Web fetch must not allow RFC1918/private targets."""
    tool = WebFetchTool()
    safe, reason = tool._is_safe_url(urllib.parse.urlparse("http://192.168.1.10"))

    assert safe is False
    assert "不安全" in reason


def test_web_fetch_allows_public_ip() -> None:
    """Public IP targets should pass URL safety validation."""
    tool = WebFetchTool()
    safe, reason = tool._is_safe_url(urllib.parse.urlparse("https://8.8.8.8"))

    assert safe is True
    assert reason == ""


def test_web_fetch_allows_reserved_proxy_ip() -> None:
    """Reserved proxy-style IPs should not break normal hosted environments."""
    tool = WebFetchTool()
    safe, reason = tool._is_safe_ip(ipaddress.ip_address("198.18.0.6"), "example.com")

    assert safe is True
    assert reason == ""


def test_web_search_parses_duckduckgo_redirect_results() -> None:
    """DuckDuckGo HTML parsing should survive class ordering and redirect URLs."""
    html = """
    <div class="result results_links web-result">
      <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&amp;rut=x">
        Example Docs
      </a>
      <a class="result__snippet" href="#">Useful documentation snippet.</a>
    </div>
    """

    results = WebSearchTool()._parse_duckduckgo_results(html, 3)

    assert results == [
        {
            "title": "Example Docs",
            "url": "https://example.com/docs",
            "snippet": "Useful documentation snippet.",
        }
    ]
