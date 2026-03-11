"""
Google OAuth 认证脚本
运行此脚本会打开浏览器授权页面，完成后在当前目录生成 token.json
"""
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# 需要的 API 权限范围
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',  # Google Sheets 读写
    'https://www.googleapis.com/auth/drive',          # Google Drive（创建文件）
    'https://www.googleapis.com/auth/gmail.readonly', # Gmail 只读（用于提取注册验证码）
]

# client_secret.json 路径
CLIENT_SECRET = os.path.expanduser('~/.config/gws/client_secret.json')
TOKEN_FILE = os.path.join(os.path.dirname(__file__), 'token.json')


def authenticate():
    """执行 OAuth 认证流程，返回认证凭证"""
    creds = None

    # 如果已有 token，直接加载
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # 如果 token 无效或不存在，重新认证
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("正在刷新访问令牌...")
            creds.refresh(Request())
        else:
            print("正在打开浏览器进行授权，请在浏览器中完成登录...")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)

        # 保存 token 到本地
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
        print(f"✅ 认证成功！凭证已保存到 {TOKEN_FILE}")
    else:
        print("✅ 已有有效凭证，无需重新认证")

    return creds


if __name__ == '__main__':
    creds = authenticate()
    print("\n认证信息:")
    print(f"  - Token 文件: {TOKEN_FILE}")
    print(f"  - 授权范围: {len(SCOPES)} 个")
    print("\n🎉 可以开始运行 import_to_sheets.py 了！")
