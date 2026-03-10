import os
from google import genai
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 获取 API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("未在 .env 中找到 GEMINI_API_KEY")

# 初始化新的 GenAI 客户端
client = genai.Client(api_key=GEMINI_API_KEY)

# 推荐的新版模型标识符
MODEL_ID = 'gemini-flash-latest'

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
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt
        )
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
    该网站支持的代码格式是：{link_format} (可能是 html, bbcode, markdown, 或者普通文本 url_field)。
    
    请帮我生成一句自然、简短、地道的英文短句，并用指定的 {link_format} 格式把上面的链接作为锚链接嵌入进去。
    锚文本应该与关键词相关。
    只返回生成的最终代码，不要解释。
    
    例如 HTML: "If you want to learn more, <a href='{target_url}'>visit our SEO strategies page</a>."
    例如 BBCode: "If you want to learn more, [url={target_url}]visit our SEO strategies page[/url]."
    例如 Markdown: "If you want to learn more, [visit our SEO strategies page]({target_url})."
    例如 url_field (纯文本): 无法嵌入超链接，直接返回 "{target_url}" 即可。
    """
    try:
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini 生成锚文本失败: {e}")
        return f"<a href='{target_url}'>click here</a>"

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
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini 生成评论失败: {e}")
        return f"Thanks for sharing this great information! Really helpful. {anchor_text}"

if __name__ == "__main__":
    # 测试代码
    print("正在测试最新 Gemini API 连通性...")
    test_url = "https://slideology.com"
    kw = analyze_keywords(test_url, "Slideology offers the best presentation templates.")
    print(f"生成关键词：{kw}")
    anchor = generate_anchor_text(kw, "markdown", test_url)
    print(f"生成锚文本：{anchor}")
    comment = generate_comment(anchor)
    print(f"生成最终评论：\n{comment}")
