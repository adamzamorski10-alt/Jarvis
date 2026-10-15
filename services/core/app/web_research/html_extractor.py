"""Bounded, non-executing HTML-to-text extraction for public research pages."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re


_MAX_HTML_CHARS = 1_000_000
_MAX_TEXT_CHARS = 100_000
_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "form", "nav", "footer"}
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "main", "ol", "p",
    "pre", "section", "table", "td", "th", "tr", "ul",
}


@dataclass(frozen=True)
class ExtractedPage:
    title: str
    text: str


class _TextParser(HTMLParser):
    def __init__(self, max_text_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self._max_text_chars = max_text_chars
        self._parts: list[str] = []
        self._length = 0
        self._skip_depth = 0
        self._title_depth = 0
        self._title_parts: list[str] = []
        self._title_length = 0

    def _append_text(self, data: str) -> None:
        if not data or self._skip_depth:
            return
        remaining = self._max_text_chars - self._length
        if remaining <= 0:
            return
        chunk = data[:remaining]
        self._parts.append(chunk)
        self._length += len(chunk)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): (value or "").strip().lower() for key, value in attrs}
        if tag in _SKIP_TAGS or "hidden" in attributes or attributes.get("aria-hidden") == "true":
            self._skip_depth += 1
            return
        if tag == "title":
            self._title_depth += 1
        if tag in _BLOCK_TAGS:
            self._append_text("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            # HTML can be malformed; decrement only on closing a skipped tag.
            if tag in _SKIP_TAGS:
                self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in _BLOCK_TAGS:
            self._append_text("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._title_depth and self._title_length < 512:
            chunk = data[: 512 - self._title_length]
            self._title_parts.append(chunk)
            self._title_length += len(chunk)
        self._append_text(data)

    def handle_entityref(self, name: str) -> None:
        # HTMLParser's convert_charrefs handles ordinary text entities.
        return

    def handle_charref(self, name: str) -> None:
        return

    def result(self) -> ExtractedPage:
        title = _normalize(" ".join(self._title_parts))[:512]
        text = _normalize(" ".join(self._parts))[: self._max_text_chars]
        return ExtractedPage(title=title, text=text)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def extract_html_text(
    html: str,
    *,
    max_html_chars: int = _MAX_HTML_CHARS,
    max_text_chars: int = _MAX_TEXT_CHARS,
) -> ExtractedPage:
    """Extract bounded text without executing scripts or interpreting page commands."""
    if not isinstance(html, str):
        raise TypeError("html must be a string")
    if max_html_chars < 1 or max_text_chars < 1:
        raise ValueError("extraction limits must be positive")
    if len(html) > max_html_chars:
        raise ValueError("html exceeds extraction input limit")

    parser = _TextParser(max_text_chars=max_text_chars)
    parser.feed(html)
    parser.close()
    return parser.result()


__all__ = ["ExtractedPage", "extract_html_text"]
