"""
Google Sheets 创建与数据导入脚本
功能：读取本地 Excel 文件，创建 Google Sheets，导入 226 条数据，应用格式规范
"""
import os
import openpyxl
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# === 配置 ===
EXCEL_FILE = '/Users/dahuang/Downloads/backlinks-resources-2026-03-01.xlsx'
CLIENT_SECRET = os.path.expanduser('~/.config/gws/client_secret.json')
TOKEN_FILE = os.path.join(os.path.dirname(__file__), 'token.json')
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]
SHEET_NAME = '外链管理总表 - Backlinks Management'


def get_credentials():
    """获取 Google 认证凭证"""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print("正在打开浏览器进行授权...")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=8080)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return creds


def read_excel_data(file_path):
    """读取 Excel 数据"""
    print(f"📂 读取 Excel 文件: {file_path}")
    wb = openpyxl.load_workbook(file_path)
    ws = wb.active
    data = []
    for row in ws.iter_rows(min_row=1, values_only=True):
        data.append(list(row))
    print(f"✅ 读取完成，共 {len(data) - 1} 条数据（不含标题）")
    return data


def build_full_header():
    """构建完整的 19 列表头（7列原始 + 12列新增管理字段）"""
    return [
        # 原始 7 列
        'ID', 'Type', 'URL', 'Discovered_From', 'Has_Captcha', 'Link_Strategy', 'Link_Format', 'Has_URL_Field',
        # 新增 11 列管理字段
        'Status', 'Priority', 'Target_Website', 'Keywords', 'Anchor_Text',
        'Comment_Content', 'Execution_Date', 'Success_URL', 'Notes', 'Last_Updated', 'Daily_Batch'
    ]


def build_rows_for_sheets(excel_data):
    """将 Excel 数据转为 Google Sheets 格式（加 ID 列 + 新增空列）"""
    header = build_full_header()
    rows = [header]

    for i, row in enumerate(excel_data[1:], start=1):  # 跳过 Excel 标题行
        # 原始 7 列数据
        original = [str(v) if v is not None else '' for v in row]
        # 新增字段：ID + 7 个原始列 + status/priority 默认值 + 其他空列
        full_row = (
            [str(i)]          # ID (自增)
            + original         # 原始 7 列
            + ['pending']      # Status 默认 pending
            + ['medium']       # Priority 默认 medium
            + [''] * 9         # 其他 9 个新增字段留空
        )
        rows.append(full_row)

    return rows


def create_spreadsheet(sheets_service, drive_service, title):
    """创建新的 Google Spreadsheet"""
    print(f"📝 正在创建新的 Google Sheets: {title}")
    spreadsheet = {
        'properties': {'title': title},
        'sheets': [{
            'properties': {
                'title': 'backlinks',
                'gridProperties': {'frozenRowCount': 1}  # 冻结标题行
            }
        }]
    }
    result = sheets_service.spreadsheets().create(body=spreadsheet).execute()
    spreadsheet_id = result['spreadsheetId']
    spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    print(f"✅ Spreadsheet 创建成功!")
    print(f"   ID: {spreadsheet_id}")
    print(f"   URL: {spreadsheet_url}")
    return spreadsheet_id, spreadsheet_url


def write_data_to_sheet(sheets_service, spreadsheet_id, rows):
    """将数据写入 Google Sheets"""
    print(f"📤 正在写入 {len(rows) - 1} 条数据到 Google Sheets...")

    # 写入全部数据
    body = {'values': rows}
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range='backlinks!A1',
        valueInputOption='RAW',
        body=body
    ).execute()
    print("✅ 数据写入完成！")


def apply_formatting(sheets_service, spreadsheet_id, sheet_id, total_rows):
    """应用格式：颜色、加粗、宽度、下拉框、条件格式"""
    print("🎨 正在应用格式...")

    requests = []

    # 1. 标题行加粗 + 背景色
    requests.append({
        'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1},
            'cell': {
                'userEnteredFormat': {
                    'textFormat': {'bold': True, 'fontSize': 10, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                    'backgroundColor': {'red': 0.2, 'green': 0.4, 'blue': 0.8},
                    'horizontalAlignment': 'CENTER',
                }
            },
            'fields': 'userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)'
        }
    })

    # 2. Status 列（I列，索引8）下拉验证
    requests.append({
        'setDataValidation': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': total_rows, 'startColumnIndex': 8, 'endColumnIndex': 9},
            'rule': {
                'condition': {
                    'type': 'ONE_OF_LIST',
                    'values': [
                        {'userEnteredValue': 'pending'},
                        {'userEnteredValue': 'in_progress'},
                        {'userEnteredValue': 'completed'},
                        {'userEnteredValue': 'failed'},
                        {'userEnteredValue': 'skipped'},
                    ]
                },
                'showCustomUi': True,
                'strict': True,
            }
        }
    })

    # 3. Priority 列（J列，索引9）下拉验证
    requests.append({
        'setDataValidation': {
            'range': {'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': total_rows, 'startColumnIndex': 9, 'endColumnIndex': 10},
            'rule': {
                'condition': {
                    'type': 'ONE_OF_LIST',
                    'values': [
                        {'userEnteredValue': 'high'},
                        {'userEnteredValue': 'medium'},
                        {'userEnteredValue': 'low'},
                    ]
                },
                'showCustomUi': True,
                'strict': True,
            }
        }
    })

    # 4. Status 条件格式 - pending 灰色
    requests.append({
        'addConditionalFormatRule': {
            'rule': {
                'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': total_rows, 'startColumnIndex': 8, 'endColumnIndex': 9}],
                'booleanRule': {
                    'condition': {'type': 'TEXT_EQ', 'values': [{'userEnteredValue': 'pending'}]},
                    'format': {'backgroundColor': {'red': 0.85, 'green': 0.85, 'blue': 0.85}}
                }
            }, 'index': 0
        }
    })

    # 5. Status 条件格式 - completed 绿色
    requests.append({
        'addConditionalFormatRule': {
            'rule': {
                'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': total_rows, 'startColumnIndex': 8, 'endColumnIndex': 9}],
                'booleanRule': {
                    'condition': {'type': 'TEXT_EQ', 'values': [{'userEnteredValue': 'completed'}]},
                    'format': {'backgroundColor': {'red': 0.7, 'green': 0.9, 'blue': 0.7}}
                }
            }, 'index': 1
        }
    })

    # 6. Status 条件格式 - failed 红色
    requests.append({
        'addConditionalFormatRule': {
            'rule': {
                'ranges': [{'sheetId': sheet_id, 'startRowIndex': 1, 'endRowIndex': total_rows, 'startColumnIndex': 8, 'endColumnIndex': 9}],
                'booleanRule': {
                    'condition': {'type': 'TEXT_EQ', 'values': [{'userEnteredValue': 'failed'}]},
                    'format': {'backgroundColor': {'red': 0.95, 'green': 0.7, 'blue': 0.7}}
                }
            }, 'index': 2
        }
    })

    # 7. 自动调整列宽
    requests.append({
        'autoResizeDimensions': {
            'dimensions': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 19}
        }
    })

    # 8. 开启筛选器
    requests.append({
        'setBasicFilter': {
            'filter': {
                'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': total_rows, 'startColumnIndex': 0, 'endColumnIndex': 19}
            }
        }
    })

    # 执行所有格式化操作
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={'requests': requests}
    ).execute()
    print("✅ 格式应用完成！")


def save_spreadsheet_id(spreadsheet_id, spreadsheet_url):
    """保存 Spreadsheet ID 到本地配置文件，供后续脚本使用"""
    config_file = os.path.join(os.path.dirname(__file__), 'sheets_config.txt')
    with open(config_file, 'w') as f:
        f.write(f"SPREADSHEET_ID={spreadsheet_id}\n")
        f.write(f"SPREADSHEET_URL={spreadsheet_url}\n")
    print(f"✅ Spreadsheet ID 已保存到 {config_file}")


def main():
    print("=" * 60)
    print("🚀 外链管理 Google Sheets 创建与数据导入工具")
    print("=" * 60)

    # Step 1: 认证
    print("\n[1/5] 获取 Google 认证...")
    creds = get_credentials()
    sheets_service = build('sheets', 'v4', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)

    # Step 2: 读取 Excel
    print("\n[2/5] 读取 Excel 数据...")
    excel_data = read_excel_data(EXCEL_FILE)

    # Step 3: 准备数据
    print("\n[3/5] 准备数据（添加 ID 列和管理字段）...")
    rows = build_rows_for_sheets(excel_data)
    print(f"   数据行数: {len(rows) - 1} 条（不含标题）")
    print(f"   总列数: {len(rows[0])} 列")

    # Step 4: 创建 Spreadsheet
    print(f"\n[4/5] 创建 Google Sheets...")
    spreadsheet_id, spreadsheet_url = create_spreadsheet(sheets_service, drive_service, SHEET_NAME)

    # Step 5: 写入数据
    print(f"\n[5/5] 写入数据并应用格式...")
    write_data_to_sheet(sheets_service, spreadsheet_id, rows)

    # 获取 sheetId（第一个 sheet）
    result = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_id = result['sheets'][0]['properties']['sheetId']
    apply_formatting(sheets_service, spreadsheet_id, sheet_id, len(rows))

    # 保存配置
    save_spreadsheet_id(spreadsheet_id, spreadsheet_url)

    print("\n" + "=" * 60)
    print("🎉 全部完成！")
    print(f"   📊 Google Sheets URL: {spreadsheet_url}")
    print(f"   📋 共导入 {len(rows) - 1} 条外链数据")
    print("=" * 60)


if __name__ == '__main__':
    main()
