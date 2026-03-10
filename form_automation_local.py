import time
from playwright.sync_api import sync_playwright

from gws_integration import GoogleSheetsManager
from ai_generator import analyze_keywords, generate_anchor_text, generate_comment

MY_TARGET_WEBSITE = "https://slideology.com"

def auto_post_content(page, comment_content, url):
    """
    一个【全自动尽力而为】的发帖机器人。
    与之前不同，这次它是在你带有所有 Cookie 的真实浏览器里跑的！
    所以遇到论坛不用再登录了，大概率也不会出那么多验证码！
    """
    try:
        # 打开页面，设置 30 秒超时
        page.goto(url, timeout=30000)
        
        # 等待页面基本加载完毕（网络空闲），因为是真实环境，速度可能更快
        page.wait_for_load_state('networkidle', timeout=10000)
        
        # 尝试寻找文字输入框 (textarea)
        textareas = page.locator('textarea')
        if textareas.count() > 0:
            print("👁️ 机器人的眼睛找到了输入框！正在尝试填写内容...")
            textarea_to_fill = textareas.first
            # 滚动到该元素并在输入前点击一下获取焦点
            textarea_to_fill.scroll_into_view_if_needed()
            textarea_to_fill.fill(comment_content)
            time.sleep(1) # 假装真人停顿一下
            
            # 使用多种规则寻找提交按钮
            print("👁️ 正在寻找提交或评论按钮...")
            button_selectors = [
                'input[type="submit"]', 
                'button[type="submit"]',
                'button:has-text("Comment")',
                'button:has-text("Post")',
                'button:has-text("Submit")',
                'button:has-text("Save")',
                '.submit-button',
                '#submit'
            ]
            
            for selector in button_selectors:
                btns = page.locator(selector)
                if btns.count() > 0:
                    print(f"👉 找到了按钮 [{selector}]，准备点击...")
                    btns.first.scroll_into_view_if_needed()
                    btns.first.click()
                    # 给提交一点时间反应（如果是假提交测试的话这里可以注释掉，现在实锤发出去）
                    page.wait_for_timeout(5000)
                    return True, "机器人自动识图填表并点击提交成功！"
                    
            return False, "填写了文字，但没有找到可以点击的提交按钮。"
        else:
            return False, "你在这个网站上还没有找到评论框。如果这是一个未登录论坛，可能是因为连你的真实浏览器也需要先手工注册。"
            
    except Exception as e:
        return False, f"尝试自动操作时发生异常: {str(e)}"

def process_task(task_row, api_row_index, manager, browser):
    url = task_row[manager.col_map['URL']]
    link_format = task_row[manager.col_map['Link_Format']]
    has_captcha = task_row[manager.col_map['Has_Captcha']]
    
    print(f"\n🚀 开始全自动处理任务行 {api_row_index} -> {url}")
    
    print("[1/3] 🤖 正在调用 AI 生成匹配当前环境的文案...")
    keywords = task_row[manager.col_map['Keywords']]
    if not keywords or keywords.strip() == "":
        keywords = analyze_keywords(MY_TARGET_WEBSITE)
    
    anchor_text = generate_anchor_text(keywords, link_format, MY_TARGET_WEBSITE)
    comment_content = generate_comment(anchor_text)
    
    print(f"[2/3] 🌐 开始尝试接管你的真实 Chrome 替你发帖...")
    # 相比原来这里不是新建一个上下文，而是使用接管过来的默认上下文
    context = browser.contexts[0]
    page = context.new_page()
    
    is_success, process_result_msg = auto_post_content(page, comment_content, url)
    print(f"🤖 全自动发帖结果报告: {process_result_msg}")
    
    # 养成好习惯做完了关掉单个标签页，免得你浏览器被塞爆
    page.close()
    
    print(f"[3/3] 📝 正在更新结果到 Google Sheets...")
    updates = {
        'Target_Website': MY_TARGET_WEBSITE,
        'Keywords': keywords,
        'Anchor_Text': anchor_text,
        'Comment_Content': comment_content,
        'Notes': process_result_msg
    }
    
    if is_success:
        updates['Status'] = 'completed'
        updates['Success_URL'] = url
    else:
        updates['Status'] = 'failed'
        
    manager.update_task(api_row_index, updates)
    return is_success

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
            
    if not today_tasks:
        print("🎉 今日无发布任务，已经让 daily_scheduler.py 罢工了。")
        return
        
    # =========== 这里是最核心的改变：连接模式 ===========
    print("🕸️ 正在尝试强行接管你的本地 Chrome 浏览器...")
    with sync_playwright() as p:
        try:
            # 连接到我们在 .command 脚本里打开的 9222 端口 Chrome
            browser_app = p.chromium.connect_over_cdp("http://localhost:9222")
            print("🤩 成功与你的本地真实 Chrome 建立脑机接口连接！你的指纹已生效。")
            
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
            
            # ========== 新增飞书通知机制 ==========
            from webhook_sender import create_webhook_sender
            sender = create_webhook_sender()
            if sender:
                summary = {"success": success_count, "failed": failed_count}
                sender.send_summary("🌍 外链自动化执行报告", summary)
            else:
                print("\nℹ️ 未配置飞书 Webhook (config.json 或 FEISHU_WEBHOOK_URL)，跳过发送飞书通知。")
                
        except Exception as e:
            print(f"❌ 接管本地浏览器失败 (是不是没通过那个黑框双击跑？): {e}")

if __name__ == '__main__':
    main()
