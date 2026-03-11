import time
import json
import datetime
from gws_integration import GoogleSheetsManager


def load_config(config_path="config.json"):
    """读取 config.json 中的配置，若文件不存在或缺失字段则使用缺省值"""
    defaults = {
        "scheduler": {
            "daily_limit": 5,
            "priority_order": ["high", "medium", "low"],
            "retry_after_days": 3
        }
    }
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        # 合并到默认值字典（确保缺少字段时有兜底）
        merged = {**defaults, **config}
        merged["scheduler"] = {**defaults["scheduler"], **config.get("scheduler", {})}
        return merged
    except:
        return defaults


def main():
    config = load_config()
    scheduler_cfg = config["scheduler"]
    DAILY_LIMIT = scheduler_cfg["daily_limit"]
    RETRY_AFTER_DAYS = scheduler_cfg["retry_after_days"]
    PRIORITY_WEIGHT = {p: i for i, p in enumerate(scheduler_cfg["priority_order"])}
    
    print("=" * 50)
    print(f"📅 自动化外链发布每日调度系统 - {datetime.date.today()}")
    print(f"   每日配额: {DAILY_LIMIT} 条 | 失败重试间隔: {RETRY_AFTER_DAYS} 天")
    print("=" * 50)
    
    manager = GoogleSheetsManager()
    
    print("\n[1/3] 正在从 Google Sheets 拉取所有任务...")
    all_data = manager.read_all_tasks()
    
    if len(all_data) <= 1:
        print("❌ 表格中没有数据！")
        return
    
    tasks = all_data[1:]  # 剔除标题行
    
    status_idx = manager.col_map['Status']
    priority_idx = manager.col_map['Priority']
    
    today = datetime.date.today()
    candidate_tasks = []
    
    for i, row in enumerate(tasks):
        row_status = row[status_idx] if len(row) > status_idx else 'pending'
        row_priority = row[priority_idx] if len(row) > priority_idx else 'medium'
        api_row_index = i + 1
        
        # 规则 1：pending 状态的任务直接纳入候选
        if row_status == 'pending':
            candidate_tasks.append({
                'row_index': api_row_index,
                'priority': row_priority,
                'data': row,
                'type': 'pending'
            })
        
        # 规则 2：failed 状态且超过 retry_after_days 天的任务也重新纳入候选
        elif row_status == 'failed':
            retry_at_col = manager.col_map.get('retry_at')
            if retry_at_col is not None:
                retry_at_str = row[retry_at_col] if len(row) > retry_at_col else ''
                if retry_at_str:
                    try:
                        retry_at_date = datetime.date.fromisoformat(retry_at_str)
                        if today >= retry_at_date:
                            candidate_tasks.append({
                                'row_index': api_row_index,
                                'priority': row_priority,
                                'data': row,
                                'type': 'retry'
                            })
                    except:
                        pass
                else:
                    # 没有 retry_at 的旧 failed 任务，也纳入
                    candidate_tasks.append({
                        'row_index': api_row_index,
                        'priority': row_priority,
                        'data': row,
                        'type': 'retry'
                    })
    
    pending_count = sum(1 for t in candidate_tasks if t['type'] == 'pending')
    retry_count = sum(1 for t in candidate_tasks if t['type'] == 'retry')
    print(f"📊 候选任务：{len(candidate_tasks)} 个（新任务 {pending_count} 个，重试任务 {retry_count} 个）")
    
    if not candidate_tasks:
        print("✅ 今日无可处理任务！")
        return
    
    print("\n[2/3] 正在根据优先级自动挑选今日任务...")
    # 排序：优先 pending > retry，同类型内按 priority 权重排序
    candidate_tasks.sort(key=lambda x: (
        0 if x['type'] == 'pending' else 1,  # pending 优先于 retry
        PRIORITY_WEIGHT.get(x['priority'].lower(), 99)
    ))
    
    today_tasks = candidate_tasks[:DAILY_LIMIT]
    print(f"🎯 为今天挑选了 {len(today_tasks)} 个任务（配额限制 {DAILY_LIMIT}）")
    
    print("\n[3/3] 正在将今日任务状态标记为进行中，分配批次号...")
    batch_token = f"Batch-{today.strftime('%Y%m%d')}"
    
    for task in today_tasks:
        idx = task['row_index']
        updates = {
            'Status': 'in_progress',
            'Daily_Batch': batch_token,
            'Execution_Date': today.strftime('%Y-%m-%d')
        }
        manager.update_task(idx, updates)
        time.sleep(1)
    
    print("\n✨ 调度完成！Google Sheets 中黄色高亮的行就是今天的目标！")


if __name__ == '__main__':
    main()
