import os
import json
import re
import time
from typing import Optional
from google import genai
from google.genai import types
from dotenv import load_dotenv
from gemini_key_manager import get_active_key

# 加载环境变量
load_dotenv()

# 生产环境默认使用固定型号，避免 latest 别名漂移
MODEL_ID = "gemini-3-flash-preview"
AI_DEFAULTS = {
    "request_timeout_seconds": 20,
    "retry_attempts": 3,
    "retry_backoff_seconds": 2,
    "model": MODEL_ID,
    "fallback_model": "gemini-flash-lite-latest",
}
_CLIENT_CACHE = {}


def _extract_json_payload(text: str):
    if not text:
        return None
    stripped = text.strip()
    candidates = [stripped]
    code_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if code_match:
        candidates.insert(0, code_match.group(1))
    brace_match = re.search(r"(\{.*\})", stripped, re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(1))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except Exception:
            continue
    return None


def _load_ai_config(config_path: str = "config.json") -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return {**AI_DEFAULTS, **config.get("ai_generation", {})}
    except Exception:
        return dict(AI_DEFAULTS)


def _get_client(config_path: str = "config.json"):
    config = _load_ai_config(config_path)
    timeout = int(config.get("request_timeout_seconds", AI_DEFAULTS["request_timeout_seconds"]) or AI_DEFAULTS["request_timeout_seconds"])
    gemini_api_key = get_active_key()
    if not gemini_api_key:
        raise ValueError("未在 .env 中找到可用的 GEMINI_API_KEY")
    cache_key = (gemini_api_key, timeout)
    cached = _CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    client = genai.Client(
        api_key=gemini_api_key,
        http_options=types.HttpOptions(
            timeout=timeout,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    _CLIENT_CACHE[cache_key] = client
    return client


def _is_retryable_ai_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return any(
        marker in message
        for marker in (
            "timed out",
            "timeout",
            "deadline exceeded",
            "connection reset",
            "temporarily unavailable",
            "service unavailable",
            "unavailable",
            "429",
            "500",
            "502",
            "503",
            "504",
        )
    )


def _model_candidates(explicit_model: Optional[str], config: dict) -> list[str]:
    candidates = [
        explicit_model or str(config.get("model", MODEL_ID) or MODEL_ID),
        str(config.get("fallback_model", "") or "").strip(),
    ]
    deduped = []
    for item in candidates:
        if item and item not in deduped:
            deduped.append(item)
    return deduped or [MODEL_ID]


def _generate_content(contents, model: Optional[str] = None, config_path: str = "config.json"):
    config = _load_ai_config(config_path)
    client = _get_client(config_path)
    attempts = max(1, int(config.get("retry_attempts", AI_DEFAULTS["retry_attempts"]) or AI_DEFAULTS["retry_attempts"]))
    backoff = float(config.get("retry_backoff_seconds", AI_DEFAULTS["retry_backoff_seconds"]) or AI_DEFAULTS["retry_backoff_seconds"])
    last_exc = None

    for model_name in _model_candidates(model, config):
        for attempt in range(attempts):
            try:
                return client.models.generate_content(model=model_name, contents=contents)
            except Exception as exc:
                last_exc = exc
                retryable = _is_retryable_ai_error(exc)
                if attempt < attempts - 1 and retryable:
                    time.sleep(backoff * (attempt + 1))
                    continue
                if not retryable:
                    raise
                break

    raise last_exc or RuntimeError("Gemini generate_content 未返回结果。")

def analyze_keywords(target_url, site_content=""):
    """
    根据目标推广网站的 URL 或内容，分析出适合发外链用的 SEO 关键词
    """
    prompt = f"""
    你是一个专业的 SEO 专家。我们的推广目标网站是：{target_url}。
    请根据这个网站的主题（如果可能的话猜测它大概是做什么的），
    给出 3-5 个最适合用于做外链锚文本的英文关键词，以逗号分隔，不要有任何多余的解释。
    
    如果有网站参考内容：{site_content[:1000]}
    """
    try:
        response = _generate_content(prompt, model=MODEL_ID)
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini 分析关键词失败: {e}")
        return "click here, visit website"

def generate_anchor_text(keywords, link_format, target_url):
    """
    根据关键词和目标网站支持的链接格式，生成对应的锚文本代码
    """
    prompt = f"""
    我需要在其他网站上留一个我们自己的外链。
    推广链接是：{target_url}
    关联关键词有：{keywords}
    该网站支持的代码格式是：{link_format} (可能是 html, bbcode, markdown, plain_text_autolink, plain_text, 或者普通文本 url_field)。
    
    请帮我生成一句自然、简短、地道的英文短句，并用指定的 {link_format} 格式把上面的链接作为锚链接嵌入进去。
    锚文本应该与关键词相关。
    只返回生成的最终代码，不要解释。
    
    例如 HTML: "If you want to learn more, <a href='{target_url}'>visit our SEO strategies page</a>."
    例如 BBCode: "If you want to learn more, [url={target_url}]visit our SEO strategies page[/url]."
    例如 Markdown: "If you want to learn more, [visit our SEO strategies page]({target_url})."
    例如 plain_text_autolink / plain_text / url_field: 无法嵌入超链接，直接返回 "{target_url}" 即可。
    """
    try:
        response = _generate_content(prompt, model=MODEL_ID)
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini 生成锚文本失败: {e}")
        return target_url

def generate_comment(anchor_text, forum_topic=""):
    """
    生成一段带锚文本的伪装真人评论
    """
    prompt = f"""
    你是一个真实的互联网用户，正在一个外语论坛或博客上留言。
    论坛的主题是（如果不为空的话可以参考）：{forum_topic}
    请用英文写一段友善的、符合语境的评论（大约 2-3 句话），并在评论的结尾部分，自然地带上以下这句锚文本链接：
    {anchor_text}
    
    切记：
    1. 语气一定要像真人。
    2. 如果主题为空，就写一句万能的友善感谢语（比如感谢分享、文章写得很好）。
    3. 只需返回评论内容本身，不要返回其他的解释说明。
    """
    try:
        response = _generate_content(prompt, model=MODEL_ID)
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini 生成评论失败: {e}")
        return f"Thanks for sharing this great information! Really helpful. {anchor_text}"


def translate_content_fields(fields: dict, target_language_name: str) -> dict:
    prompt = f"""
你是一个多语言内容翻译助手。请把输入 JSON 的值翻译成 {target_language_name}，并严格返回 JSON。

要求：
1. 保留所有 URL、域名、HTML 标签、Markdown 链接、BBCode、换行和标点结构。
2. 只翻译自然语言内容，不要删除字段，不要加解释。
3. 返回结果必须是 JSON，对象键名必须与输入完全一致。

输入 JSON：
{json.dumps(fields, ensure_ascii=False, indent=2)}
"""
    try:
        response = _generate_content(prompt, model=MODEL_ID)
        payload = _extract_json_payload(getattr(response, "text", ""))
        if not payload:
            raise RuntimeError("Gemini 未返回可解析的多语言翻译 JSON。")
        return {key: str(payload.get(key, value)) for key, value in fields.items()}
    except Exception as e:
        print(f"❌ Gemini 翻译内容失败: {e}")
        return {key: str(value) for key, value in fields.items()}


def translate_comment_to_chinese(comment_content: str) -> str:
    translated = translate_content_fields({"comment_content_zh": comment_content}, "Simplified Chinese")
    return translated["comment_content_zh"]


def summarize_comment_discussion(comments_raw: list[str], target_language_name: str) -> str:
    if not comments_raw:
        return ""
    trimmed_comments = comments_raw[:20]
    prompt = f"""
你是一个评论区分析助手。请阅读这些公开评论，并输出一个简短摘要，供另一个写作模型使用。

要求：
1. 使用 {target_language_name} 输出。
2. 摘要只保留讨论主题、常见观点、评论语气、重复出现的关键词。
3. 不逐条复述，不要加解释，不超过 180 字。

评论列表：
{json.dumps(trimmed_comments, ensure_ascii=False, indent=2)}
"""
    try:
        response = _generate_content(prompt, model=MODEL_ID)
        return getattr(response, "text", "").strip()
    except Exception as e:
        print(f"❌ Gemini 评论区摘要失败: {e}")
        return ""


def load_active_target(targets_path="targets.json"):
    """
    从 targets.json 读取 active=true 的推广目标。
    如果没有 active 的目标，返回 None。
    这样可以支持多个目标网站，切换时只需在 json 里改 active 字段。
    """
    import json, os
    path = targets_path if os.path.isabs(targets_path) else os.path.join(os.path.dirname(__file__), targets_path)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for target in data.get("targets", []):
            if target.get("active", False):
                return target
    except Exception as e:
        print(f"⚠️ 读取 targets.json 失败: {e}")
    return None


def build_anchor_texts(anchor_text: str, target_url: str) -> dict:
    """
    根据用户提供的固定锚文本词和目标链接，生成四种格式的锚文本代码：
    - html: <a href="url">anchor_text</a>
    - bbcode: [url=url]anchor_text[/url]
    - markdown: [anchor_text](url)
    - url_field/plain_text/plain_text_autolink: 直接返回 url
    
    返回一个字典，包含所有格式
    """
    return {
        "html":      f'<a href="{target_url}">{anchor_text}</a>',
        "bbcode":    f'[url={target_url}]{anchor_text}[/url]',
        "markdown":  f'[{anchor_text}]({target_url})',
        "url_field": target_url,
        "plain_text": target_url,
        "plain_text_autolink": target_url,
        "unknown": target_url,
    }


def get_anchor_for_format(anchor_text: str, link_format: str, target_url: str) -> str:
    """
    根据 link_format 返回对应格式的锚文本代码。
    若 link_format 未知，默认返回保守的裸 URL。
    """
    texts = build_anchor_texts(anchor_text, target_url)
    fmt = link_format.lower().strip() if link_format else "url_field"
    return texts.get(fmt, texts["plain_text"])


def generate_comment_for_target(target: dict, link_format: str = "markdown", forum_topic: str = "") -> str:
    """
    根据 targets.json 中的推广目标，用指定格式（html/bbcode/markdown）生成一段自然评论。
    (评论内容由 AI 生成，锚文本词固定不变)
    """
    anchor_text = target.get("anchor_text", "click here")
    target_url  = target.get("url", "")
    description = target.get("description", "")
    
    # 根据指定格式获取锚链接片段
    anchor_code = get_anchor_for_format(anchor_text, link_format, target_url)
    
    prompt = f"""
你是一个真实的互联网用户，正在一个外语论坛或博客上留言评论。
{'论坛/博客的主题是：' + forum_topic if forum_topic else ''}
{'关于我们推广网站的描述：' + description if description else ''}

请用英文写 2-3 句友善的、符合语境的评论，并在末尾自然地融入下面这个链接（格式不能改动）：
{anchor_code}

要求：
1. 语气像真人，不要太商业化
2. 只返回评论内容，不要加任何解释
3. 如果链接格式是 plain_text / plain_text_autolink / url_field，则必须把裸 URL 自然地放进句子里，不能改成 HTML、Markdown 或 BBCode。
    4. 锚文本部分必须原封不动地保留：{anchor_code}
"""
    try:
        response = _generate_content(prompt, model=MODEL_ID)
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini 生成评论失败: {e}")
        return f"Really enjoyed this article, thanks for sharing! Check out {anchor_code} too."


def generate_localized_bundle_for_target(
    target: dict,
    link_format: str,
    page_context: dict,
    include_chinese_translation: bool = True,
) -> dict:
    target_url = target.get("url", "")
    target_description = target.get("description", "")
    anchor_seed = target.get("anchor_text", "click here")
    language_name = page_context.get("language_name", "English")
    page_title = page_context.get("title", "")
    page_description = page_context.get("description", "")
    page_excerpt = page_context.get("excerpt", "")
    comments_summary = page_context.get("comments_summary", "")
    page_url = page_context.get("url", "")

    prompt = f"""
你是一个经验丰富的站外评论写手，需要在目标网页上留下自然、有上下文的评论。

目标网页：
- URL: {page_url}
- 标题: {page_title}
- 摘要: {page_description}
- 正文摘要: {page_excerpt[:1200]}
- 评论区摘要: {comments_summary[:800]}
- 目标网页主要语言: {language_name}

我们要推广的网站：
- URL: {target_url}
- 产品描述: {target_description}
- 锚文本语义种子: {anchor_seed}
- 链接格式: {link_format}

请严格返回 JSON，字段如下：
{{
  "language_code": "目标网页语言代码",
  "language_name": "目标网页语言名称",
  "keywords": "3-5 个逗号分隔的关键词，使用目标网页语言",
  "anchor_text": "按 {link_format} 格式生成的锚文本，必须保留 URL 原样",
  "comment_content": "2-3 句评论，必须结合目标网页主题或评论区讨论点，不能是泛泛而谈的万能夸奖，并且末尾原封不动包含 anchor_text",
  "comment_content_zh": "comment_content 的简体中文翻译，保留 URL 和链接格式"
}}

要求：
1. 关键词、锚文本、评论内容必须与目标网页语言一致；若无法判断，默认英语。
2. 评论必须结合网页标题、正文主题或评论区讨论点中的至少一个具体话题，不能只写“Thanks for sharing”这类泛化评论。
3. anchor_text 必须是自然短句，不要只输出裸链接；如果格式是 url_field、plain_text 或 plain_text_autolink，则直接返回 URL。
4. comment_content 中必须原封不动包含 anchor_text。
5. comment_content_zh 必须忠实翻译 comment_content。
    6. 除 JSON 外不要输出任何解释。
"""
    try:
        response = _generate_content(prompt, model=MODEL_ID)
        payload = _extract_json_payload(getattr(response, "text", ""))
        if not payload:
            raise RuntimeError("Gemini 未返回可解析的多语言内容 JSON。")
        fallback_anchor = get_anchor_for_format(anchor_seed, link_format, target_url)
        anchor_text = str(payload.get("anchor_text", fallback_anchor))
        if link_format in {"url_field", "plain_text", "plain_text_autolink"}:
            anchor_text = target_url
        elif target_url not in anchor_text:
            anchor_text = fallback_anchor
        comment_content = str(payload.get("comment_content", generate_comment_for_target(target, link_format, page_title)))
        if anchor_text not in comment_content:
            comment_content = f"{comment_content.rstrip()} {anchor_text}".strip()
        comment_content_zh = str(payload.get("comment_content_zh", "")).strip()
        if not include_chinese_translation:
            comment_content_zh = ""
        elif not comment_content_zh:
            comment_content_zh = translate_comment_to_chinese(comment_content)
        return {
            "language_code": str(payload.get("language_code", page_context.get("language_code", "en"))),
            "language_name": str(payload.get("language_name", language_name)),
            "keywords": str(payload.get("keywords", anchor_seed)),
            "anchor_text": anchor_text,
            "comment_content": comment_content,
            "comment_content_zh": comment_content_zh,
        }
    except Exception as e:
        print(f"❌ Gemini 生成多语言上下文评论失败: {e}")
        anchor_text = get_anchor_for_format(anchor_seed, link_format, target_url)
        comment_content = generate_comment_for_target(target, link_format, page_title)
        return {
            "language_code": page_context.get("language_code", "en"),
            "language_name": language_name,
            "keywords": anchor_seed,
            "anchor_text": anchor_text,
            "comment_content": comment_content,
            "comment_content_zh": translate_comment_to_chinese(comment_content) if include_chinese_translation else "",
        }


if __name__ == "__main__":
    # 测试代码
    print("正在测试最新 Gemini API 连通性...")
    test_url = "https://bearclicker.net/"
    kw = analyze_keywords(test_url, "Bear Clicker is a browser game about creating and collecting unique bears.")
    print(f"生成关键词：{kw}")
    anchor = generate_anchor_text(kw, "markdown", test_url)
    print(f"生成锚文本：{anchor}")
    comment = generate_comment(anchor)
    print(f"生成最终评论：\n{comment}")
