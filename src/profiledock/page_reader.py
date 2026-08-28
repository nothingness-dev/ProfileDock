"""Lightweight HTML-to-Markdown and text extractor for terminal reading.

Extracts readable article content, headings, lists, tables, and links while
stripping navigation boilerplate, scripts, stylesheets, and tracking pixels.
Uses standard library html.parser without adding heavy external dependencies.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse


def _safe_text(value: str) -> str:
    return "".join(
        character
        for character in value
        if character in "\n\t" or (ord(character) >= 32 and not 127 <= ord(character) <= 159)
    )


class PageContentExtractor(HTMLParser):
    """Parses HTML DOM into structured clean text and Markdown."""

    _SKIP_TAGS = frozenset(
        {
            "script",
            "style",
            "noscript",
            "svg",
            "iframe",
            "object",
            "embed",
            "template",
        }
    )

    _BLOCK_TAGS = frozenset(
        {
            "p",
            "div",
            "article",
            "section",
            "header",
            "footer",
            "aside",
            "nav",
            "main",
            "blockquote",
            "pre",
            "figure",
            "figcaption",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "tr",
            "hr",
        }
    )

    def __init__(self, base_url: str = "") -> None:
        super().__init__()
        self.base_url = base_url
        self.title = ""
        self._tag_stack: list[str] = []
        self._in_title = False
        self._skip_depth = 0
        self._buffer: list[str] = []
        self._links: list[dict[str, str]] = []
        self._current_link_url = ""
        self._current_link_text: list[str] = []
        self._list_depth = 0
        self._list_item_index: list[int] = []
        self._in_pre = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        self._tag_stack.append(tag_lower)

        if tag_lower in self._SKIP_TAGS:
            self._skip_depth += 1
            return

        if self._skip_depth > 0:
            return

        attr_dict = {k.lower(): v for k, v in attrs if v is not None}

        if tag_lower == "title":
            self._in_title = True
        elif tag_lower == "pre":
            self._in_pre = True
            self._buffer.append("\n```\n")
        elif tag_lower == "br":
            self._buffer.append("\n")
        elif tag_lower == "hr":
            self._buffer.append("\n---\n")
        elif tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_lower[1])
            self._buffer.append(f"\n\n{'#' * level} ")
        elif tag_lower == "p":
            self._buffer.append("\n\n")
        elif tag_lower == "blockquote":
            self._buffer.append("\n\n> ")
        elif tag_lower == "ul":
            self._list_depth += 1
            self._list_item_index.append(0)
            self._buffer.append("\n")
        elif tag_lower == "ol":
            self._list_depth += 1
            self._list_item_index.append(1)
            self._buffer.append("\n")
        elif tag_lower == "li":
            indent = "  " * max(0, self._list_depth - 1)
            if self._list_item_index and self._list_item_index[-1] > 0:
                idx = self._list_item_index[-1]
                self._buffer.append(f"\n{indent}{idx}. ")
                self._list_item_index[-1] += 1
            else:
                self._buffer.append(f"\n{indent}* ")
        elif tag_lower == "a":
            href = _safe_text(attr_dict.get("href", "")).strip()
            if href and not href.lower().startswith(("javascript:", "mailto:", "#")):
                full_url = urljoin(self.base_url, href) if self.base_url else href
                parsed_url = urlparse(full_url)
                if parsed_url.scheme.lower() in ("http", "https"):
                    self._current_link_url = full_url
                    self._current_link_text = []
        elif tag_lower in ("strong", "b"):
            self._buffer.append("**")
        elif tag_lower in ("em", "i"):
            self._buffer.append("*")
        elif tag_lower == "code" and not self._in_pre:
            self._buffer.append("`")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self._tag_stack and self._tag_stack[-1] == tag_lower:
            self._tag_stack.pop()

        if tag_lower in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return

        if self._skip_depth > 0:
            return

        if tag_lower == "title":
            self._in_title = False
        elif tag_lower == "pre":
            self._in_pre = False
            self._buffer.append("\n```\n")
        elif tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._buffer.append("\n")
        elif tag_lower in ("ul", "ol"):
            if self._list_depth > 0:
                self._list_depth -= 1
            if self._list_item_index:
                self._list_item_index.pop()
            self._buffer.append("\n")
        elif tag_lower == "a" and self._current_link_url:
            text = "".join(self._current_link_text).strip()
            if text:
                link_num = len(self._links) + 1
                self._links.append({"index": str(link_num), "text": text, "url": self._current_link_url})
                self._buffer.append(f"[{link_num}]")
            self._current_link_url = ""
            self._current_link_text = []
        elif tag_lower in ("strong", "b"):
            self._buffer.append("**")
        elif tag_lower in ("em", "i"):
            self._buffer.append("*")
        elif tag_lower == "code" and not self._in_pre:
            self._buffer.append("`")
        elif tag_lower in self._BLOCK_TAGS:
            self._buffer.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return

        data = _safe_text(data)

        if self._in_title:
            self.title += data
            return

        if self._current_link_url:
            self._current_link_text.append(data)

        if self._in_pre:
            self._buffer.append(data)
        else:
            cleaned = re.sub(r"\s+", " ", data)
            self._buffer.append(cleaned)

    def get_markdown(self) -> str:
        raw_text = "".join(self._buffer)
        normalized = re.sub(r"\n{3,}", "\n\n", raw_text).strip()
        return normalized

    def get_links(self) -> list[dict[str, str]]:
        return list(self._links)


def extract_page_markdown(html: str, base_url: str = "") -> dict[str, Any]:
    """Parse HTML string and extract clean title, Markdown content, and link references."""
    if not html:
        return {"title": "", "content": "", "links": []}

    extractor = PageContentExtractor(base_url=base_url)
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:
        pass

    title = _safe_text(extractor.title).strip()
    if not title and base_url:
        parsed = urlparse(base_url)
        title = parsed.netloc or base_url

    content = extractor.get_markdown()
    links = extractor.get_links()

    if links:
        link_footer_lines = ["\n\n## References"]
        for item in links[:50]:
            link_footer_lines.append(f"[{item['index']}] {item['text']} — {item['url']}")
        content += "\n".join(link_footer_lines)

    return {
        "title": title,
        "content": content,
        "links": links,
    }
