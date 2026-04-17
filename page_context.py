import html
import re
from urllib.parse import urlparse

import requests


LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "pl": "Polish",
    "nl": "Dutch",
    "ru": "Russian",
}

STOPWORDS = {
    "en": {"the", "and", "you", "with", "this", "that", "for", "your", "from"},
    "es": {"que", "para", "con", "una", "este", "esta", "los", "las", "por"},
    "fr": {"avec", "pour", "dans", "vous", "une", "des", "les", "est", "sur"},
    "de": {"und", "mit", "eine", "der", "die", "das", "für", "nicht", "ist"},
    "it": {"che", "per", "con", "una", "sono", "questo", "della", "delle"},
    "pt": {"com", "para", "uma", "este", "esta", "você", "não", "das", "dos"},
    "pl": {"jest", "nie", "dla", "oraz", "który", "która", "przez", "się"},
    "nl": {"met", "een", "voor", "niet", "deze", "dat", "zijn", "van", "het"},
    "ru": {"и", "в", "не", "на", "что", "это", "для", "как", "с", "по"},
}

COMMENT_CONTAINER_HINTS = (
    "comments",
    "comment-list",
    "commentlist",
    "discussion",
    "responses",
)
COMMENT_ITEM_HINTS = (
    "comment",
    "comment-content",
    "comment-body",
    "comment-text",
    "reply-content",
    "comment__content",
)
COMMENT_EXCLUDE_HINTS = (
    "recent comments",
    "related posts",
    "leave a reply",
    "post comment",
    "navigation",
    "sidebar",
)


def _clean_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_title(raw_html: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_html)
    return html.unescape(match.group(1)).strip() if match else ""


def _extract_meta_description(raw_html: str) -> str:
    patterns = [
        r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'(?is)<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
        r'(?is)<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_html)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def _extract_html_lang(raw_html: str) -> str:
    match = re.search(r'(?is)<html[^>]+lang=["\']([a-zA-Z-]+)["\']', raw_html)
    return match.group(1).lower() if match else ""


def _extract_comment_candidates(raw_html: str) -> list[str]:
    candidates = []
    tag_patterns = [
        r"(?is)<(?P<tag>section|div|ol|ul)[^>]*(id|class)=[\"'][^\"']*(comments|comment-list|commentlist|discussion|responses)[^\"']*[\"'][^>]*>(?P<content>.*?)</(?P=tag)>",
        r"(?is)<(?P<tag>article|div|li)[^>]*(id|class)=[\"'][^\"']*(comment-content|comment-body|comment-text|comment__content|reply-content)[^\"']*[\"'][^>]*>(?P<content>.*?)</(?P=tag)>",
    ]

    for pattern in tag_patterns:
        for match in re.finditer(pattern, raw_html):
            text = _clean_text(match.group("content"))
            normalized = text.lower()
            if not text or len(text) < 25:
                continue
            if any(exclude in normalized for exclude in COMMENT_EXCLUDE_HINTS):
                continue
            candidates.append(text[:1200])

    deduped = []
    seen = set()
    for candidate in candidates:
        key = candidate[:180]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped[:20]


def _normalize_language_code(code: str) -> str:
    if not code:
        return ""
    base = code.split("-")[0].lower()
    return base if base in LANGUAGE_NAMES else ""


def _score_stopwords(text: str, language_code: str) -> int:
    words = re.findall(r"[a-zA-ZÀ-ÿ']+", text.lower())
    lexicon = STOPWORDS.get(language_code, set())
    return sum(1 for word in words if word in lexicon)


def detect_language(text: str, html_lang: str = "") -> str:
    normalized = _normalize_language_code(html_lang)
    if normalized:
        return normalized

    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    if re.search(r"[А-Яа-яЁё]", text):
        return "ru"

    best_code = "en"
    best_score = 0
    for code in STOPWORDS:
        score = _score_stopwords(text, code)
        if score > best_score:
            best_code = code
            best_score = score
    return best_code


def fetch_page_context(url: str, timeout: int = 12, include_comments_summary: bool = False) -> dict:
    fallback_title = urlparse(url).netloc
    context = {
        "url": url,
        "title": fallback_title,
        "description": "",
        "excerpt": "",
        "language_code": "en",
        "language_name": "English",
        "comments_raw": [],
        "comments_summary": "",
        "status_code": None,
        "fetch_error": "",
    }

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
                )
            },
            timeout=timeout,
        )
        response.raise_for_status()
        raw_html = response.text[:400000]
        title = _extract_title(raw_html) or fallback_title
        description = _extract_meta_description(raw_html)
        text = _clean_text(raw_html)
        excerpt = text[:1800]
        comments_raw = _extract_comment_candidates(raw_html)
        language_code = detect_language(f"{title} {description} {excerpt}", _extract_html_lang(raw_html))
        comments_summary = ""
        if comments_raw and include_comments_summary:
            try:
                from ai_generator import summarize_comment_discussion

                comments_summary = summarize_comment_discussion(comments_raw, LANGUAGE_NAMES.get(language_code, "English"))
            except Exception:
                comments_summary = ""

        context.update(
            {
                "title": title,
                "description": description,
                "excerpt": excerpt,
                "language_code": language_code,
                "language_name": LANGUAGE_NAMES.get(language_code, "English"),
                "comments_raw": comments_raw,
                "comments_summary": comments_summary,
                "status_code": response.status_code,
            }
        )
    except Exception as exc:
        context["fetch_error"] = str(exc)

    return context
