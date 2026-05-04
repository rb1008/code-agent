"""Web 相关工具 - 网页获取和网络搜索

参考主流编码代理的 WebFetch 和 WebSearch 工作流实现。
"""

import asyncio
from html.parser import HTMLParser
import ipaddress
import json
import socket
import urllib.parse

import aiohttp

from code_agent.tools.base import BaseTool, ToolPermission, ToolResult


class WebFetchTool(BaseTool):
    """获取指定 URL 的网页内容"""
    
    name = "web_fetch"
    aliases = ["fetch_url", "url"]
    search_hint = "抓取 网页 URL 文档 API docs fetch"
    description = (
        "按 URL 抓取网页内容，返回文本或 Markdown。"
        "适合读取文档、文章或其他网页内容。"
    )
    parameters = {
        "url": {
            "type": "string",
            "description": "要抓取的 URL",
            "required": True,
        },
        "max_length": {
            "type": "integer",
            "description": "最多返回字符数，默认 10000",
            "required": False,
        },
        "format": {
            "type": "string",
            "description": "输出格式：text 或 markdown，默认 text",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )
    
    def __init__(self, timeout: int = 30):
        super().__init__()
        self.timeout = timeout
    
    async def execute(
        self,
        url: str,
        max_length: int = 10000,
        format: str = "text",
    ) -> ToolResult:
        """获取网页内容
        
        Args:
            url: 目标 URL
            max_length: 最大返回字符数
            format: 输出格式
            
        Returns:
            网页内容
        """
        try:
            # 验证 URL
            parsed = urllib.parse.urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return ToolResult.fail(f"URL 无效：{url}")
            
            # 限制允许的协议
            if parsed.scheme not in ("http", "https"):
                return ToolResult.fail(f"协议不支持：{parsed.scheme}")

            safe, reason = self._is_safe_url(parsed)
            if not safe:
                return ToolResult.fail(reason)
            
            current_url = url
            redirects = 0

            # 异步获取网页；手动跟随并校验重定向，避免 SSRF 绕过。
            async with aiohttp.ClientSession() as session:
                while True:
                    async with session.get(
                        current_url,
                        timeout=aiohttp.ClientTimeout(total=self.timeout),
                        allow_redirects=False,
                        headers={"User-Agent": "Code-Agent/1.0 (AI Coding Assistant)"},
                    ) as response:
                        if 300 <= response.status < 400:
                            location = response.headers.get("Location", "")
                            if not location:
                                return ToolResult.fail(f"重定向缺少 Location：HTTP {response.status}")
                            redirects += 1
                            if redirects > 5:
                                return ToolResult.fail("重定向次数过多，已停止抓取。")
                            next_url = urllib.parse.urljoin(current_url, location)
                            next_parsed = urllib.parse.urlparse(next_url)
                            if next_parsed.scheme not in ("http", "https"):
                                return ToolResult.fail(f"重定向协议不支持：{next_parsed.scheme}")
                            safe, reason = self._is_safe_url(next_parsed)
                            if not safe:
                                return ToolResult.fail(f"重定向目标不安全：{reason}")
                            current_url = next_url
                            continue
                        if response.status != 200:
                            return ToolResult.fail(f"HTTP {response.status}: {response.reason}")

                        content_type = response.headers.get("Content-Type", "")

                        # 处理 HTML
                        if "text/html" in content_type:
                            html = await response.text()
                            if format == "markdown":
                                text = self._html_to_markdown(html)
                            else:
                                text = self._html_to_text(html)
                        # 处理 JSON
                        elif "application/json" in content_type:
                            data = await response.json()
                            text = json.dumps(data, indent=2, ensure_ascii=False)
                        # 其他文本内容
                        elif "text/" in content_type:
                            text = await response.text()
                        else:
                            # 二进制内容，返回基本信息
                            return ToolResult.ok(
                                f"二进制内容（{content_type}）。"
                                f"大小：{response.headers.get('Content-Length', 'unknown')} bytes"
                            )

                        # 截断内容
                        if len(text) > max_length:
                            text = text[:max_length] + f"\n\n... [已截断，总长度：{len(text)} 字符]"

                        return ToolResult.ok(
                            text,
                            url=current_url,
                            content_type=content_type,
                            length=len(text),
                        )
                    
        except asyncio.TimeoutError:
            return ToolResult.fail(f"抓取超时：{url}")
        except Exception as e:
            return ToolResult.fail(f"抓取失败：{url}: {str(e)}")

    def _is_safe_url(self, parsed: urllib.parse.ParseResult) -> tuple[bool, str]:
        """Reject local, private, and metadata endpoints before fetching."""
        hostname = parsed.hostname
        if not hostname:
            return False, "URL 必须包含主机名"

        host = hostname.strip().lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False, f"已拦截本地主机名：{hostname}"

        try:
            ip = ipaddress.ip_address(host)
            return self._is_safe_ip(ip, hostname)
        except ValueError:
            pass

        try:
            addresses = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            return False, f"无法解析主机 {hostname}: {str(e)}"

        for *_, sockaddr in addresses:
            ip = ipaddress.ip_address(sockaddr[0])
            safe, reason = self._is_safe_ip(ip, hostname)
            if not safe:
                return safe, reason

        return True, ""

    def _is_safe_ip(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address, host: str) -> tuple[bool, str]:
        """Return whether an IP address is safe for outbound web fetch."""
        # Some managed networks resolve public hosts through RFC 2544 benchmark
        # proxy addresses. Treat only that narrow range as allowed; still block
        # loopback, link-local, RFC1918, and metadata-style targets.
        if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("198.18.0.0/15"):
            return True, ""
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"已拦截不安全的网络目标 {host} ({ip})"

        return True, ""
    
    def _html_to_text(self, html: str) -> str:
        """将 HTML 转换为纯文本
        
        Args:
            html: HTML 内容
            
        Returns:
            纯文本
        """
        try:
            from html.parser import HTMLParser
            
            class TextExtractor(HTMLParser):
                def __init__(self) -> None:
                    super().__init__()
                    self.text: list[str] = []
                    self.skip_tags: set[str] = {"script", "style", "nav", "footer", "header"}
                    self._skip: int = 0
                
                def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                    if tag in self.skip_tags:
                        self._skip += 1
                    elif tag in ("br", "p", "div", "h1", "h2", "h3", "h4", "li"):
                        self.text.append("\n")
                
                def handle_endtag(self, tag: str) -> None:
                    if tag in self.skip_tags:
                        self._skip -= 1
                    elif tag in ("p", "div", "h1", "h2", "h3", "h4", "li"):
                        self.text.append("\n")
                
                def handle_data(self, data: str) -> None:
                    if self._skip == 0:
                        self.text.append(data)
            
            extractor = TextExtractor()
            extractor.feed(html)
            text = "".join(extractor.text)
            
            # 清理空白
            lines = [line.strip() for line in text.splitlines()]
            text = "\n".join(line for line in lines if line)
            
            return text
            
        except Exception:
            # 如果解析失败，返回原始 HTML
            return html
    
    def _html_to_markdown(self, html: str) -> str:
        """将 HTML 转换为 Markdown
        
        Args:
            html: HTML 内容
            
        Returns:
            Markdown 文本
        """
        # 简化版转换，实际可以使用 html2text 库
        text = self._html_to_text(html)
        return text


class WebSearchTool(BaseTool):
    """网络搜索工具
    
    使用 DuckDuckGo 或 Bing API 进行搜索。
    """
    
    name = "web_search"
    aliases = ["search_web"]
    search_hint = "搜索 查询 互联网 网页 网络 文档 API docs"
    description = (
        "搜索网络信息，返回标题、URL 和摘要。"
        "适合查找文档、方案或当前信息。"
    )
    parameters = {
        "query": {
            "type": "string",
            "description": "搜索关键词",
            "required": True,
        },
        "n_results": {
            "type": "integer",
            "description": "返回结果数量，默认 5",
            "required": False,
        },
    }
    permission = ToolPermission(
        require_confirmation=False, allowed_in_auto_mode=True, destructive=False
    )
    
    def __init__(self, timeout: int = 30):
        super().__init__()
        self.timeout = timeout
    
    async def execute(
        self,
        query: str,
        n_results: int = 5,
    ) -> ToolResult:
        """执行网络搜索
        
        Args:
            query: 搜索关键词
            n_results: 返回结果数量
            
        Returns:
            搜索结果
        """
        try:
            n_results = max(1, min(int(n_results), 10))
            # 使用 DuckDuckGo HTML 端点（无需 API key）
            results = await self._search_duckduckgo(query, n_results)
            
            if not results:
                return ToolResult.ok(f"未找到搜索结果：{query}")
            
            # 格式化输出
            output = f"搜索结果：{query}\n"
            output += "=" * 50 + "\n\n"
            
            for i, result in enumerate(results, 1):
                output += f"{i}. {result['title']}\n"
                output += f"   URL: {result['url']}\n"
                output += f"   {result['snippet']}\n\n"
            
            return ToolResult.ok(
                output,
                query=query,
                results_count=len(results),
                results=results,
            )
            
        except Exception as e:
            return ToolResult.fail(f"搜索失败：{str(e)}")
    
    async def _search_duckduckgo(
        self,
        query: str,
        n_results: int,
    ) -> list[dict[str, str]]:
        """使用 DuckDuckGo 搜索
        
        Args:
            query: 搜索关键词
            n_results: 结果数量
            
        Returns:
            搜索结果列表
        """
        # DuckDuckGo HTML 搜索 URL
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                search_url,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
                },
            ) as response:
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}")
                
                html = await response.text()
                if "captcha" in html.lower() and "result__a" not in html:
                    raise Exception("搜索服务要求验证码，暂时无法返回结果。")
                return self._parse_duckduckgo_results(html, n_results)
    
    def _parse_duckduckgo_results(
        self,
        html: str,
        n_results: int,
    ) -> list[dict[str, str]]:
        """解析 DuckDuckGo 搜索结果
        
        Args:
            html: 搜索结果 HTML
            n_results: 最大结果数
            
        Returns:
            解析后的结果列表
        """
        parser = _DuckDuckGoHTMLParser()
        parser.feed(html)
        parser.close()
        results: list[dict[str, str]] = []
        for result in parser.results:
            if len(results) >= n_results:
                break
            url = self._decode_duckduckgo_url(result.get("url", ""))
            if not url or "duckduckgo.com/y.js" in url:
                continue
            results.append({
                "title": result.get("title", "").strip(),
                "url": url,
                "snippet": result.get("snippet", "").strip(),
            })
        return results

    def _decode_duckduckgo_url(self, url: str) -> str:
        """Decode DuckDuckGo redirect URLs into the real target."""
        url = self._clean_html(url)
        if url.startswith("//"):
            url = "https:" + url
        parsed = urllib.parse.urlparse(url)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            query = urllib.parse.parse_qs(parsed.query)
            target = query.get("uddg", [""])[0]
            return urllib.parse.unquote(target) if target else url
        return url
    
    def _clean_html(self, html: str) -> str:
        """清理 HTML 标签
        
        Args:
            html: 包含 HTML 标签的字符串
            
        Returns:
            纯文本
        """
        import re
        
        # 移除 HTML 标签
        text = re.sub(r"<[^>]+>", "", html)
        # 解码 HTML 实体
        import html as html_module
        text = html_module.unescape(text)
        # 清理空白
        text = " ".join(text.split())
        return text


class _DuckDuckGoHTMLParser(HTMLParser):
    """Small tolerant parser for DuckDuckGo's HTML search page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._finish_current()
            self._current = {"url": attrs_map.get("href", ""), "title": "", "snippet": ""}
            self._capture = "title"
            self._buffer = []
        elif self._current is not None and "result__snippet" in classes:
            self._capture = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture == "title" and self._current is not None:
            self._current["title"] = " ".join("".join(self._buffer).split())
            self._capture = None
            self._buffer = []
        elif tag in {"a", "div"} and self._capture == "snippet" and self._current is not None:
            self._current["snippet"] = " ".join("".join(self._buffer).split())
            self._capture = None
            self._buffer = []
            self._finish_current()

    def close(self) -> None:
        self._finish_current()
        super().close()

    def _finish_current(self) -> None:
        if self._current and self._current.get("title") and self._current.get("url"):
            if self._current not in self.results:
                self.results.append(self._current)
        self._current = None
