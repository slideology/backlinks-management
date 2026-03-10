import time
import datetime
from gws_integration import GoogleSheetsManager

# 每日最大发帖限制，防止被当作垃圾信息屏蔽
DAILY_LIMIT = 5 

def main():
    print("=" * 50)
    print(f"📅 自动化外链发布每日调度系统 - {datetime.date.today()}")
    print("=" * 50)
    
    manager = GoogleSheetsManager()
    
    print("\n[1/3] 正在从 Google Sheets 拉取所有任务...")
    all_data = manager.read_all_tasks()
    
    if len(all_data) <= 1:
        print("❌ 表格中没有数据！")
        return
        
    headers = all_data[0]
    tasks = all_data[1:] # 剔除第一行标题
    
    # 获取列对应的索引
    status_idx = manager.col_map['Status']
    priority_idx = manager.col_map['Priority']
    
    pending_tasks = []
    
    # 注意：我们的行索引需要 +1（因为去掉了标题行），然后再传给 Google API
    for i, row in enumerate(tasks):
        # 处理空列表（API 返回的结尾空列会截断）
        row_status = row[status_idx] if len(row) > status_idx else 'pending'
        row_priority = row[priority_idx] if len(row) > priority_idx else 'medium'
        
        if row_status == 'pending':
            # 保存任务并带上真实的行索引 (i+1 是实际数据行，在 API 里标题行是 index 0，数据行是从 index 1 开始)
            # 在 API 里： 第一行标题 index=0，第一条数据 index=1
            api_row_index = i + 1 
            pending_tasks.append({
                'row_index': api_row_index,
                'priority': row_priority,
                'data': row
            })
            
    print(f"📊 发现待处理任务：{len(pending_tasks)} 个。")
    
    if len(pending_tasks) == 0:
        print("✅ 所有外链任务已完成！系统无需运行。")
        return
        
    print("\n[2/3] 正在根据优先级自动挑选今日任务...")
    
    # 按照优先级排序：high > medium > low
    # 假设如果表格里为空，给它一个默认的排序权重
    priority_weight = {'high': 1, 'medium': 2, 'low': 3}
    pending_tasks.sort(key=lambda x: priority_weight.get(x['priority'].lower(), 2))
    
    # 挑选出当天的份额
    today_tasks = pending_tasks[:DAILY_LIMIT]
    print(f"🎯 为今天挑选了 {len(today_tasks)} 个任务 (配额限制 {DAILY_LIMIT})")
    
    print("\n[3/3] 正在将今日任务状态标记为进行中，分配批次号...")
    batch_token = f"Batch-{datetime.date.today().strftime('%Y%m%d')}"
    
    for task in today_tasks:
        idx = task['row_index']
        # 核心更新：把状态由 pending 改为 in_progress，并登记当天的批次号
        updates = {
            'Status': 'in_progress',
            'Daily_Batch': batch_token,
            'Execution_Date': datetime.date.today().strftime('%Y-%m-%d')
        }
        manager.update_task(idx, updates)
        time.sleep(1) # 休眠以免触发 Google API 并发限制
        
    print("\n✨ 调度完成！请进入你的 Google Sheets 查看状态变为黄颜色的就是今天的猎物！")

if __name__ == '__main__':
    main()
