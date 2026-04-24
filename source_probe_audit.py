from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from browser_cdp import ensure_allowed_cdp_url
from backlink_state import (
    EXECUTION_MODE_CLASSIC,
    PROBE_STATUS_COMPLETED,
    PROBE_STATUS_FAILED,
    PROBE_STATUS_PENDING,
    PROBE_STATUS_SKIPPED,
    PROBE_WORTH_NO,
    PROBE_WORTH_REVIEW,
    PROBE_WORTH_YES,
    TARGET_SITE_HEADERS,
    dynamic_source_headers,
)
from feishu_workbook import FeishuWorkbook
from form_automation_local import _preprobe_page_for_generation
from legacy_feishu_history import extract_cell_text, extract_cell_url, normalize_source_url
from source_format_backfill import (
    build_source_probe_update,
    load_json,
    open_probe_browser,
    read_target_rows,
    save_json,
    should_retry_write,
)
from source_history_audit import (
    audit_single_page,
    build_history_audit_update,
    extract_active_target_domains,
)
from sync_reporting_workbook import _normalize_excluded_domains, _normalize_excluded_urls
from website_format_detector import WebsiteFormatDetector


DEFAULT_CONFIG = {
    "state_file": "artifacts/page_probe/state.json",
    "summary_file": "artifacts/page_probe/summary.json",
    "report_json_file": "artifacts/page_probe/source_probe_results.json",
    "report_csv_file": "artifacts/page_probe/source_probe_results.csv",
    "connect_cdp_url": "http://127.0.0.1:9666",
    "page_load_timeout_ms": 15000,
    "single_page_timeout_seconds": 120,
    "read_page_size": 250,
    "write_retry_count": 5,
    "write_retry_base_seconds": 2,
    "confidence_threshold": 0.8,
    "enable_vision": True,
    "isolated_probe_worker": True,
    "worker_poll_interval_seconds": 10,
    "worker_timeout_buffer_seconds": 20,
}


class ProbePageTimeoutError(TimeoutError):
    pass


@contextmanager
def probe_page_timeout_guard(seconds: int):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handle_timeout(_signum, _frame):
        raise ProbePageTimeoutError(f"页面探测超时（>{seconds}秒）")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def load_probe_task_config(config_path: str = "config.json") -> dict:
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_CONFIG)
    merged = {**DEFAULT_CONFIG, **payload.get("page_probe", {})}
    browser_cfg = payload.get("browser", {}) or {}
    merged["connect_cdp_url"] = str(browser_cfg.get("connect_cdp_url", "") or merged["connect_cdp_url"])
    merged["excluded_source_domains"] = list((payload.get("reporting_workbook", {}) or {}).get("excluded_source_domains", []) or [])
    merged["excluded_source_urls"] = list((payload.get("reporting_workbook", {}) or {}).get("excluded_source_urls", []) or [])
    return merged


def read_source_rows_paged(
    workbook: FeishuWorkbook,
    max_cols: int = 250,
    page_size: int = 250,
) -> list[dict]:
    deduped = {}
    selected_headers = [
        "来源链接",
        "页面探测状态",
        "页面探测时间",
        "是否值得发帖",
        "页面探测失败原因",
        "推荐策略",
    ]
    for row_index, row in workbook.iter_sheet_selected_rows(
        "sources",
        selected_headers=selected_headers,
        max_cols=max_cols,
        page_size=page_size,
    ):
        normalized_url = normalize_source_url(
            extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", ""))
        )
        if normalized_url:
            deduped[normalized_url] = {
                **row,
                "_row_index": row_index,
            }
    return list(deduped.values())


def write_source_row_with_retry(
    workbook: FeishuWorkbook,
    headers: list[str],
    row_index: int,
    row: dict,
    max_retries: int,
    base_sleep: float,
) -> None:
    for attempt in range(max_retries):
        try:
            workbook.write_sheet_partial_row("sources", headers, row_index, row)
            return
        except Exception as exc:
            if attempt >= max_retries - 1 or not should_retry_write(exc):
                raise
            time.sleep(base_sleep * (attempt + 1))


def _probe_failed_capability(reason: str) -> dict:
    return {
        "static_result": {
            "recommended_format": "unknown",
            "evidence_type": "probe_failed",
            "confidence": 0,
        },
        "final_result": {
            "recommended_format": "unknown",
            "evidence_type": "probe_failed",
            "confidence": 0,
        },
        "stage": "static_only",
        "vision_used": False,
        "status": "failed",
        "runtime_result": None,
        "vision_result": {"reason": reason},
    }


def build_probe_timeout_bundle(reason: str) -> tuple[dict, dict, dict]:
    message = reason or "页面探测超时"
    audit_result = {
        "status": "failed",
        "requires_login": "",
        "login_evidence": "",
        "supports_google_login": "",
        "google_login_evidence": "",
        "has_comment_area": "",
        "comment_evidence": "",
        "historical_presence": "",
        "historical_presence_evidence": message,
    }
    capability = _probe_failed_capability(message)
    preprobe_meta = {
        "ok": False,
        "message": "页面预探测超时",
        "diagnosis": message,
        "diagnostic_category": "preprobe_timeout",
        "recommended_strategy": "dom",
    }
    return audit_result, capability, preprobe_meta


def _current_python_bin() -> str:
    return sys.executable


def _url_matches_exclusions(url: str, excluded_domains: set[str], excluded_urls: set[str]) -> bool:
    normalized = normalize_source_url(url)
    if not normalized:
        return False
    if normalized in excluded_urls:
        return True
    hostname = (urlparse(normalized).hostname or "").strip().lower()
    if not hostname:
        return False
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in excluded_domains)


def _choose_recommended_strategy(existing_strategy: str, preprobe_meta: dict, capability: dict) -> str:
    preprobe_strategy = str(preprobe_meta.get("recommended_strategy", "") or "").strip().lower()
    if preprobe_strategy in {"dom", "iframe", "vision", "skip"}:
        return preprobe_strategy

    evidence = str(capability.get("final_result", {}).get("evidence_type", "") or "").lower()
    if any(marker in evidence for marker in ("iframe", "blogger", "disqus", "comment_frame")):
        return "iframe"

    current = str(existing_strategy or "").strip().lower()
    if current in {"dom", "iframe", "vision", "skip"}:
        return current
    return "dom"


def _cleanup_probe_context_pages(context, primary_page, expected_url: str) -> None:
    expected_host = (urlparse(expected_url).hostname or "").strip().lower()
    for candidate in list(getattr(context, "pages", []) or []):
        if candidate == primary_page:
            continue
        try:
            candidate_url = str(getattr(candidate, "url", "") or "").strip()
            candidate_host = (urlparse(candidate_url).hostname or "").strip().lower()
            if not candidate_url or candidate_url == "about:blank":
                candidate.close()
                continue
            # The probe only needs one top-level page; close cross-site popups/tabs
            # such as social shares or consent helpers that get opened accidentally.
            if candidate_host and candidate_host != expected_host:
                candidate.close()
        except Exception:
            continue


def _build_probe_worker_payload(
    row: dict,
    normalized_url: str,
    config: dict,
    excluded_domains: set[str],
    excluded_urls: set[str],
    target_domains: list[str],
) -> dict:
    return {
        "row": row,
        "normalized_url": normalized_url,
        "config": config,
        "excluded_domains": sorted(excluded_domains),
        "excluded_urls": sorted(excluded_urls),
        "target_domains": list(target_domains),
    }


def _run_probe_worker(task_payload: dict) -> dict:
    config = dict(task_payload["config"])
    row = dict(task_payload["row"])
    normalized_url = str(task_payload["normalized_url"])
    excluded_domains = set(task_payload.get("excluded_domains", []))
    excluded_urls = set(task_payload.get("excluded_urls", []))
    target_domains = list(task_payload.get("target_domains", []))

    detector = WebsiteFormatDetector()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    with sync_playwright() as p:
        cdp_url = ensure_allowed_cdp_url(config["connect_cdp_url"], {"connect_cdp_url": config["connect_cdp_url"]})
        browser_owner, context, page, _ = open_probe_browser(p, cdp_url)
        try:
            page.set_default_timeout(int(config["page_load_timeout_ms"]))
            _cleanup_probe_context_pages(context, page, normalized_url)

            if _url_matches_exclusions(normalized_url, excluded_domains, excluded_urls):
                audit_result = {
                    "status": "completed",
                    "requires_login": "否",
                    "login_evidence": "",
                    "supports_google_login": "否",
                    "google_login_evidence": "",
                    "has_comment_area": "否",
                    "comment_evidence": "",
                    "historical_presence": "未发现",
                    "historical_presence_evidence": "",
                }
                capability = _probe_failed_capability("命中现有来源排除规则")
                preprobe_meta = {
                    "ok": False,
                    "message": "命中现有来源排除规则",
                    "diagnosis": "命中现有来源排除规则",
                    "diagnostic_category": "hard_blocker",
                    "recommended_strategy": "skip",
                }
            else:
                try:
                    with probe_page_timeout_guard(int(config.get("single_page_timeout_seconds", 120))):
                        audit_result = audit_single_page(page, normalized_url, target_domains, config)
                        try:
                            capability = detector.analyze_page_capability(
                                page,
                                normalized_url,
                                enable_vision=bool(config.get("enable_vision", True)),
                                confidence_threshold=float(config.get("confidence_threshold", 0.8)),
                            )
                        except Exception as exc:
                            capability = _probe_failed_capability(str(exc)[:200])

                        try:
                            preprobe_meta = _preprobe_page_for_generation(
                                page,
                                normalized_url,
                                int(config["page_load_timeout_ms"]),
                                EXECUTION_MODE_CLASSIC,
                                str(extract_cell_text(row.get("推荐策略", "")) or "dom"),
                            )
                        except Exception as exc:
                            preprobe_meta = {
                                "ok": False,
                                "message": "页面预探测失败",
                                "diagnosis": str(exc)[:200],
                                "diagnostic_category": "preprobe_failed",
                                "recommended_strategy": str(extract_cell_text(row.get("推荐策略", "")) or "dom"),
                            }
                except ProbePageTimeoutError as exc:
                    audit_result, capability, preprobe_meta = build_probe_timeout_bundle(str(exc))
                except Exception as exc:
                    audit_result = {
                        "status": "failed",
                        "requires_login": "",
                        "login_evidence": "",
                        "supports_google_login": "",
                        "google_login_evidence": "",
                        "has_comment_area": "",
                        "comment_evidence": "",
                        "historical_presence": "",
                        "historical_presence_evidence": str(exc)[:200],
                    }
                    capability = _probe_failed_capability(str(exc)[:200])
                    preprobe_meta = {
                        "ok": False,
                        "message": "页面预探测失败",
                        "diagnosis": str(exc)[:200],
                        "diagnostic_category": "preprobe_failed",
                        "recommended_strategy": str(extract_cell_text(row.get("推荐策略", "")) or "dom"),
                    }

            classification = classify_probe_outcome(
                normalized_url,
                audit_result,
                capability,
                preprobe_meta,
                excluded_domains,
                excluded_urls,
            )
            recommended_strategy = _choose_recommended_strategy(
                str(extract_cell_text(row.get("推荐策略", "")) or ""),
                preprobe_meta,
                capability,
            )
            updates = {}
            updates.update(build_history_audit_update(row, audit_result, timestamp))
            updates.update(build_source_probe_update(row, capability, timestamp))
            updates.update(build_page_probe_update(row, classification, timestamp, recommended_strategy))
            result_item = {
                "来源链接": normalized_url,
                "页面探测状态": classification["页面探测状态"],
                "页面探测时间": timestamp,
                "是否值得发帖": classification["是否值得发帖"],
                "评论区是否存在": audit_result.get("has_comment_area", ""),
                "是否需要登录": audit_result.get("requires_login", ""),
                "是否支持Google登录": audit_result.get("supports_google_login", ""),
                "最终链接格式": capability.get("final_result", {}).get("recommended_format", "unknown"),
                "推荐策略": recommended_strategy,
                "页面探测失败原因": classification["页面探测失败原因"],
            }
            _cleanup_probe_context_pages(context, page, normalized_url)
            return {
                "ok": True,
                "normalized_url": normalized_url,
                "timestamp": timestamp,
                "updates": updates,
                "result_item": result_item,
            }
        finally:
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass
            try:
                browser_owner.close()
            except Exception:
                pass


def _run_probe_row_via_subprocess(task_payload: dict, config: dict) -> dict:
    timeout_seconds = int(config.get("single_page_timeout_seconds", 0) or 0)
    poll_interval = max(2, int(config.get("worker_poll_interval_seconds", 10) or 10))
    timeout_buffer = max(5, int(config.get("worker_timeout_buffer_seconds", 20) or 20))

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as task_fp:
        json.dump(task_payload, task_fp, ensure_ascii=False)
        task_path = task_fp.name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as result_fp:
        result_path = result_fp.name

    proc = None
    normalized_url = str(task_payload["normalized_url"])
    try:
        proc = subprocess.Popen(
            [
                _current_python_bin(),
                str(Path(__file__).resolve()),
                "--worker-task-file",
                task_path,
                "--worker-result-file",
                result_path,
            ],
            cwd=str(Path(__file__).resolve().parent),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        started_at = time.time()
        next_heartbeat = poll_interval
        hard_timeout = timeout_seconds + timeout_buffer if timeout_seconds > 0 else 0

        while True:
            rc = proc.poll()
            elapsed = int(time.time() - started_at)
            if rc is not None:
                break
            if elapsed >= next_heartbeat:
                print(f"  ⏳ 单页面探测子进程仍在执行（{elapsed}s）...")
                next_heartbeat += poll_interval
            if hard_timeout > 0 and elapsed >= hard_timeout:
                print(f"  ⏱️ 单页面探测子进程超时（>{hard_timeout}s），正在终止...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                break
            time.sleep(1)

        if Path(result_path).exists():
            try:
                payload = json.loads(Path(result_path).read_text(encoding="utf-8") or "{}")
                if isinstance(payload, dict) and payload:
                    return payload
            except Exception:
                pass

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        timeout_reason = f"页面探测子进程超时（>{hard_timeout}s）" if hard_timeout > 0 else "页面探测子进程异常退出"
        row = dict(task_payload["row"])
        audit_result, capability, preprobe_meta = build_probe_timeout_bundle(timeout_reason)
        classification = classify_probe_outcome(
            normalized_url,
            audit_result,
            capability,
            preprobe_meta,
            set(task_payload.get("excluded_domains", [])),
            set(task_payload.get("excluded_urls", [])),
        )
        recommended_strategy = _choose_recommended_strategy(
            str(extract_cell_text(row.get("推荐策略", "")) or ""),
            preprobe_meta,
            capability,
        )
        updates = {}
        updates.update(build_history_audit_update(row, audit_result, timestamp))
        updates.update(build_source_probe_update(row, capability, timestamp))
        updates.update(build_page_probe_update(row, classification, timestamp, recommended_strategy))
        return {
            "ok": False,
            "normalized_url": normalized_url,
            "timestamp": timestamp,
            "updates": updates,
            "result_item": {
                "来源链接": normalized_url,
                "页面探测状态": classification["页面探测状态"],
                "页面探测时间": timestamp,
                "是否值得发帖": classification["是否值得发帖"],
                "评论区是否存在": audit_result.get("has_comment_area", ""),
                "是否需要登录": audit_result.get("requires_login", ""),
                "是否支持Google登录": audit_result.get("supports_google_login", ""),
                "最终链接格式": capability.get("final_result", {}).get("recommended_format", "unknown"),
                "推荐策略": recommended_strategy,
                "页面探测失败原因": classification["页面探测失败原因"],
            },
        }
    finally:
        for path in (task_path, result_path):
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass


def _probe_worker_main(task_file: str, result_file: str) -> int:
    task_payload = json.loads(Path(task_file).read_text(encoding="utf-8"))
    result = _run_probe_worker(task_payload)
    Path(result_file).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


def classify_probe_outcome(
    url: str,
    audit_result: dict,
    capability: dict,
    preprobe_meta: dict,
    excluded_domains: set[str],
    excluded_urls: set[str],
) -> dict:
    if _url_matches_exclusions(url, excluded_domains, excluded_urls):
        return {
            "页面探测状态": PROBE_STATUS_SKIPPED,
            "是否值得发帖": PROBE_WORTH_NO,
            "页面探测失败原因": "命中现有来源排除规则",
        }

    if not preprobe_meta.get("ok", True):
        diagnosis = str(preprobe_meta.get("diagnosis", "") or preprobe_meta.get("message", "")).strip()
        category = str(preprobe_meta.get("diagnostic_category", "") or "").strip()
        if category in {"hard_blocker", "comment_signal_missing"}:
            return {
                "页面探测状态": PROBE_STATUS_COMPLETED,
                "是否值得发帖": PROBE_WORTH_NO,
                "页面探测失败原因": diagnosis or "页面预探测判定当前页面不值得继续尝试",
            }
        return {
            "页面探测状态": PROBE_STATUS_FAILED,
            "是否值得发帖": PROBE_WORTH_REVIEW,
            "页面探测失败原因": diagnosis or "页面预探测失败",
        }

    if str(audit_result.get("status", "") or "") != "completed":
        return {
            "页面探测状态": PROBE_STATUS_FAILED,
            "是否值得发帖": PROBE_WORTH_REVIEW,
            "页面探测失败原因": str(audit_result.get("historical_presence_evidence", "") or "历史审计失败"),
        }

    if str(audit_result.get("requires_login", "") or "") == "是":
        return {
            "页面探测状态": PROBE_STATUS_COMPLETED,
            "是否值得发帖": PROBE_WORTH_NO,
            "页面探测失败原因": str(audit_result.get("login_evidence", "") or "页面需要登录后才能评论"),
        }

    if str(audit_result.get("has_comment_area", "") or "") == "否":
        return {
            "页面探测状态": PROBE_STATUS_COMPLETED,
            "是否值得发帖": PROBE_WORTH_NO,
            "页面探测失败原因": str(audit_result.get("comment_evidence", "") or "未发现评论区"),
        }

    capability_status = str(capability.get("status", "") or "").strip()
    if capability_status == "failed":
        final_result = capability.get("final_result", {}) or {}
        return {
            "页面探测状态": PROBE_STATUS_FAILED,
            "是否值得发帖": PROBE_WORTH_REVIEW,
            "页面探测失败原因": str(final_result.get("evidence_type", "") or "格式探测失败"),
        }

    if capability_status == "conflict":
        return {
            "页面探测状态": PROBE_STATUS_COMPLETED,
            "是否值得发帖": PROBE_WORTH_REVIEW,
            "页面探测失败原因": "格式检测存在冲突，建议复核",
        }

    return {
        "页面探测状态": PROBE_STATUS_COMPLETED,
        "是否值得发帖": PROBE_WORTH_YES,
        "页面探测失败原因": "",
    }


def build_page_probe_update(row: dict, classification: dict, timestamp: str, recommended_strategy: str) -> dict:
    return {
        "页面探测状态": classification["页面探测状态"],
        "页面探测时间": timestamp,
        "是否值得发帖": classification["是否值得发帖"],
        "页面探测失败原因": classification["页面探测失败原因"],
        "推荐策略": recommended_strategy,
    }


def summarize_results(results: list[dict]) -> dict:
    probe_status_counts = Counter()
    worth_counts = Counter()
    strategy_counts = Counter()
    for item in results:
        probe_status_counts[item.get("页面探测状态", PROBE_STATUS_PENDING)] += 1
        worth_counts[item.get("是否值得发帖", "") or ""] += 1
        strategy_counts[item.get("推荐策略", "") or "dom"] += 1
    return {
        "total_scanned": len(results),
        "页面探测状态分布": dict(probe_status_counts),
        "是否值得发帖分布": dict(worth_counts),
        "推荐策略分布": dict(strategy_counts),
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
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
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def run_probe(limit: Optional[int] = None, reset_state: bool = False, force: bool = False, config_path: str = "config.json") -> dict:
    config = load_probe_task_config(config_path)
    state_path = Path(config["state_file"])
    summary_path = Path(config["summary_file"])
    report_json_path = Path(config["report_json_file"])
    report_csv_path = Path(config["report_csv_file"])
    state = {} if reset_state else load_json(state_path, {"processed_urls": [], "results": []})
    processed_urls = set(state.get("processed_urls", []))
    prior_results = list(state.get("results", []))

    workbook = FeishuWorkbook.from_config(config_path)
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法执行来源页面探测。")

    target_rows = read_target_rows(workbook)
    target_domains = extract_active_target_domains(target_rows)
    source_headers = dynamic_source_headers(target_rows)
    workbook.ensure_sheet_headers("sources", source_headers)
    source_rows = read_source_rows_paged(
        workbook,
        max_cols=max(len(source_headers), 250),
        page_size=int(config.get("read_page_size", 250)),
    )
    excluded_domains = _normalize_excluded_domains(config.get("excluded_source_domains", []))
    excluded_urls = _normalize_excluded_urls(config.get("excluded_source_urls", []))
    use_isolated_worker = bool(config.get("isolated_probe_worker", True))

    processed_this_run = []
    scanned = 0

    for row in source_rows:
        normalized_url = normalize_source_url(
            extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", ""))
        )
        if not normalized_url:
            continue
        existing_probe_status = str(extract_cell_text(row.get("页面探测状态", "")) or "").strip()
        if not force and existing_probe_status in {PROBE_STATUS_COMPLETED, PROBE_STATUS_FAILED, PROBE_STATUS_SKIPPED}:
            continue
        if not force and normalized_url in processed_urls:
            continue

        print(f"🔎 [{len(processed_urls) + 1}] 开始统一探测: {normalized_url}")
        worker_payload = _build_probe_worker_payload(
            row=row,
            normalized_url=normalized_url,
            config=config,
            excluded_domains=excluded_domains,
            excluded_urls=excluded_urls,
            target_domains=target_domains,
        )
        if use_isolated_worker:
            worker_result = _run_probe_row_via_subprocess(worker_payload, config)
        else:
            worker_result = _run_probe_worker(worker_payload)

        timestamp = worker_result["timestamp"]
        updates = worker_result["updates"]
        result_item = worker_result["result_item"]

        write_source_row_with_retry(
            workbook,
            source_headers,
            int(row.get("_row_index", 0) or 0),
            updates,
            max_retries=int(config["write_retry_count"]),
            base_sleep=float(config["write_retry_base_seconds"]),
        )

        processed_urls.add(normalized_url)
        processed_this_run.append(result_item)
        scanned += 1

        save_json(
            state_path,
            {
                "processed": len(processed_urls),
                "processed_urls": sorted(processed_urls),
                "results": prior_results + processed_this_run,
                "last_scanned_at": timestamp,
                "last_url": normalized_url,
            },
        )
        current_results = prior_results + processed_this_run
        current_summary = summarize_results(current_results)
        save_json(summary_path, current_summary)
        save_json(report_json_path, current_results)
        _write_csv(report_csv_path, current_results)
        if limit and scanned >= limit:
            break

    all_results = prior_results + processed_this_run
    summary = summarize_results(all_results)
    save_json(summary_path, summary)
    save_json(report_json_path, all_results)
    _write_csv(report_csv_path, all_results)
    return {
        "processed_this_run": scanned,
        "total_processed": len(all_results),
        "summary": summary,
        "state_file": str(state_path),
        "summary_file": str(summary_path),
        "report_json_file": str(report_json_path),
        "report_csv_file": str(report_csv_path),
    }


def main():
    parser = argparse.ArgumentParser(description="批量执行来源页面探测，并写回统一可发性状态。")
    parser.add_argument("--limit", type=int, default=None, help="本次最多扫描多少条来源链接。")
    parser.add_argument("--reset-state", action="store_true", help="忽略断点状态，从头开始扫描。")
    parser.add_argument("--force", action="store_true", help="即使已有页面探测状态，也重新探测。")
    parser.add_argument("--config", default="config.json", help="配置文件路径。")
    parser.add_argument("--worker-task-file", default="", help=argparse.SUPPRESS)
    parser.add_argument("--worker-result-file", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker_task_file and args.worker_result_file:
        raise SystemExit(_probe_worker_main(args.worker_task_file, args.worker_result_file))

    result = run_probe(limit=args.limit, reset_state=args.reset_state, force=args.force, config_path=args.config)
    print("✅ 来源页面统一探测完成")
    print(f"   本次处理: {result['processed_this_run']}")
    print(f"   累计处理: {result['total_processed']}")
    print(f"   汇总文件: {result['summary_file']}")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
