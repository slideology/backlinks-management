import time
from playwright.sync_api import sync_playwright

from gws_integration import GoogleSheetsManager
from ai_generator import analyze_keywords, generate_anchor_text, generate_comment

# 我们的目标推广网址
MY_TARGET_WEBSITE = "https://bearclicker.net/"


def auto_post_content(page, comment_content, url):
    """
    一个【全自动尽力而为】的发帖机器人。
    它会尝试在网页里找输入框（textarea），填入内容并尝试点击按钮。
    由于各种网站格式千奇百怪，这个函数的成功率大概在 20%-30% 左右。
    """
    try:
        # 打开页面，设置 30 秒超时
        page.goto(url, timeout=30000)
        
        # 等待页面基本加载完毕（网络空闲）
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
            return False, "网页中没有找到明显的评论框 (Textarea)。需要注册登录或者被拦截。"
            
    except Exception as e:
        return False, f"尝试自动操作时发生异常或被验证码拦截: {str(e)}"


def process_task(task_row, api_row_index, manager, browser):
    """
    处理单条任务
    """
    url = task_row[manager.col_map['URL']]
    link_format = task_row[manager.col_map['Link_Format']]
    has_captcha = task_row[manager.col_map['Has_Captcha']]
    
    print(f"\n🚀 开始全自动处理任务行 {api_row_index} -> {url}")
    
    if has_captcha.lower() == 'yes':
        print("⚠️ 警告：该站点记录显示存在验证码，全自动脚本极有可能被拦截。")

    # 步骤 1：利用 AI 生成要发布的内容
    print("[1/3] 🤖 正在调用 AI 生成匹配当前环境的文案...")
    
    keywords = task_row[manager.col_map['Keywords']]
    if not keywords or keywords.strip() == "":
        keywords = analyze_keywords(MY_TARGET_WEBSITE)
    
    anchor_text = generate_anchor_text(keywords, link_format, MY_TARGET_WEBSITE)
    comment_content = generate_comment(anchor_text)
    
    # 步骤 2：开启无头模拟器访问并尝试【全自动发帖】
    print(f"[2/3] 🌐 正在全自动尝试发帖...")
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    
    is_success, process_result_msg = auto_post_content(page, comment_content, url)
    print(f"🤖 全自动发帖结果报告: {process_result_msg}")
    
    context.close()
    
    # 步骤 3：回写 Google Sheets 结果
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
        # 如果全自动失败了，我们就把它标记为 failed
        updates['Status'] = 'failed'
        
    manager.update_task(api_row_index, updates)


def main():
    print("=" * 50)
    print("🤖 外链自动化 - 🔥全自动🔥 发布模块启动")
    print("=" * 50)
    
    manager = GoogleSheetsManager()
    all_data = manager.read_all_tasks()
    
    if len(all_data) <= 1:
        print("表格无数据！")
        return
        
    status_idx = manager.col_map['Status']
    today_tasks = []
    
    # 筛选出被 scheduler 捞出来的 in_progress 任务
    for i, row in enumerate(all_data[1:]):
        status = row[status_idx] if len(row) > status_idx else ''
        if status == 'in_progress':
            today_tasks.append({
                'row_index': i + 1,
                'data': row
            })
            
    print(f"📦 找到 {len(today_tasks)} 个处于进行中 (in_progress) 状态的待执行任务。")
    if not today_tasks:
        print("🎉 今日无发布任务，请先运行 daily_scheduler.py 挑选任务。")
        return
        
    # 采用 headless=True (无头隐藏模式) 让它纯后台发不干扰你
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        for idx, task in enumerate(today_tasks, start=1):
            print(f"\n>>> 进度: {idx}/{len(today_tasks)}")
            process_task(task['data'], task['row_index'], manager, browser)
            
        browser.close()
        
    print("\n✅ 今日全自动任务已处理完毕！失败的任务已在备注中标明原因。")

if __name__ == '__main__':
    main()
