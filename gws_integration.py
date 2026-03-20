import os
import time
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from sheet_localization import GOOGLE_HEADERS, localize_basic_value, normalize_google_row

# --- 配置常量 ---
# 这两个从上一步 import_to_sheets.py 跑完后生成的配置里读取
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'sheets_config.txt')
TOKEN_FILE = os.path.join(os.path.dirname(__file__), 'token.json')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SHEET_RANGE = 'backlinks!A1:T' # 读取整张表 (A列到T列)

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
        self.headers = GOOGLE_HEADERS
        self.ensure_schema()
        # 定义列索引映射，方便程序通过名字而不是通过数字来访问数据
        self.col_map = {name: index for index, name in enumerate(self.headers)}

    def get_sheet_id(self):
        """获取第一个 sheet 的真实 ID (而不是硬编码为 0)"""
        spreadsheet = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        return spreadsheet['sheets'][0]['properties']['sheetId']

    def ensure_schema(self):
        """确保主表表头与代码中的列定义一致，必要时自动补列迁移。"""
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range="backlinks!A1:Z",
        ).execute()
        rows = result.get("values", [])
        if not rows:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=SHEET_RANGE,
                valueInputOption="RAW",
                body={"values": [self.headers]},
            ).execute()
            return

        current_headers = rows[0]
        if current_headers == self.headers:
            return

        header_map = {name: idx for idx, name in enumerate(current_headers)}
        migrated_rows = [self.headers]
        for row in rows[1:]:
            migrated_rows.append(
                [row[header_map[name]] if name in header_map and len(row) > header_map[name] else "" for name in self.headers]
            )

        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"backlinks!A1:T{len(migrated_rows)}",
            valueInputOption="RAW",
            body={"values": migrated_rows},
        ).execute()

    def read_all_tasks_raw(self):
        """读取整张表的所有数据（包括标题行）"""
        sheet = self.service.spreadsheets()
        result = sheet.values().get(
            spreadsheetId=self.spreadsheet_id, 
            range=SHEET_RANGE
        ).execute()
        return result.get('values', [])

    def read_all_tasks(self):
        """读取整张表数据，并把关键枚举字段规范化为程序内部使用的英文值。"""
        rows = self.read_all_tasks_raw()
        if len(rows) <= 1:
            return rows
        normalized_rows = [rows[0]]
        for row in rows[1:]:
            normalized_rows.append(normalize_google_row(row))
        return normalized_rows

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
            stored_value = localize_basic_value(col_name, value)
            
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
                    'rows': [{ 'values': [{'userEnteredValue': {'stringValue': str(stored_value)}}] }],
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
            for attempt in range(5):
                try:
                    self.service.spreadsheets().batchUpdate(
                        spreadsheetId=self.spreadsheet_id,
                        body={'requests': requests}
                    ).execute()
                    print(f"✅ 第 {row_index + 1} 行已更新：{updates}")
                    break
                except HttpError as exc:
                    if exc.resp.status != 429 or attempt == 4:
                        raise
                    wait_seconds = min(15 * (attempt + 1), 60)
                    print(f"⏳ Google Sheets 写入限流，{wait_seconds} 秒后重试第 {row_index + 1} 行...")
                    time.sleep(wait_seconds)

if __name__ == '__main__':
    # 简单的测试一下能不能连通
    print("正在测试 Google Sheets 连通性...")
    manager = GoogleSheetsManager()
    data = manager.read_all_tasks()
    print(f"成功连接！当前表格共读取到 {len(data)-1} 条外链任务。")
