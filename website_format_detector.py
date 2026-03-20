#!/usr/bin/env python3
"""
评论输入格式检测器
用于在发帖前基于评论编辑器提示和历史评论 DOM 预判 Link_Format。
"""

import re
from typing import Dict, Iterable, Optional

import requests
from bs4 import BeautifulSoup, Tag


COMMENT_REGION_SELECTORS = (
    "#comments",
    ".comments",
    ".comments-area",
    ".commentlist",
    ".comment-list",
    ".responses",
    ".discussion",
    "#respond",
    ".comment-respond",
)

COMMENT_BLOCK_SELECTORS = (
    ".comment-content",
    ".comment-body",
    ".comment-text",
    ".comments__content",
    ".entry-comment",
    ".comment-entry",
    ".comment-container",
    ".commentContainer",
    ".commentList",
    ".media",
    "article.comment",
    "li.comment",
    "div.comment",
    "p.comment",
    '[class*="comments__item"]',
    '[id^="commentbody-"]',
)

COMMENT_FORM_HINTS = ("comment", "reply", "message", "textarea", "discussion")
NOISE_HINTS = ("sidebar", "widget", "recent", "related", "menu", "nav", "header", "footer")
GENERIC_LINK_TEXTS = {"reply", "edit", "permalink", "share", "report", "like"}

MARKDOWN_PATTERNS = (
    r"\bmarkdown\b",
    r"\[.+?\]\(.+?\)",
    r"supports markdown",
    r"markdown formatting",
    r"md-editor",
)

BBCODE_PATTERNS = (
    r"\bbbcode\b",
    r"\[url=.*?\].*?\[/url\]",
    r"\[b\].*?\[/b\]",
    r"\[i\].*?\[/i\]",
    r"bbcode is on",
)

HTML_PATTERNS = (
    r"html tags allowed",
    r"html is allowed",
    r"allowed html",
    r"allowed tags",
    r"use html",
)

RICH_EDITOR_HINTS = (
    "tinymce",
    "ckeditor",
    "froala",
    "summernote",
    "quill",
    "wysiwyg",
    "rich-editor",
    "html-editor",
)

WEBSITE_FIELD_HINTS = (
    'name="url"',
    "name='url'",
    'id="url"',
    "id='url'",
    'name="website"',
    "name='website'",
    'id="website"',
    "id='website'",
    "comment-form-url",
    "website",
    "web site",
)

HARD_SKIP_URL_PATTERNS = (
    "/profile",
    "/users/",
    "/user/",
    "/profiles/",
    "member.php",
    "memberlist.php",
    "action=profile",
    "searchuser",
    "/company/",
    "/employer/",
    "/groups/",
    "show_bug.cgi",
    "/support/discussions/",
    "/discussions/threads/",
    "/qa/user",
    "mastodon.social/@",
    "wiki/user:",
    "/sounds/",
)


def _normalize_url_text(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    return text.rstrip("/").strip()


def _looks_like_bare_url(text: str) -> bool:
    candidate = str(text or "").strip()
    return bool(
        re.fullmatch(r"https?://\S+", candidate)
        or re.fullmatch(r"www\.\S+", candidate)
        or re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/\S*)?", candidate)
    )


def _is_noise_node(node: Optional[Tag]) -> bool:
    current = node
    while current and isinstance(current, Tag):
        if current.name in {"aside", "nav", "header", "footer"}:
            return True
        attrs = " ".join(
            str(part)
            for part in ([current.get("id", "")] + current.get("class", []))
            if part
        ).lower()
        if any(hint in attrs for hint in NOISE_HINTS):
            return True
        current = current.parent
    return False


def _dedupe_nodes(nodes: Iterable[Tag]) -> list[Tag]:
    seen = set()
    deduped = []
    for node in nodes:
        marker = id(node)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(node)
    return deduped


def _should_skip_url(url: str) -> bool:
    normalized = str(url or "").strip().lower()
    return any(pattern in normalized for pattern in HARD_SKIP_URL_PATTERNS)


class WebsiteFormatDetector:
    """基于评论区证据预判 Link_Format。"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
                )
            }
        )

    def analyze_website(self, url: str) -> Dict:
        """分析网站评论能力，返回推荐格式与证据。"""
        if _should_skip_url(url):
            return {
                "url": url,
                "recommended_format": "unknown",
                "evidence_type": "skip_non_article_page",
                "confidence": 0.0,
                "supported_formats": ["unknown"],
            }
        try:
            print(f"正在分析评论格式: {url}")
            response = self.session.get(url, timeout=12)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            editor_result = self._detect_editor_format(soup)
            if editor_result:
                return {
                    "url": url,
                    "status_code": response.status_code,
                    "title": self._get_title(soup),
                    "recommended_format": editor_result["recommended_format"],
                    "evidence_type": editor_result["evidence_type"],
                    "confidence": editor_result["confidence"],
                    "supported_formats": [editor_result["recommended_format"]],
                }

            history_result = self._detect_historical_comment_format(soup)
            if history_result:
                return {
                    "url": url,
                    "status_code": response.status_code,
                    "title": self._get_title(soup),
                    "recommended_format": history_result["recommended_format"],
                    "evidence_type": history_result["evidence_type"],
                    "confidence": history_result["confidence"],
                    "supported_formats": [history_result["recommended_format"]],
                }

            fallback_result = self._detect_form_capability_fallback(soup)
            if fallback_result:
                return {
                    "url": url,
                    "status_code": response.status_code,
                    "title": self._get_title(soup),
                    "recommended_format": fallback_result["recommended_format"],
                    "evidence_type": fallback_result["evidence_type"],
                    "confidence": fallback_result["confidence"],
                    "supported_formats": [fallback_result["recommended_format"]],
                }

            return {
                "url": url,
                "status_code": response.status_code,
                "title": self._get_title(soup),
                "recommended_format": "unknown",
                "evidence_type": "unknown",
                "confidence": 0.0,
                "supported_formats": ["unknown"],
            }
        except Exception as exc:
            return {
                "url": url,
                "status": "failed",
                "error": str(exc),
                "recommended_format": "unknown",
                "evidence_type": "unknown",
                "confidence": 0.0,
                "supported_formats": ["unknown"],
            }

    def _get_title(self, soup: BeautifulSoup) -> str:
        title_tag = soup.find("title")
        return title_tag.get_text(strip=True) if title_tag else "No title"

    def _detect_editor_format(self, soup: BeautifulSoup) -> Optional[Dict]:
        combined_text = self._collect_editor_context(soup)
        combined_lower = combined_text.lower()

        if any(re.search(pattern, combined_lower, re.IGNORECASE) for pattern in MARKDOWN_PATTERNS):
            return {
                "recommended_format": "markdown",
                "evidence_type": "editor_markdown",
                "confidence": 0.98,
            }

        if any(re.search(pattern, combined_lower, re.IGNORECASE) for pattern in BBCODE_PATTERNS):
            return {
                "recommended_format": "bbcode",
                "evidence_type": "editor_bbcode",
                "confidence": 0.98,
            }

        if any(re.search(pattern, combined_lower, re.IGNORECASE) for pattern in HTML_PATTERNS):
            return {
                "recommended_format": "html",
                "evidence_type": "editor_html",
                "confidence": 0.95,
            }

        if soup.select('[contenteditable="true"]'):
            return {
                "recommended_format": "html",
                "evidence_type": "editor_html",
                "confidence": 0.9,
            }

        if any(hint in combined_lower for hint in RICH_EDITOR_HINTS):
            return {
                "recommended_format": "html",
                "evidence_type": "editor_html",
                "confidence": 0.88,
            }

        return None

    def _collect_editor_context(self, soup: BeautifulSoup) -> str:
        parts = []
        forms = soup.find_all("form")
        for form in forms:
            form_html = str(form)
            form_text = form.get_text(" ", strip=True)
            if not any(hint in form_html.lower() or hint in form_text.lower() for hint in COMMENT_FORM_HINTS):
                continue
            parts.append(form_html[:5000])
            parts.append(form_text[:1500])
            parent = form.parent if isinstance(form.parent, Tag) else None
            if parent:
                parts.append(parent.get_text(" ", strip=True)[:1500])

        for editable in soup.select('[contenteditable="true"]'):
            if _is_noise_node(editable):
                continue
            parent = editable.parent if isinstance(editable.parent, Tag) else None
            parts.append(str(editable)[:2000])
            if parent:
                parts.append(parent.get_text(" ", strip=True)[:1500])

        return " ".join(part for part in parts if part)

    def _comment_regions(self, soup: BeautifulSoup) -> list[Tag]:
        regions = []
        for selector in COMMENT_REGION_SELECTORS:
            regions.extend(soup.select(selector))
        if regions:
            return _dedupe_nodes([node for node in regions if not _is_noise_node(node)])

        fallback_forms = []
        for form in soup.find_all("form"):
            text = f"{form.get_text(' ', strip=True)} {str(form)}".lower()
            if any(hint in text for hint in COMMENT_FORM_HINTS):
                fallback_forms.append(form.parent if isinstance(form.parent, Tag) else form)
        return _dedupe_nodes([node for node in fallback_forms if isinstance(node, Tag) and not _is_noise_node(node)])

    def _comment_blocks(self, soup: BeautifulSoup) -> list[Tag]:
        blocks = []
        for region in self._comment_regions(soup):
            for selector in COMMENT_BLOCK_SELECTORS:
                blocks.extend(region.select(selector))

        if not blocks:
            for selector in COMMENT_BLOCK_SELECTORS:
                blocks.extend(soup.select(selector))

        filtered = []
        for block in _dedupe_nodes(blocks):
            if _is_noise_node(block):
                continue
            text = block.get_text(" ", strip=True)
            if len(text) < 20:
                continue
            filtered.append(block)
        return filtered[:80]

    def _comment_forms(self, soup: BeautifulSoup) -> list[Tag]:
        forms = []
        for form in soup.find_all("form"):
            form_html = str(form).lower()
            form_text = form.get_text(" ", strip=True).lower()
            if any(hint in form_html or hint in form_text for hint in COMMENT_FORM_HINTS):
                forms.append(form)
        return forms

    def _has_website_field(self, form: Tag) -> bool:
        form_html = str(form).lower()
        form_text = form.get_text(" ", strip=True).lower()
        return any(hint in form_html or hint in form_text for hint in WEBSITE_FIELD_HINTS)

    def _has_comment_list_signal(self, soup: BeautifulSoup) -> bool:
        return bool(
            soup.select(
                ".comment-list, .commentlist, .commentList, .comments-list, "
                ".comment-entry, .comment-container, .commentContainer, "
                ".comments-count-wrapper, [id^='comment-'], article.comment, li.comment"
            )
        )

    def _detect_historical_comment_format(self, soup: BeautifulSoup) -> Optional[Dict]:
        saw_autolink = False
        for block in self._comment_blocks(soup):
            anchors = block.find_all("a")
            for anchor in anchors:
                href = str(anchor.get("href", "") or "").strip()
                text = anchor.get_text(" ", strip=True)
                if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
                    continue
                if text.lower() in GENERIC_LINK_TEXTS:
                    continue

                normalized_href = _normalize_url_text(href)
                normalized_text = _normalize_url_text(text)

                if _looks_like_bare_url(text) and (
                    normalized_text == normalized_href
                    or normalized_text in normalized_href
                    or normalized_href in normalized_text
                ):
                    saw_autolink = True
                    continue

                if text and normalized_text and normalized_text != normalized_href:
                    return {
                        "recommended_format": "html",
                        "evidence_type": "historical_anchor_text_link",
                        "confidence": 0.82,
                    }

        if saw_autolink:
            return {
                "recommended_format": "plain_text_autolink",
                "evidence_type": "historical_autolink",
                "confidence": 0.74,
            }

        return None

    def _detect_form_capability_fallback(self, soup: BeautifulSoup) -> Optional[Dict]:
        comment_forms = self._comment_forms(soup)
        if not comment_forms:
            return None

        has_website_field = any(self._has_website_field(form) for form in comment_forms)
        has_comment_list = self._has_comment_list_signal(soup)

        if has_website_field and has_comment_list:
            return {
                "recommended_format": "html",
                "evidence_type": "comment_form_website_and_history",
                "confidence": 0.56,
            }

        return None

    def batch_analyze(self, urls: list[str]) -> dict[str, Dict]:
        results = {}
        for url in urls:
            results[url] = self.analyze_website(url)
        return results


def main():
    detector = WebsiteFormatDetector()
    test_urls = [
        "https://www.learnalanguage.com/blog/italian-greetings-how-are-you-in-italian/",
        "https://www.beautythroughimperfection.com/reindeer-donuts/",
    ]

    for url in test_urls:
        print(f"\n{'=' * 50}")
        result = detector.analyze_website(url)
        print(result)


if __name__ == "__main__":
    main()
