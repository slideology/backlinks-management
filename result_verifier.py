"""
result_verifier.py
==================
外链存活验证模块

功能：每周扫描所有被标记为 completed 的已发布外链，
确认页面中是否仍然存在我们的域名/链接，
避免"发出去却被版主悄悄删除"的情况默默损耗外链效果。
"""
import requests
from datetime import datetime
from gws_integration import GoogleSheetsManager

MY_DOMAIN = "bearclicker.net"

def verify_url_contains_link(success_url: str, target_domain: str = MY_DOMAIN) -> bool:
    """
    请求 success_url 对应的网页内容，
    检查正文 HTML 中是否还包含我们的网站域名。
    返回 True 表示外链还活着，False 表示已被删除。
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        resp = requests.get(success_url, headers=headers, timeout=15)
        return target_domain in resp.text
    except Exception as e:
        print(f"  ⚠️ 无法请求页面 {success_url}: {e}")
        return False  # 请求失败视为不确定，保守处理


def run_weekly_verification():
    """
    每周执行一次：扫描所有 completed 任务并验证外链还在不在。
    对确认已消失的外链，降级状态为 failed 并标注删除时间。
    """
    print("=" * 50)
    print("🔍 外链存活验证开始（每周运行）")
    print("=" * 50)
    
    manager = GoogleSheetsManager()
    all_data = manager.read_all_tasks()
    
    if len(all_data) <= 1:
        print("表格无数据。")
        return
    
    tasks = all_data[1:]
    status_idx = manager.col_map['Status']
    success_url_idx = manager.col_map.get('Success_URL')
    
    if success_url_idx is None:
        print("❌ 表格中未找到 Success_URL 列，无法验证。")
        return
    
    alive_count = 0
    dead_count = 0
    skip_count = 0
    
    for i, row in enumerate(tasks):
        row_status = row[status_idx] if len(row) > status_idx else ''
        if row_status != 'completed':
            continue
        
        success_url = row[success_url_idx] if len(row) > success_url_idx else ''
        if not success_url or not success_url.startswith('http'):
            skip_count += 1
            continue
        
        api_row_index = i + 1
        print(f"  查验行 {api_row_index}: {success_url[:60]}...", end=" ")
        
        is_alive = verify_url_contains_link(success_url)
        if is_alive:
            print("✅ 外链健在")
            alive_count += 1
        else:
            print("❌ 外链已消失，降级状态")
            dead_count += 1
            updates = {
                'Status': 'failed',
                'Notes': f'外链已被删除（验证于 {datetime.now().strftime("%Y-%m-%d")}）'
            }
            manager.update_task(api_row_index, updates)
    
    print(f"\n✅ 验证完成！存活: {alive_count} 条 | 已删: {dead_count} 条 | 跳过: {skip_count} 条")


if __name__ == '__main__':
    run_weekly_verification()
