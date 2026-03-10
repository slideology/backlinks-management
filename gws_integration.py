import os
import time
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# --- 配置常量 ---
# 这两个从上一步 import_to_sheets.py 跑完后生成的配置里读取
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'sheets_config.txt')
TOKEN_FILE = os.path.join(os.path.dirname(__file__), 'token.json')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SHEET_RANGE = 'backlinks!A1:S' # 读取整张表 (A列到S列)

def get_spreadsheet_id():
    """从本地配置文件读取 Spreadsheet ID"""
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"找不到配置文件 {CONFIG_FILE}，请先运行 import_to_sheets.py")
    
    with open(CONFIG_FILE, 'r') as f:
        for line in f:
            if line.startswith('SPREADSHEET_ID='):
                return line.strip().split('=')[1]
    raise ValueError("配置文件中缺少 SPREADSHEET_ID")

def get_sheets_service():
    """获取 Google Sheets API 服务客户端"""
    if not os.path.exists(TOKEN_FILE):
        raise FileNotFoundError("找不到授权文件 token.json，请先运行 import_to_sheets.py 认证")
    
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build('sheets', 'v4', credentials=creds)

class GoogleSheetsManager:
    """管理和操作外链 Google Sheets 的核心类"""
    
    def __init__(self):
        self.service = get_sheets_service()
        self.spreadsheet_id = get_spreadsheet_id()
        self.sheet_id = self.get_sheet_id()
        # 定义列索引映射，方便程序通过名字而不是通过数字来访问数据
        self.col_map = {
            'ID': 0, 'Type': 1, 'URL': 2, 'Discovered_From': 3, 'Has_Captcha': 4,
            'Link_Strategy': 5, 'Link_Format': 6, 'Has_URL_Field': 7, 'Status': 8,
            'Priority': 9, 'Target_Website': 10, 'Keywords': 11, 'Anchor_Text': 12,
            'Comment_Content': 13, 'Execution_Date': 14, 'Success_URL': 15, 'Notes': 16,
            'Last_Updated': 17, 'Daily_Batch': 18
        }

    def get_sheet_id(self):
        """获取第一个 sheet 的真实 ID (而不是硬编码为 0)"""
        spreadsheet = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        return spreadsheet['sheets'][0]['properties']['sheetId']

    def read_all_tasks(self):
        """读取整张表的所有数据（包括标题行）"""
        sheet = self.service.spreadsheets()
        result = sheet.values().get(
            spreadsheetId=self.spreadsheet_id, 
            range=SHEET_RANGE
        ).execute()
        return result.get('values', [])

    def update_task(self, row_index, updates):
        """
        更新某一行(row)的具体字段
        row_index: 行号（比如第5行对应表格里的真实行数，注意减去标题行偏移）
        updates: 字典形式 { 'Status': 'completed', 'Notes': '成功' }
        """
        requests = []
        for col_name, value in updates.items():
            if col_name not in self.col_map:
                print(f"警告：未知的列名 '{col_name}'")
                continue
                
            col_idx = self.col_map[col_name]
            
            # 使用 update cells 接口精确定位我们要修改的那一个格子
            requests.append({
                'updateCells': {
                    'range': {
                        'sheetId': self.sheet_id, 
                        'startRowIndex': row_index,       # Google API 是 0 索引
                        'endRowIndex': row_index + 1,
                        'startColumnIndex': col_idx,
                        'endColumnIndex': col_idx + 1
                    },
                    'rows': [{ 'values': [{'userEnteredValue': {'stringValue': str(value)}}] }],
                    'fields': 'userEnteredValue'
                }
            })
            
            # 自动维护 Last_Updated 字段
            if col_name != 'Last_Updated':
                last_update_idx = self.col_map['Last_Updated']
                current_time = time.strftime('%Y-%m-%d %H:%M:%S')
                requests.append({
                    'updateCells': {
                        'range': {
                            'sheetId': self.sheet_id,
                            'startRowIndex': row_index,
                            'endRowIndex': row_index + 1,
                            'startColumnIndex': last_update_idx,
                            'endColumnIndex': last_update_idx + 1
                        },
                        'rows': [{ 'values': [{'userEnteredValue': {'stringValue': current_time}}] }],
                        'fields': 'userEnteredValue'
                    }
                })

        if requests:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={'requests': requests}
            ).execute()
            print(f"✅ 第 {row_index + 1} 行已更新：{updates}")

if __name__ == '__main__':
    # 简单的测试一下能不能连通
    print("正在测试 Google Sheets 连通性...")
    manager = GoogleSheetsManager()
    data = manager.read_all_tasks()
    print(f"成功连接！当前表格共读取到 {len(data)-1} 条外链任务。")
