from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from feishu_workbook import FeishuWorkbook
from legacy_feishu_history import extract_cell_text, extract_cell_url, normalize_source_url

LOG_PATTERN = re.compile(r"开始处理来源\s+(.*?)\s+->\s+站点\s+(.*)$")
ACTIVE_FAILURE_STATUSES = {"待重试", "未开始", "进行中"}


def _normalize_source_value(value) -> str:
    normalized = normalize_source_url(extract_cell_url(value) or extract_cell_text(value))
    if normalized:
        return normalized
    return str(extract_cell_text(value) or value or "").strip()


def _load_attempt_counts(log_path: Path) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    if not log_path.exists():
        return counts

    with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            matched = LOG_PATTERN.search(line)
            if not matched:
                continue
            source_url = normalize_source_url(matched.group(1).strip())
            site = matched.group(2).strip()
            if not source_url or not site:
                continue
            counts[(source_url, site)] += 1
    return counts


def build_repeated_unsuccessful_candidates(
    workbook: FeishuWorkbook,
    threshold: int = 5,
    log_path: str = "logs/launchd.stdout.log",
) -> list[dict]:
    _, rows = workbook.read_sheet_dicts("records", max_cols=30, max_rows=5000)
    attempt_counts = _load_attempt_counts(Path(log_path))

    candidates: list[dict] = []
    for row in rows:
        source_url = _normalize_source_value(row.get("来源链接"))
        site = str(row.get("目标站标识") or "").strip()
        status = str(row.get("状态") or "").strip()
        success_time = str(row.get("最近成功时间") or "").strip()
        attempts = attempt_counts.get((source_url, site), 0)

        if not source_url or not site:
            continue
        if success_time or status not in ACTIVE_FAILURE_STATUSES:
            continue
        if attempts < threshold:
            continue

        candidates.append(
            {
                "来源链接": source_url,
                "目标站点": site,
                "尝试次数": attempts,
                "当前状态": status,
                "最近失败分类": str(row.get("最近失败分类") or "").strip(),
                "最近失败原因": str(row.get("最近失败原因") or "").strip(),
            }
        )

    candidates.sort(
        key=lambda item: (
            -int(item["尝试次数"]),
            str(item["目标站点"]),
            str(item["来源链接"]),
        )
    )
    return candidates


def write_candidate_reports(
    candidates: list[dict],
    threshold: int,
    output_dir: str = "artifacts/source_audit",
) -> tuple[Path, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = f"threshold_{threshold}"
    json_path = directory / f"repeated_unsuccessful_candidates_{suffix}.json"
    csv_path = directory / f"repeated_unsuccessful_candidates_{suffix}.csv"

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "threshold": threshold,
        "count": len(candidates),
        "candidates": candidates,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = ["来源链接", "目标站点", "尝试次数", "当前状态", "最近失败分类", "最近失败原因"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(candidates)

    return json_path, csv_path


def main() -> None:
    parser = argparse.ArgumentParser(description="导出重试多次仍未成功的外链候选清单")
    parser.add_argument("--threshold", type=int, default=5, help="最小尝试次数阈值，默认 5")
    parser.add_argument("--log-path", default="logs/launchd.stdout.log", help="用于统计尝试次数的日志路径")
    parser.add_argument("--output-dir", default="artifacts/source_audit", help="清单输出目录")
    args = parser.parse_args()

    workbook = FeishuWorkbook.from_config()
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法生成重试候选清单。")

    candidates = build_repeated_unsuccessful_candidates(
        workbook,
        threshold=args.threshold,
        log_path=args.log_path,
    )
    json_path, csv_path = write_candidate_reports(candidates, args.threshold, output_dir=args.output_dir)
    print(f"✅ 已生成重试候选清单，共 {len(candidates)} 条")
    print(f"   JSON: {json_path}")
    print(f"   CSV:  {csv_path}")


if __name__ == "__main__":
    main()
