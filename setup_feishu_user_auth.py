import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from feishu_integration import create_feishu_client


CALLBACK_RESULT = {"code": None, "state": None, "error": None}


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        CALLBACK_RESULT["code"] = query.get("code", [None])[0]
        CALLBACK_RESULT["state"] = query.get("state", [None])[0]
        CALLBACK_RESULT["error"] = query.get("error", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if CALLBACK_RESULT["code"]:
            self.wfile.write("飞书授权成功，可以关闭这个页面了。".encode("utf-8"))
        else:
            self.wfile.write("飞书授权失败，请回到终端查看错误。".encode("utf-8"))

    def log_message(self, format, *args):
        return


def update_config_for_user_mode(spreadsheet_token: str, sheet_id: str, config_path: str = "config.json"):
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    config.setdefault("feishu", {})
    config["feishu"]["enabled"] = True
    config["feishu"]["auth_mode"] = "user"
    config["feishu"]["redirect_uri"] = "http://127.0.0.1:8787/callback"
    config["feishu"]["user_token_file"] = ".feishu_user_token.json"
    config["feishu"]["spreadsheet_token"] = spreadsheet_token
    config["feishu"]["sheet_id"] = sheet_id

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
        f.write("\n")


def main():
    client = create_feishu_client()
    if not client:
        raise SystemExit("未能初始化飞书客户端，请先检查 config.json 中的 app_id/app_secret。")

    auth_url, expected_state = client.get_authorization_url()
    server = HTTPServer(("127.0.0.1", 8787), CallbackHandler)
    server.timeout = 300

    print("正在启动本地回调服务: http://127.0.0.1:8787/callback")
    print("即将打开飞书授权页，请使用你当前登录的飞书账号确认授权。")
    print(auth_url)

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    webbrowser.open(auth_url)
    thread.join(timeout=300)
    server.server_close()

    if CALLBACK_RESULT["error"]:
        raise SystemExit(f"飞书授权失败: {CALLBACK_RESULT['error']}")
    if not CALLBACK_RESULT["code"]:
        raise SystemExit("未收到飞书授权回调，请重试。")
    if CALLBACK_RESULT["state"] != expected_state:
        raise SystemExit("飞书授权 state 校验失败，请重试。")

    client.exchange_code_for_user_token(CALLBACK_RESULT["code"])
    spreadsheet = client.create_spreadsheet("Codex Backlink User Sheet", as_user=True)
    spreadsheet_token = spreadsheet["spreadsheet_token"]
    sheet_id = client.get_sheet_id_by_token(spreadsheet_token, as_user=True)
    update_config_for_user_mode(spreadsheet_token, sheet_id)

    print("飞书用户授权成功。")
    print(f"新表格: {spreadsheet.get('url')}?sheet={sheet_id}")
    print("config.json 已切换为用户身份模式。")


if __name__ == "__main__":
    main()
