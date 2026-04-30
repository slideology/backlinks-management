from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


DEFAULT_INPUT = "artifacts/page_probe/source_probe_results.json"
DEFAULT_OUTPUT_JSON = "artifacts/page_probe/worth_no_manual_review.json"
DEFAULT_OUTPUT_CSV = "artifacts/page_probe/worth_no_manual_review.csv"
DEFAULT_SUMMARY_JSON = "artifacts/page_probe/worth_no_manual_review_summary.json"


DIRECT_CLEAN_REASON_PATTERNS = (
    "dom 与页面文本均未发现评论区线索",
    "未发现评论区",
    "页面提示评论已关闭",
)
LOGIN_REASON_PATTERNS = (
    "必须有账号才能评论",
    "必须登录后才能评论",
    "page_text_login_wall",
    "登录墙",
)
CHALLENGE_REASON_PATTERNS = (
    "cloudflare",
    "验证码",
    "captcha",
    "challenge",
)


def load_rows(input_path: Path) -> list[dict]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("source_probe_results.json 格式异常，预期为 list。")
    return payload


def classify_worth_no_row(row: dict) -> dict:
    reason = str(row.get("页面探测失败原因", "") or "").strip()
    reason_lower = reason.lower()

    if any(pattern in reason_lower for pattern in DIRECT_CLEAN_REASON_PATTERNS):
        return {
            "原因大类": "无评论区/评论关闭",
            "建议动作": "直接清理",
            "建议说明": "页面本身没有可用评论区，继续进入待发池价值很低。",
        }

    if any(pattern in reason_lower for pattern in LOGIN_REASON_PATTERNS):
        return {
            "原因大类": "必须登录后才能评论",
            "建议动作": "登录冻结",
            "建议说明": "先从日常调度池冻结，后续若专门维护登录态再单独处理。",
        }

    if any(pattern in reason_lower for pattern in CHALLENGE_REASON_PATTERNS):
        return {
            "原因大类": "验证码/Cloudflare 挑战",
            "建议动作": "验证码灰区",
            "建议说明": "不建议日常反复重试，可放入灰区池低频复查。",
        }

    return {
        "原因大类": "其他非值得发帖",
        "建议动作": "人工复核",
        "建议说明": "原因不属于明确清理/冻结/验证码灰区，建议人工再看一遍。",
    }


def build_manual_review_rows(rows: list[dict]) -> list[dict]:
    reviewed = []
    for row in rows:
        if str(row.get("是否值得发帖", "") or "").strip() != "否":
            continue
        classification = classify_worth_no_row(row)
        reviewed.append(
            {
                "来源链接": row.get("来源链接", ""),
                "页面探测状态": row.get("页面探测状态", ""),
                "页面探测时间": row.get("页面探测时间", ""),
                "是否值得发帖": row.get("是否值得发帖", ""),
                "评论区是否存在": row.get("评论区是否存在", ""),
                "是否需要登录": row.get("是否需要登录", ""),
                "是否支持Google登录": row.get("是否支持Google登录", ""),
                "最终链接格式": row.get("最终链接格式", ""),
                "推荐策略": row.get("推荐策略", ""),
                "页面探测失败原因": row.get("页面探测失败原因", ""),
                **classification,
            }
        )
    reviewed.sort(
        key=lambda item: (
            item.get("建议动作", ""),
            item.get("原因大类", ""),
            item.get("来源链接", ""),
        )
    )
    return reviewed


def write_csv(output_path: Path, rows: list[dict]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "来源链接",
        "页面探测状态",
        "页面探测时间",
        "是否值得发帖",
        "评论区是否存在",
        "是否需要登录",
        "是否支持Google登录",
        "最终链接格式",
        "推荐策略",
        "页面探测失败原因",
        "原因大类",
        "建议动作",
        "建议说明",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(output_path: Path, payload) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def summarize_rows(rows: list[dict]) -> dict:
    return {
        "total_rows": len(rows),
        "建议动作分布": dict(Counter(row.get("建议动作", "") for row in rows)),
        "原因大类分布": dict(Counter(row.get("原因大类", "") for row in rows)),
    }


def run(
    input_file: str = DEFAULT_INPUT,
    output_json: str = DEFAULT_OUTPUT_JSON,
    output_csv: str = DEFAULT_OUTPUT_CSV,
    summary_json: str = DEFAULT_SUMMARY_JSON,
) -> dict:
    rows = load_rows(Path(input_file))
    reviewed = build_manual_review_rows(rows)
    summary = summarize_rows(reviewed)
    write_json(Path(output_json), reviewed)
    write_csv(Path(output_csv), reviewed)
    write_json(Path(summary_json), summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出“是否值得发帖=否”的人工复核清单。")
    parser.add_argument("--input-file", default=DEFAULT_INPUT)
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-csv", default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--summary-json", default=DEFAULT_SUMMARY_JSON)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run(
        input_file=args.input_file,
        output_json=args.output_json,
        output_csv=args.output_csv,
        summary_json=args.summary_json,
    )
    print("🧾 非值得发帖人工复核清单已生成")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
