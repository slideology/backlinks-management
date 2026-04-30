#!/usr/bin/env python3
"""
评论输入格式检测器
用于在发帖前基于评论编辑器提示和历史评论 DOM 预判 Link_Format。
"""

import re
from urllib.parse import urlparse
from typing import Dict, Iterable, Optional

import requests
from bs4 import BeautifulSoup, Tag
from playwright.sync_api import Page


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

RUNTIME_WEBSITE_SELECTORS = (
    'input[name*="url"]',
    'input[id*="url"]',
    'input[name*="website"]',
    'input[id*="website"]',
    'input[placeholder*="Website"]',
    'input[placeholder*="Url"]',
)

RUNTIME_SUBMIT_SELECTORS = (
    'input[type="submit"]',
    'button[type="submit"]',
    'button:has-text("Publish")',
    'button:has-text("Post")',
    'button:has-text("Comment")',
    'button:has-text("Submit")',
    'div[role="button"]:has-text("Publish")',
    '[aria-label*="Publish"]',
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


def _looks_like_blogger_url(url: str) -> bool:
    hostname = (urlparse(str(url or "")).hostname or "").lower()
    return "blogspot." in hostname or hostname.endswith(".blogger.com") or hostname == "blogger.com"


def _normalize_probe_result(url: str, result: Optional[Dict], fallback_stage: str) -> Dict:
    payload = dict(result or {})
    payload["url"] = url
    payload["recommended_format"] = str(payload.get("recommended_format", "unknown") or "unknown").strip()
    payload["evidence_type"] = str(payload.get("evidence_type", "unknown") or "unknown").strip()
    try:
        payload["confidence"] = float(payload.get("confidence", 0) or 0)
    except Exception:
        payload["confidence"] = 0.0
    payload["stage"] = str(payload.get("stage", fallback_stage) or fallback_stage)
    payload["supported_formats"] = payload.get("supported_formats") or [payload["recommended_format"]]
    return payload


def _is_weak_format(result: Optional[Dict], threshold: float = 0.8) -> bool:
    if not result:
        return True
    fmt = str(result.get("recommended_format", "unknown") or "unknown").strip()
    confidence = float(result.get("confidence", 0) or 0)
    return fmt in {"unknown", "plain_text_autolink"} or confidence < threshold


def _prefer_stronger_result(base: Dict, candidate: Optional[Dict]) -> Dict:
    if not candidate:
        return dict(base)
    candidate = dict(candidate)
    base_fmt = str(base.get("recommended_format", "unknown") or "unknown")
    candidate_fmt = str(candidate.get("recommended_format", "unknown") or "unknown")
    if candidate_fmt == "html" and base_fmt != "html":
        return candidate
    if base_fmt == "unknown" and candidate_fmt != "unknown":
        return candidate
    if base_fmt == "plain_text_autolink" and candidate_fmt == "plain_text":
        return base
    if float(candidate.get("confidence", 0) or 0) > float(base.get("confidence", 0) or 0):
        return candidate
    return dict(base)


def _is_probe_error_result(result: Optional[Dict]) -> bool:
    if not result:
        return False
    evidence_type = str(result.get("evidence_type", "") or "").strip().lower()
    return evidence_type in {
        "vision_api_error",
        "vision_invalid_json",
        "vision_probe_error",
        "runtime_probe_error",
    }


def _has_probe_conflict(static_result: Dict, runtime_result: Optional[Dict], vision_result: Optional[Dict]) -> bool:
    static_fmt = str(static_result.get("recommended_format", "unknown") or "unknown")
    runtime_fmt = str((runtime_result or {}).get("recommended_format", "unknown") or "unknown")
    vision_fmt = str((vision_result or {}).get("recommended_format", "unknown") or "unknown")

    if runtime_result and not _is_probe_error_result(runtime_result) and static_fmt == "plain_text_autolink" and runtime_fmt == "html":
        return True

    if runtime_result and not _is_probe_error_result(runtime_result) and static_fmt == "html" and runtime_fmt in {"unknown", "plain_text_autolink", "plain_text"}:
        return True

    if runtime_result and vision_result and not _is_probe_error_result(vision_result) and runtime_fmt != vision_fmt:
        return True

    return False


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

            blogger_result = self._detect_blogger_capability(soup, url)
            if blogger_result:
                return {
                    "url": url,
                    "status_code": response.status_code,
                    "title": self._get_title(soup),
                    "recommended_format": blogger_result["recommended_format"],
                    "evidence_type": blogger_result["evidence_type"],
                    "confidence": blogger_result["confidence"],
                    "supported_formats": [blogger_result["recommended_format"]],
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

    def analyze_runtime_page(self, page: Page, url: str) -> Dict:
        try:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            try:
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight * 0.7)")
                page.wait_for_timeout(800)
            except Exception:
                pass

            rendered_soup = BeautifulSoup(page.content(), "html.parser")

            editor_result = self._detect_editor_format(rendered_soup)
            if editor_result:
                return _normalize_probe_result(
                    url,
                    {
                        **editor_result,
                        "stage": "runtime_probe",
                    },
                    "runtime_probe",
                )

            history_result = self._detect_historical_comment_format(rendered_soup)
            if history_result:
                return _normalize_probe_result(
                    url,
                    {
                        **history_result,
                        "stage": "runtime_probe",
                    },
                    "runtime_probe",
                )

            fallback_result = self._detect_form_capability_fallback(rendered_soup)
            if fallback_result:
                return _normalize_probe_result(
                    url,
                    {
                        **fallback_result,
                        "stage": "runtime_probe",
                    },
                    "runtime_probe",
                )

            blogger_result = self._detect_blogger_capability(rendered_soup, page.url)
            if blogger_result:
                return _normalize_probe_result(
                    url,
                    {
                        **blogger_result,
                        "stage": "runtime_probe",
                    },
                    "runtime_probe",
                )

            if self._runtime_has_visible_contenteditable(page):
                return _normalize_probe_result(
                    url,
                    {
                        "recommended_format": "html",
                        "evidence_type": "runtime_contenteditable",
                        "confidence": 0.9,
                        "stage": "runtime_probe",
                    },
                    "runtime_probe",
                )

            frame_probe = self._runtime_iframe_probe(page)
            if frame_probe:
                return _normalize_probe_result(url, frame_probe, "runtime_probe")

            if self._runtime_has_visible_textarea(page) and self._runtime_has_visible_website_field(page):
                return _normalize_probe_result(
                    url,
                    {
                        "recommended_format": "html",
                        "evidence_type": "runtime_textarea_website_field",
                        "confidence": 0.76,
                        "stage": "runtime_probe",
                    },
                    "runtime_probe",
                )

            return _normalize_probe_result(
                url,
                {
                    "recommended_format": "unknown",
                    "evidence_type": "runtime_unknown",
                    "confidence": 0.0,
                    "stage": "runtime_probe",
                },
                "runtime_probe",
            )
        except Exception as exc:
            return _normalize_probe_result(
                url,
                {
                    "recommended_format": "unknown",
                    "evidence_type": "runtime_probe_error",
                    "confidence": 0.0,
                    "error": str(exc),
                    "stage": "runtime_probe",
                },
                "runtime_probe",
            )

    def analyze_page_capability(self, page: Page, url: str, enable_vision: bool = True, confidence_threshold: float = 0.8) -> Dict:
        static_result = _normalize_probe_result(url, self.analyze_website(url), "static_only")
        runtime_result = None
        vision_result = None
        page_ready = False

        if _is_weak_format(static_result, threshold=confidence_threshold):
            if not page_ready:
                self._prepare_probe_page(page, url)
                page_ready = True
            runtime_result = self.analyze_runtime_page(page, url)

        final_result = _prefer_stronger_result(static_result, runtime_result)
        conflict = _has_probe_conflict(static_result, runtime_result, None)

        if enable_vision and (_is_weak_format(final_result, threshold=confidence_threshold) or conflict):
            if not page_ready:
                self._prepare_probe_page(page, url)
                page_ready = True
            vision_result = self.analyze_vision_page(page, url)
            final_result = _prefer_stronger_result(final_result, vision_result)
            conflict = _has_probe_conflict(static_result, runtime_result, vision_result)

        status = "conflict" if conflict else "failed" if final_result["recommended_format"] == "unknown" else "completed"
        stage = "vision_probe" if vision_result else "runtime_probe" if runtime_result else "static_only"
        return {
            "url": url,
            "static_result": static_result,
            "runtime_result": runtime_result,
            "vision_result": vision_result,
            "final_result": _normalize_probe_result(url, {**final_result, "stage": stage}, stage),
            "stage": stage,
            "status": status,
            "vision_used": bool(vision_result),
            "conflict": conflict,
        }

    def _prepare_probe_page(self, page: Page, url: str) -> None:
        last_error = None
        for wait_until, timeout_ms in (("domcontentloaded", 12000), ("commit", 8000)):
            try:
                page.goto(url, timeout=timeout_ms, wait_until=wait_until)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        try:
            page.wait_for_load_state("domcontentloaded", timeout=2500)
        except Exception:
            pass
        try:
            # Slow ad-heavy blog pages often never reach a calm network state; keep
            # this best-effort and short so probe time is spent on actual capability checks.
            page.wait_for_load_state("networkidle", timeout=1500)
        except Exception:
            pass

    def analyze_vision_page(self, page: Page, url: str) -> Optional[Dict]:
        try:
            from vision_agent import analyze_link_format_capability

            analysis = analyze_link_format_capability(page)
        except Exception as exc:
            return _normalize_probe_result(
                url,
                {
                    "recommended_format": "unknown",
                    "evidence_type": "vision_probe_error",
                    "confidence": 0.0,
                    "error": str(exc),
                    "stage": "vision_probe",
                },
                "vision_probe",
            )

        if not analysis.get("ok") or not analysis.get("result"):
            return _normalize_probe_result(
                url,
                {
                    "recommended_format": "unknown",
                    "evidence_type": analysis.get("error_code", "vision_probe_error"),
                    "confidence": 0.0,
                    "reason": analysis.get("raw_text", ""),
                    "stage": "vision_probe",
                },
                "vision_probe",
            )

        result = dict(analysis["result"])
        result["stage"] = "vision_probe"
        return _normalize_probe_result(url, result, "vision_probe")

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

    def _detect_blogger_capability(self, soup: BeautifulSoup, url: str) -> Optional[Dict]:
        page_text = soup.get_text(" ", strip=True).lower()
        iframe_sources = [
            str(node.get("src", "") or "").lower()
            for node in soup.find_all("iframe")
        ]

        has_blogger_brand = _looks_like_blogger_url(url) or "powered by blogger" in page_text
        has_blogger_comment_surface = any("blogger.com/comment" in src or "blogblog.com" in src for src in iframe_sources)
        has_identity_menu = bool(soup.select("#identityMenu, select[name='identityMenu']"))
        has_comment_signal = self._has_comment_list_signal(soup) or bool(self._comment_forms(soup))

        if has_blogger_brand and (has_blogger_comment_surface or has_identity_menu or has_comment_signal):
            return {
                "recommended_format": "html",
                "evidence_type": "blogger_comment_system",
                "confidence": 0.72,
            }

        return None

    def batch_analyze(self, urls: list[str]) -> dict[str, Dict]:
        results = {}
        for url in urls:
            results[url] = self.analyze_website(url)
        return results

    def _runtime_has_visible_textarea(self, page: Page) -> bool:
        try:
            return page.locator("textarea:visible").count() > 0
        except Exception:
            return False

    def _runtime_has_visible_contenteditable(self, page: Page) -> bool:
        try:
            return page.locator('[contenteditable="true"]:visible').count() > 0
        except Exception:
            return False

    def _runtime_has_visible_website_field(self, page: Page) -> bool:
        for selector in RUNTIME_WEBSITE_SELECTORS:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _runtime_iframe_probe(self, page: Page) -> Optional[Dict]:
        for frame in page.frames:
            frame_url = str(getattr(frame, "url", "") or "")
            if not frame_url:
                continue
            if "blogger.com/comment" in frame_url or "blogblog.com" in frame_url:
                return {
                    "recommended_format": "html",
                    "evidence_type": "runtime_blogger_iframe",
                    "confidence": 0.86,
                    "stage": "runtime_probe",
                }
            try:
                if frame.locator('[contenteditable="true"]:visible').count() > 0:
                    return {
                        "recommended_format": "html",
                        "evidence_type": "runtime_iframe_contenteditable",
                        "confidence": 0.88,
                        "stage": "runtime_probe",
                    }
                if frame.locator("textarea:visible").count() > 0:
                    for selector in RUNTIME_SUBMIT_SELECTORS:
                        try:
                            btn = frame.locator(selector)
                            if btn.count() > 0 and btn.first.is_visible():
                                return {
                                    "recommended_format": "html",
                                    "evidence_type": "runtime_iframe_comment_surface",
                                    "confidence": 0.8,
                                    "stage": "runtime_probe",
                                }
                        except Exception:
                            continue
            except Exception:
                continue
        return None


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
