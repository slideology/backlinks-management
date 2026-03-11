"""
vision_agent.py
=================
Gemini Vision 多模态视觉代理模块

功能：当传统 DOM 寻找评论框的方式失败后，
对当前网页截图，发送给 Gemini Vision AI，
让 AI 从截图中识别评论框和提交按钮的屏幕坐标，
再用物理鼠标点击来突破各类前端结构限制。
"""
import os
import re
import base64
import json
from typing import Optional
from dotenv import load_dotenv
from playwright.sync_api import Page

load_dotenv()

def _get_gemini_client():
    """初始化 Gemini 客户端"""
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("环境变量 GEMINI_API_KEY 未配置，无法使用 Vision 模块！")
    return genai.Client(api_key=api_key)


def analyze_page_for_comment_area(page: Page) -> Optional[dict]:
    """
    对网页截图并调用 Gemini Vision 分析，
    返回评论框和提交按钮的屏幕坐标字典，或 None（未找到时）。
    
    返回格式：
    {
        "textarea_x": 640,
        "textarea_y": 800,
        "submit_x": 640,
        "submit_y": 950,
        "has_overlay": False
    }
    """
    print("  📸 正在截取网页快照，准备发给 AI 视觉模型分析...")
    
    try:
        # 截全页截图（返回 bytes）
        screenshot_bytes = page.screenshot(full_page=False)
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
        
        # 获取视口尺寸，用于 prompt 提示
        viewport = page.viewport_size
        width = viewport['width'] if viewport else 1280
        height = viewport['height'] if viewport else 800
        
        prompt = f"""
这是一张网页的屏幕截图，尺寸为 {width} x {height} 像素。

请帮我完成以下任务（严格按 JSON 格式回答，不要加任何其他文字）：
1. 找出网页上用于填写评论/留言的文本输入区域（textarea 或 contenteditable 区域）的中心点坐标
2. 找出提交评论/留言的按钮的中心点坐标
3. 判断是否有全屏遮挡弹窗（如 Cookie 同意弹窗），如果有，找出关闭该弹窗的按钮坐标

返回格式（如果某项不存在，对应值设置为 null）：
{{
  "textarea_x": <X坐标数字或null>,
  "textarea_y": <Y坐标数字或null>,
  "submit_x": <提交按钮X坐标数字或null>,
  "submit_y": <提交按钮Y坐标数字或null>,
  "overlay_close_x": <弹窗关闭按钮X坐标数字或null>,
  "overlay_close_y": <弹窗关闭按钮Y坐标数字或null>
}}
"""
        
        client = _get_gemini_client()
        
        from google.genai import types
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(
                    data=screenshot_bytes,
                    mime_type="image/png"
                ),
                prompt
            ]
        )
        
        result_text = response.text.strip()
        print(f"  🤖 Vision AI 分析结果: {result_text[:200]}...")
        
        # 先剥离 Markdown 代码块标记（模型有时会返回 ```json {...} ``` 格式）
        clean_text = re.sub(r'```(?:json)?\s*', '', result_text).replace('```', '').strip()
        
        # 提取 JSON 内容
        json_match = re.search(r'\{.*\}', clean_text, re.DOTALL)
        if json_match:
            coords = json.loads(json_match.group())
            return coords
        
        return None
        
    except Exception as e:
        print(f"  ❌ Vision AI 分析失败: {e}")
        return None


def try_post_via_vision(page: Page, comment_content: str) -> tuple[bool, str]:
    """
    使用 Vision AI 坐标定位来完成评论填写和提交。
    
    返回 (是否成功, 描述信息)
    """
    coords = analyze_page_for_comment_area(page)
    
    if not coords:
        return False, "Vision AI 无法从截图中识别有效的表单坐标。"
    
    # 如果发现了弹窗遮挡，先关掉它
    if coords.get("overlay_close_x") and coords.get("overlay_close_y"):
        print(f"  🛑 Vision AI 发现遮挡弹窗，正在关闭...")
        page.mouse.click(coords["overlay_close_x"], coords["overlay_close_y"])
        import time
        time.sleep(1)
    
    # 点击评论框并填入内容
    tx = coords.get("textarea_x")
    ty = coords.get("textarea_y")
    if tx and ty:
        print(f"  📍 Vision AI 定位到评论框坐标：({tx}, {ty})，正在填写...")
        page.mouse.click(tx, ty)
        import time
        time.sleep(0.5)
        page.keyboard.type(comment_content, delay=25)
        time.sleep(1)
        
        # 点击提交按钮
        sx = coords.get("submit_x")
        sy = coords.get("submit_y")
        if sx and sy:
            print(f"  👉 Vision AI 定位到提交按钮坐标：({sx}, {sy})，正在点击...")
            page.mouse.click(sx, sy)
            page.wait_for_timeout(5000)
            return True, f"Vision AI 视觉定位成功！评论框坐标({tx},{ty})，提交按钮坐标({sx},{sy})"
        else:
            return False, "Vision AI 识别到了评论框但未找到提交按钮的坐标。"
    else:
        return False, "Vision AI 未能在截图中识别到评论输入框的坐标。"
