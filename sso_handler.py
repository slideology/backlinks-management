"""
sso_handler.py
==============
单点登录（SSO）突破模块

功能：检测网页是否存在 "Sign in with Google" 快捷登录按钮，
并模拟使用本地已登录的 Google 账号自动完成登录。
这样对于需要登录才能评论的网站，可以无需密码直接一键进入。
"""
import time
from playwright.sync_api import Page


def detect_and_do_google_sso(page: Page, timeout_ms: int = 3000) -> bool:
    """
    检测页面上是否有 Google 快捷登录按钮，如果有则尝试点击完成登录。
    
    返回值：
    - True：成功触发 Google 登录弹框（不代表最终登录成功，需要上层函数再验证）
    - False：未发现 Google 快捷登录入口
    """
    google_sso_selectors = [
        # 文字匹配（各网站措辞不同）
        'button:has-text("Sign in with Google")',
        'button:has-text("Continue with Google")',
        'button:has-text("Login with Google")',
        'a:has-text("Sign in with Google")',
        'a:has-text("Continue with Google")',
        # Class/ID 匹配（常见第三方登录库）
        '.google-login-button',
        '#google-login',
        '[data-provider="google"]',
        # 图标 alt 匹配
        'img[alt*="Google"][role="button"]',
        # 包含 Google OAuth 的 iframe 背后的按钮（部分使用 popup 弹窗）
        'div[data-type="google"]',
    ]
    
    for selector in google_sso_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=timeout_ms):
                print(f"  🔑 发现 Google 快捷登录入口 [{selector}]，准备点击...")
                context = page.context
                popup = None
                try:
                    with context.expect_page(timeout=5000) as new_page_info:
                        btn.click()
                    popup = new_page_info.value
                except Exception:
                    btn.click()
                time.sleep(2)
                
                # 尝试检测并点击 Google 账号选择弹框
                # Google 的账号选择弹框通常会打开一个新标签页或弹窗
                return _handle_google_account_selection(page, popup)
        except:
            continue
    
    return False


def _handle_google_account_selection(page: Page, popup: Page = None) -> bool:
    """
    内部函数：处理 Google 账号选择弹框。
    因为本地 Chrome 已登录 Google，找到并点击默认账号完成授权。
    """
    try:
        if popup is None:
            context = page.context
            with context.expect_page(timeout=5000) as new_page_info:
                pass
            popup = new_page_info.value
        popup.wait_for_load_state('networkidle', timeout=10000)
        
        # 在 Google 账号选择页面上，找到并点击第一个可用账号
        account_selectors = [
            '[data-identifier]',        # 账号选择行
            '.account-list-item',
            '[aria-label*="@"]',        # 含邮箱 aria-label 的选项
        ]
        
        for sel in account_selectors:
            try:
                acc = popup.locator(sel).first
                if acc.is_visible(timeout=2000):
                    print(f"  ✅ 找到 Google 账号，正在点击授权...")
                    acc.click()
                    time.sleep(3)  # 等待授权完成
                    return True
            except:
                continue
        
        # 如果找不到明确的账号选项，尝试点击"继续"按钮
        continue_btns = [
            'button:has-text("Continue")',
            'button:has-text("Allow")',
        ]
        for sel in continue_btns:
            try:
                btn = popup.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    time.sleep(3)
                    return True
            except:
                continue
        
        print("  ⚠️ 未能在 Google 弹窗中找到可点击的账号选项。")
        return False
        
    except Exception as e:
        print(f"  ⚠️ Google SSO 弹窗处理失败: {e}")
        return False
