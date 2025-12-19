"""Browser tool for headless web browsing.

This module provides a Playwright-based browser tool that allows the AI agent
to fetch and extract content from web pages to help answer complex queries.
"""

import asyncio
import logging
import time
import re
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Safety: Blocked URL patterns (security, privacy, internal networks)
BLOCKED_URL_PATTERNS = [
    r"^file://",
    r"^localhost",
    r"^127\.",
    r"^192\.168\.",
    r"^10\.",
    r"^172\.(1[6-9]|2[0-9]|3[01])\.",
    r"\.local$",
    r"\.internal$",
]

# Tool schema for LLM function calling
BROWSER_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browse_web",
        "description": "Fetch and read content from a web page URL. Use this tool when you need current information from the internet that is not available in the knowledge base. Good for: checking documentation, fetching current data, reading articles.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch (must start with http:// or https://)"
                },
                "extract_type": {
                    "type": "string",
                    "enum": ["text", "markdown", "links"],
                    "description": "Type of content to extract: 'text' for plain text, 'markdown' for formatted content, 'links' for page links"
                }
            },
            "required": ["url"]
        }
    }
}


@dataclass
class BrowserToolResult:
    """Result from a browser tool operation."""
    success: bool
    url: str
    title: str = ""
    content: str = ""
    content_length: int = 0
    duration_ms: float = 0.0
    error: Optional[str] = None
    links: List[Dict[str, str]] = field(default_factory=list)


class BrowserTool:
    """Headless browser tool using Playwright for web content extraction."""
    
    def __init__(
        self,
        timeout_ms: int = 30000,
        max_content_length: int = 15000,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ):
        self.timeout_ms = timeout_ms
        self.max_content_length = max_content_length
        self.user_agent = user_agent
        self._cache: Dict[str, BrowserToolResult] = {}
        self._cache_ttl = 300  # 5 minutes cache
        self._cache_times: Dict[str, float] = {}
    
    def _is_url_allowed(self, url: str) -> tuple[bool, str]:
        """Check if URL is allowed to be fetched."""
        try:
            parsed = urlparse(url)
            
            # Must have valid scheme
            if parsed.scheme not in ("http", "https"):
                return False, "URL must use http:// or https://"
            
            # Check against blocked patterns
            full_url = url.lower()
            hostname = parsed.hostname or ""
            
            for pattern in BLOCKED_URL_PATTERNS:
                if re.search(pattern, hostname) or re.search(pattern, full_url):
                    return False, f"URL blocked for security: matches pattern {pattern}"
            
            return True, ""
        except Exception as e:
            return False, f"Invalid URL: {str(e)}"
    
    def _get_cached(self, url: str) -> Optional[BrowserToolResult]:
        """Get cached result if still valid."""
        if url in self._cache:
            cache_time = self._cache_times.get(url, 0)
            if time.time() - cache_time < self._cache_ttl:
                logger.debug(f"Browser cache hit for: {url}")
                return self._cache[url]
            else:
                # Expired, remove from cache
                del self._cache[url]
                del self._cache_times[url]
        return None
    
    def _set_cached(self, url: str, result: BrowserToolResult):
        """Cache a result."""
        self._cache[url] = result
        self._cache_times[url] = time.time()
        
        # Limit cache size
        if len(self._cache) > 50:
            oldest_url = min(self._cache_times, key=self._cache_times.get)
            del self._cache[oldest_url]
            del self._cache_times[oldest_url]
    
    async def fetch_page(
        self,
        url: str,
        extract_type: str = "text",
        wait_for_selector: Optional[str] = None
    ) -> BrowserToolResult:
        """
        Fetch a web page and extract content.
        
        Args:
            url: URL to fetch
            extract_type: Type of extraction ('text', 'markdown', 'links')
            wait_for_selector: Optional CSS selector to wait for
            
        Returns:
            BrowserToolResult with extracted content
        """
        start_time = time.time()
        
        # Check URL safety
        allowed, reason = self._is_url_allowed(url)
        if not allowed:
            return BrowserToolResult(
                success=False,
                url=url,
                error=reason,
                duration_ms=(time.time() - start_time) * 1000
            )
        
        # Check cache
        cached = self._get_cached(url)
        if cached:
            cached.duration_ms = 0.1  # Indicate cache hit
            return cached
        
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return BrowserToolResult(
                success=False,
                url=url,
                error="Playwright is not installed. Please install with: pip install playwright && playwright install chromium",
                duration_ms=(time.time() - start_time) * 1000
            )
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ]
                )
                
                try:
                    context = await browser.new_context(
                        user_agent=self.user_agent,
                        viewport={"width": 1280, "height": 720}
                    )
                    page = await context.new_page()
                    
                    # Navigate to URL
                    response = await page.goto(
                        url,
                        timeout=self.timeout_ms,
                        wait_until="domcontentloaded"
                    )
                    
                    if response and response.status >= 400:
                        return BrowserToolResult(
                            success=False,
                            url=url,
                            error=f"HTTP error: {response.status}",
                            duration_ms=(time.time() - start_time) * 1000
                        )
                    
                    # Wait for optional selector
                    if wait_for_selector:
                        try:
                            await page.wait_for_selector(wait_for_selector, timeout=5000)
                        except Exception:
                            logger.debug(f"Selector {wait_for_selector} not found, continuing")
                    
                    # Wait a bit for dynamic content
                    await asyncio.sleep(0.5)
                    
                    # Get title
                    title = await page.title()
                    
                    # Extract content based on type
                    if extract_type == "links":
                        links = await page.evaluate('''() => {
                            return Array.from(document.querySelectorAll('a[href]'))
                                .slice(0, 50)
                                .map(a => ({
                                    text: a.innerText.trim().slice(0, 100),
                                    href: a.href
                                }))
                                .filter(l => l.text && l.href.startsWith('http'));
                        }''')
                        content = f"Found {len(links)} links on the page."
                        
                        result = BrowserToolResult(
                            success=True,
                            url=url,
                            title=title,
                            content=content,
                            content_length=len(content),
                            duration_ms=(time.time() - start_time) * 1000,
                            links=links
                        )
                    
                    elif extract_type == "markdown":
                        # Extract with some structure preserved
                        content = await page.evaluate('''() => {
                            // Remove unwanted elements
                            const remove = document.querySelectorAll(
                                'script, style, nav, footer, header, aside, iframe, noscript, svg, [role="navigation"], [role="banner"], [role="contentinfo"], .nav, .navbar, .footer, .header, .sidebar, .ad, .advertisement'
                            );
                            remove.forEach(el => el.remove());
                            
                            // Get main content area or body
                            const main = document.querySelector('main, article, [role="main"], .content, .post, .article') || document.body;
                            
                            // Convert to simple markdown-like format
                            let result = '';
                            const walk = (node) => {
                                if (node.nodeType === 3) {
                                    result += node.textContent;
                                } else if (node.nodeType === 1) {
                                    const tag = node.tagName.toLowerCase();
                                    if (tag === 'h1') result += '\\n# ';
                                    else if (tag === 'h2') result += '\\n## ';
                                    else if (tag === 'h3') result += '\\n### ';
                                    else if (tag === 'p') result += '\\n\\n';
                                    else if (tag === 'br') result += '\\n';
                                    else if (tag === 'li') result += '\\n- ';
                                    
                                    for (const child of node.childNodes) {
                                        walk(child);
                                    }
                                    
                                    if (['p', 'div', 'section', 'article'].includes(tag)) {
                                        result += '\\n';
                                    }
                                }
                            };
                            walk(main);
                            return result.replace(/\\n{3,}/g, '\\n\\n').trim();
                        }''')
                        
                        # Truncate if needed
                        if len(content) > self.max_content_length:
                            content = content[:self.max_content_length] + "\n\n[Content truncated...]"
                        
                        result = BrowserToolResult(
                            success=True,
                            url=url,
                            title=title,
                            content=content,
                            content_length=len(content),
                            duration_ms=(time.time() - start_time) * 1000
                        )
                    
                    else:  # text
                        content = await page.evaluate('''() => {
                            // Remove unwanted elements
                            const remove = document.querySelectorAll(
                                'script, style, nav, footer, header, aside, iframe, noscript, svg, [role="navigation"], [role="banner"], [role="contentinfo"], .nav, .navbar, .footer, .header, .sidebar, .ad, .advertisement'
                            );
                            remove.forEach(el => el.remove());
                            
                            // Get main content area or body
                            const main = document.querySelector('main, article, [role="main"], .content, .post, .article') || document.body;
                            return main.innerText;
                        }''')
                        
                        # Clean up whitespace
                        content = re.sub(r'\n{3,}', '\n\n', content)
                        content = re.sub(r'[ \t]{2,}', ' ', content)
                        content = content.strip()
                        
                        # Truncate if needed
                        if len(content) > self.max_content_length:
                            content = content[:self.max_content_length] + "\n\n[Content truncated...]"
                        
                        result = BrowserToolResult(
                            success=True,
                            url=url,
                            title=title,
                            content=content,
                            content_length=len(content),
                            duration_ms=(time.time() - start_time) * 1000
                        )
                    
                    # Cache the result
                    self._set_cached(url, result)
                    
                    logger.info(f"Browser fetched {url}: {len(result.content)} chars in {result.duration_ms:.0f}ms")
                    return result
                    
                finally:
                    await browser.close()
                    
        except asyncio.TimeoutError:
            return BrowserToolResult(
                success=False,
                url=url,
                error=f"Timeout: Page took longer than {self.timeout_ms}ms to load",
                duration_ms=(time.time() - start_time) * 1000
            )
        except Exception as e:
            logger.error(f"Browser fetch error for {url}: {e}")
            return BrowserToolResult(
                success=False,
                url=url,
                error=f"Failed to fetch page: {str(e)}",
                duration_ms=(time.time() - start_time) * 1000
            )


# Singleton instance for reuse
_browser_tool: Optional[BrowserTool] = None


def get_browser_tool() -> BrowserTool:
    """Get or create the browser tool singleton."""
    global _browser_tool
    if _browser_tool is None:
        _browser_tool = BrowserTool()
    return _browser_tool


async def browse_url(url: str, extract_type: str = "text") -> BrowserToolResult:
    """
    Convenience function to browse a URL.
    
    Args:
        url: URL to fetch
        extract_type: 'text', 'markdown', or 'links'
        
    Returns:
        BrowserToolResult
    """
    tool = get_browser_tool()
    return await tool.fetch_page(url, extract_type)
