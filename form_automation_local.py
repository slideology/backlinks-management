import time
import re
from playwright.sync_api import sync_playwright, Page

from gws_integration import GoogleSheetsManager
from ai_generator import analyze_keywords, generate_anchor_text, generate_comment

MY_TARGET_WEBSITE = "https://slideology.com"

# =====================================================================
# 预处理：尝试关掉所有 Cookie 同意弹窗（修复 GDPR 遮挡问题）
# =====================================================================
def try_dismiss_overlays(page: Page):
    """
    尝试关闭常见的 Cookie/GDPR 弹窗，防止它遮挡评论框和提交按钮。
    欧洲网站几乎 100% 有这类弹框，是导致按钮被遮挡最常见的元凶。
    此函数不会在任何情况下抛出异常，仅做"尽力而为"的清理。
    """
    dismiss_selectors = [
        'button:has-text("Accept all")',
        'button:has-text("Accept All")',
        'button:has-text("Accept")',
        'button:has-text("I Accept")',
        'button:has-text("Agree")',
        'button:has-text("I Agree")',
        'button:has-text("OK")',
        'button:has-text("Got it")',
        'button:has-text("Close")',
        'button:has-text("Dismiss")',
        '#onetrust-accept-btn-handler',         # OneTrust Cookie 框架
        '.cookie-consent button',
        '.gdpr-banner button',
        '[aria-label="Close"]',
        '[aria-label="Dismiss"]',
        '.cc-btn.cc-allow',                      # Cookie Consent JS 框架
        '#cookieConsentOK',
    ]
    for selector in dismiss_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=500)
                time.sleep(0.5)
                print(f"  ✅ 已自动关闭一个弹窗: [{selector}]")
                break  # 关掉一个就行，不用全试
        except:
            continue  # 找不到或点不到，静默跳过


# =====================================================================
# 核心发帖函数（修复了 contenteditable 识别 + 弹窗清除 + 重试机制）
# =====================================================================
def auto_post_content(page: Page, comment_content: str, url: str, max_retries: int = 2):
    """
    四层衰减策略全自动发帖机器人：
    Layer 1 → 传统 DOM (textarea / contenteditable)
    Layer 2 → Google SSO 单点登录后再试
    Layer 3 → Gemini Vision AI 截图坐标物理点击
    Layer 4 → 宣告失败
    """
    last_error = ""
    
    for attempt in range(max_retries):
        if attempt > 0:
            print(f"  ⏳ 第 {attempt + 1} 次重试...")
            time.sleep(5)
        
        try:
            page.goto(url, timeout=30000)
            try:
                page.wait_for_load_state('networkidle', timeout=15000)
            except:
                pass
            
            # 【预处理】关闭 GDPR Cookie 弹窗
            try_dismiss_overlays(page)
            
            # ===== Layer 1：传统 DOM 识别 =====
            layer1_result = _try_dom_post(page, comment_content)
            if layer1_result[0]:
                return layer1_result
            
            print("  🔄 Layer 1 失败，尝试 Layer 2：Google SSO 单点登录...")
            
            # ===== Layer 2：Google SSO 登录后再试 =====
            from sso_handler import detect_and_do_google_sso
            sso_success = detect_and_do_google_sso(page)
            if sso_success:
                print("  ✅ SSO 登录成功，重新尝试填写表单...")
                try_dismiss_overlays(page)  # 登录后可能还有弹窗
                layer2_result = _try_dom_post(page, comment_content)
                if layer2_result[0]:
                    return layer2_result
                print("  🔄 SSO 后 DOM 方式仍失败，升级到 Layer 3...")
            else:
                print("  ➡️  未找到 Google SSO 入口，直接尝试 Layer 3...")
            
            # ===== Layer 3：Vision AI 截图坐标点击 =====
            print("  🤖 Layer 3：调用 Gemini Vision AI 分析网页截图...")
            from vision_agent import try_post_via_vision
            vision_result = try_post_via_vision(page, comment_content)
            if vision_result[0]:
                return vision_result
            
            last_error = vision_result[1]
            
        except Exception as e:
            last_error = str(e)
            print(f"  ⚠️ 发生异常（尝试 {attempt + 1}/{max_retries}）: {e}")
    
    return False, last_error


def _try_dom_post(page: Page, comment_content: str) -> tuple[bool, str]:
    """
    Layer 1 内部函数：使用传统 DOM 方式寻找评论框并填写提交
    """
    # 方式 1：传统 textarea
    textareas = page.locator('textarea:visible')
    if textareas.count() > 0:
        print("  👁️ 找到 <textarea> 输入框，正在填写...")
        ta = textareas.first
        ta.scroll_into_view_if_needed()
        ta.fill(comment_content)
        time.sleep(1)
        return _try_submit(page)
    
    # 方式 2：现代 contenteditable 富文本框
    content_editables = page.locator('[contenteditable="true"]:visible')
    if content_editables.count() > 0:
        print("  👁️ 找到 contenteditable 富文本评论框，正在填写...")
        ce = content_editables.first
        ce.scroll_into_view_if_needed()
        ce.click()
        time.sleep(0.3)
        page.keyboard.type(comment_content, delay=30)
        time.sleep(1)
        return _try_submit(page)
    
    return False, "Layer 1: 未找到 textarea 或 contenteditable 评论框"


def _try_submit(page: Page) -> tuple[bool, str]:
    """
    内部辅助函数：寻找提交按钮并点击，返回 (是否成功, 描述信息)
    """
    print("  👁️ 正在寻找提交按钮...")
    button_selectors = [
        'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("Comment")',
        'button:has-text("Post Comment")',
        'button:has-text("Post")',
        'button:has-text("Submit")',
        'button:has-text("Save")',
        'button:has-text("Send")',
        'button:has-text("Publish")',
        '.submit-button',
        '#submit',
        '#commentsubmit',
    ]
    for selector in button_selectors:
        try:
            btns = page.locator(selector)
            if btns.count() > 0 and btns.first.is_visible():
                print(f"  👉 找到按钮 [{selector}]，准备点击...")
                btns.first.scroll_into_view_if_needed()
                btns.first.click()
                page.wait_for_timeout(5000)
                return True, "机器人自动识图填表并点击提交成功！"
        except:
            continue
    
    return False, "填写了评论内容，但没有找到可以点击的提交按钮。"


# =====================================================================
# 处理单条任务
# =====================================================================
def process_task(task_row, api_row_index, manager, browser):
    url = task_row[manager.col_map['URL']]
    link_format = task_row[manager.col_map['Link_Format']]
    
    print(f"\n🚀 开始处理任务行 {api_row_index} -> {url}")
    
    # 步骤 1：AI 生成文案
    print("  [1/3] 🤖 AI 正在生成外链推广文案...")
    keywords = task_row[manager.col_map['Keywords']]
    if not keywords or keywords.strip() == "":
        keywords = analyze_keywords(MY_TARGET_WEBSITE)
    
    anchor_text = generate_anchor_text(keywords, link_format, MY_TARGET_WEBSITE)
    comment_content = generate_comment(anchor_text)
    
    # 步骤 2：自动发帖（带重试机制）
    print("  [2/3] 🌐 开始接管真实 Chrome 进行自动发帖...")
    context = browser.contexts[0]
    page = context.new_page()
    
    is_success, result_msg = auto_post_content(page, comment_content, url)
    print(f"  🤖 发帖结果: {result_msg}")
    page.close()
    
    # 步骤 3：结果写回 Google Sheets
    print("  [3/3] 📝 更新结果至 Google Sheets...")
    from datetime import datetime, timedelta
    
    updates = {
        'Target_Website': MY_TARGET_WEBSITE,
        'Keywords': keywords,
        'Anchor_Text': anchor_text,
        'Comment_Content': comment_content,
        'Notes': result_msg
    }
    
    if is_success:
        updates['Status'] = 'completed'
        updates['Success_URL'] = url
    else:
        updates['Status'] = 'failed'
        # 【新增】写入 3 天后的重试日期，让 scheduler 到时候重新捞出来
        retry_date = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
        if 'retry_at' in manager.col_map:
            updates['retry_at'] = retry_date
    
    manager.update_task(api_row_index, updates)
    return is_success


# =====================================================================
# 主程序入口
# =====================================================================
def main():
    manager = GoogleSheetsManager()
    all_data = manager.read_all_tasks()
    
    if len(all_data) <= 1:
        print("表格无数据！")
        return
    
    status_idx = manager.col_map['Status']
    today_tasks = []
    
    for i, row in enumerate(all_data[1:]):
        status = row[status_idx] if len(row) > status_idx else ''
        if status == 'in_progress':
            today_tasks.append({
                'row_index': i + 1,
                'data': row
            })
    
    print("=" * 50)
    print("🤖 外链自动化 - 增强版全自动发布模块")
    print("=" * 50)
    
    if not today_tasks:
        print("🎉 今日无发布任务，请先运行 daily_scheduler.py 挑选任务。")
        return
    
    print(f"📦 找到 {len(today_tasks)} 个 in_progress 任务，开始处理...")
    
    print("🕸️ 正在连接你的本地 Chrome 浏览器（9222 端口）...")
    with sync_playwright() as p:
        try:
            browser_app = p.chromium.connect_over_cdp("http://localhost:9222")
            print("🤩 成功接管本地真实 Chrome！指纹认证已生效。\n")
            
            success_count = 0
            failed_count = 0
            
            for idx, task in enumerate(today_tasks, start=1):
                print(f"\n>>> 进度: {idx}/{len(today_tasks)}")
                is_success = process_task(task['data'], task['row_index'], manager, browser_app)
                if is_success:
                    success_count += 1
                else:
                    failed_count += 1
            
            browser_app.close()
            
            # 发送飞书通知
            from webhook_sender import create_webhook_sender
            sender = create_webhook_sender()
            if sender:
                summary = {"success": success_count, "failed": failed_count}
                sender.send_summary("🌍 外链自动化执行报告", summary)
            else:
                print("\nℹ️ 未配置飞书 Webhook，跳过通知。")
        
        except Exception as e:
            print(f"❌ 接管本地浏览器失败（请确认是通过 Start_Robot.command 启动的）: {e}")

if __name__ == '__main__':
    main()
