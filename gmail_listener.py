"""
gmail_listener.py
===================
Gmail 邮件验证码猎手

功能：当网站需要通过邮件验证（注册激活 / 验证码）时，
自动调用 Gmail API 获取最新的验证邮件，
并从中提取 4-8 位验证码或激活链接，交还给前台 Playwright 填入。
"""
import os
import re
import time
import base64
from datetime import datetime, timedelta


def _get_gmail_service():
    """初始化 Gmail API 服务"""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    
    token_path = os.path.join(os.path.dirname(__file__), 'token.json')
    creds = Credentials.from_authorized_user_file(token_path)
    
    service = build('gmail', 'v1', credentials=creds)
    return service


def _decode_email_body(payload) -> str:
    """递归解码邮件正文为纯文本"""
    if payload.get('body', {}).get('data'):
        data = payload['body']['data']
        return base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='ignore')
    
    parts = payload.get('parts', [])
    body_text = ''
    for part in parts:
        mime_type = part.get('mimeType', '')
        if mime_type in ['text/plain', 'text/html']:
            data = part.get('body', {}).get('data', '')
            if data:
                body_text += base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='ignore')
        elif 'parts' in part:
            body_text += _decode_email_body(part)
    
    return body_text


def extract_code_or_link(email_body: str) -> dict:
    """
    从邮件正文中提取验证码（4-8位数字）或激活链接。
    返回：{ "code": "123456" } 或 { "link": "https://..." } 或 {}
    """
    # 首先尝试找 6 位或 8 位数字验证码（最常见格式）
    code_patterns = [
        r'\b([0-9]{6})\b',   # 6 位数字
        r'\b([0-9]{8})\b',   # 8 位数字
        r'\b([0-9]{4})\b',   # 4 位数字
        r'\b([A-Za-z0-9]{6,8})\b',   # 6-8 位字母数字混合
    ]
    for pattern in code_patterns:
        match = re.search(pattern, email_body)
        if match:
            return {"code": match.group(1)}
    
    # 否则提取激活链接（包含 verify/activate/confirm 的 URL）
    link_pattern = r'https?://[^\s"\'<>]+(?:verif|activat|confirm|token)[^\s"\'<>]*'
    link_match = re.search(link_pattern, email_body, re.IGNORECASE)
    if link_match:
        return {"link": link_match.group(0)}
    
    return {}


def wait_for_verification_email(
    subject_keywords: list = None,
    sender_keywords: list = None,
    max_wait_seconds: int = 180,
    poll_interval: int = 5
) -> dict:
    """
    轮询 Gmail，等待并返回最新的验证邮件中的验证码或链接。
    
    参数：
    - subject_keywords: 邮件主题关键词列表（如 ["verify", "activation"]）
    - sender_keywords: 发件人关键词列表（如 ["noreply", "accounts"]）
    - max_wait_seconds: 最长等待秒数（默认 180 秒 = 3 分钟）
    - poll_interval: 轮询间隔（默认 5 秒）
    
    返回：
    - { "code": "123456" } 或 { "link": "https://..." } 或 {} (超时未找到)
    """
    subject_keywords = subject_keywords or ["verify", "activation", "confirm", "验证", "激活"]
    
    print(f"  📬 开始监听 Gmail 验证邮件（最多等待 {max_wait_seconds} 秒）...")
    
    try:
        service = _get_gmail_service()
    except Exception as e:
        print(f"  ❌ 无法初始化 Gmail 服务（是否已添加 gmail.readonly 权限？）: {e}")
        return {}
    
    start_time = time.time()
    search_after = (datetime.now() - timedelta(minutes=2)).strftime('%s')
    
    # 构建搜索查询
    query_parts = [f"after:{int(float(search_after))}"]
    if subject_keywords:
        query_parts.append(f"({' OR '.join(['subject:' + k for k in subject_keywords])})")
    query = ' '.join(query_parts)
    
    while time.time() - start_time < max_wait_seconds:
        try:
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=5
            ).execute()
            
            messages = results.get('messages', [])
            if messages:
                # 读取最新的一封
                msg = service.users().messages().get(
                    userId='me',
                    id=messages[0]['id'],
                    format='full'
                ).execute()
                
                email_body = _decode_email_body(msg['payload'])
                result = extract_code_or_link(email_body)
                
                if result:
                    if "code" in result:
                        print(f"  ✅ 成功提取到验证码：{result['code']}")
                    else:
                        print(f"  ✅ 成功提取到激活链接：{result['link'][:60]}...")
                    return result
            
            elapsed = int(time.time() - start_time)
            print(f"  ⏳ 还未找到验证邮件，已等待 {elapsed}s / {max_wait_seconds}s...")
            time.sleep(poll_interval)
            
        except Exception as e:
            print(f"  ⚠️ 轮询 Gmail 时发生错误: {e}")
            time.sleep(poll_interval)
    
    print(f"  ❌ 等待超时（{max_wait_seconds}s），未收到验证邮件。")
    return {}
