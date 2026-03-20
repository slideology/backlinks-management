import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from feishu_integration import FeishuClient, load_feishu_config


logger = logging.getLogger(__name__)

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "spm",
    "yclid",
}
TRACKING_QUERY_PREFIXES = ("utm_",)

DEFAULT_PROMOTED_SITE_MAP = {
    "bearclicker.net": "bearclicker.net",
    "www.bearclicker.net": "bearclicker.net",
    "nanobananaimage.org": "nanobananaimage.org",
    "www.nanobananaimage.org": "nanobananaimage.org",
    "nanobananaimage.com": "nanobananaimage.org",
    "www.nanobananaimage.com": "nanobananaimage.org",
}
LEGACY_SITE_KEY_ALIASES = {
    "b": "bearclicker.net",
    "n": "nanobananaimage.org",
}


def load_legacy_history_config(config_path: str = "config.json") -> dict:
    defaults = {
        "enabled": True,
        "spreadsheet_token": "DhMBsqDNKh0dI4trm5Rc3tJEnyb",
        "skip_sheet_titles": ["汇总", "Sheet54"],
        "cache_file": "artifacts/legacy_history/cache.json",
        "cache_ttl_hours": 12,
        "min_page_ascore": 10,
        "promoted_site_map": DEFAULT_PROMOTED_SITE_MAP,
    }
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        merged = {**defaults, **config.get("legacy_history", {})}
        merged["promoted_site_map"] = {
            **DEFAULT_PROMOTED_SITE_MAP,
            **config.get("legacy_history", {}).get("promoted_site_map", {}),
        }
        return merged
    except Exception as exc:
        logger.warning("读取旧飞书历史库配置失败: %s", exc)
        return defaults


def create_legacy_auth_client(config_path: str = "config.json") -> Optional[FeishuClient]:
    feishu_config = load_feishu_config(config_path)
    if not feishu_config.get("enabled"):
        return None
    if not feishu_config.get("app_id") or not feishu_config.get("app_secret"):
        return None
    return FeishuClient(
        app_id=feishu_config["app_id"],
        app_secret=feishu_config["app_secret"],
        spreadsheet_token="",
        sheet_id="",
        auth_mode=feishu_config.get("auth_mode", "app"),
        redirect_uri=feishu_config.get("redirect_uri", "http://127.0.0.1:8787/callback"),
        user_token_file=feishu_config.get("user_token_file", ".feishu_user_token.json"),
        scopes=feishu_config.get("scopes"),
    )


def extract_cell_text(cell) -> str:
    if cell is None:
        return ""
    if isinstance(cell, str):
        return cell.strip()
    if isinstance(cell, (int, float)):
        if isinstance(cell, float) and cell.is_integer():
            return str(int(cell))
        return str(cell)
    if isinstance(cell, list):
        parts = [extract_cell_text(item) for item in cell]
        return "".join(part for part in parts if part).strip()
    if isinstance(cell, dict):
        return str(cell.get("text") or cell.get("link") or "").strip()
    return str(cell).strip()


def extract_cell_url(cell) -> str:
    if cell is None:
        return ""
    if isinstance(cell, str):
        text = cell.strip()
        if text:
            match = re.search(r"https?://[^\s'\"\\\],}]+", text)
            if match:
                return match.group(0)
        return text
    if isinstance(cell, list):
        for item in cell:
            url = extract_cell_url(item)
            if url:
                return url
        return ""
    if isinstance(cell, dict):
        return str(cell.get("link") or cell.get("text") or "").strip()
    return ""


def normalize_source_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        return text

    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in TRACKING_QUERY_KEYS:
            continue
        if any(lower_key.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        filtered_query.append((key, value))

    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
        query=urlencode(filtered_query, doseq=True),
    )
    return urlunparse(normalized)


def get_root_domain(url: str) -> str:
    normalized = normalize_source_url(url)
    if not normalized:
        return ""
    hostname = urlparse(normalized).hostname or ""
    hostname = hostname.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def promoted_site_key_for_target(target_website: str, promoted_site_map: Optional[dict] = None) -> Optional[str]:
    normalized = normalize_source_url(target_website)
    if not normalized:
        return None
    hostname = urlparse(normalized).hostname or ""
    lookup = (promoted_site_map or DEFAULT_PROMOTED_SITE_MAP)
    promoted_site_key = str(lookup.get(hostname.lower()) or "").strip().lower()
    if not promoted_site_key:
        return None
    return LEGACY_SITE_KEY_ALIASES.get(promoted_site_key, promoted_site_key)


def parse_marker_value(marker_value: str, marker_prefix: str) -> tuple[bool, str]:
    text = extract_cell_text(marker_value).strip().lower()
    prefix = marker_prefix.lower()
    if not text or not text.startswith(prefix):
        return False, ""
    return True, text[len(prefix) :].strip()


def parse_page_ascore(value: str) -> Optional[float]:
    text = extract_cell_text(value)
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


@dataclass
class LegacyHistoryRecord:
    legacy_tab_title: str
    source_url: str
    source_title: str
    source_root_domain: str
    page_ascore: str
    promoted_site_key: str
    posted_flag: bool
    posted_date_raw: str
    source_sheet_id: str
    source_row: int


@dataclass
class LegacySourceRow:
    legacy_tab_title: str
    source_url: str
    source_title: str
    source_root_domain: str
    page_ascore: str
    tab_category: str
    n_marker: str
    b_marker: str
    extra_value: str
    source_sheet_id: str
    source_row: int


class LegacyFeishuHistoryStore:
    def __init__(
        self,
        records: list[LegacyHistoryRecord],
        promoted_site_map: Optional[dict] = None,
        source_rows: Optional[list[LegacySourceRow]] = None,
    ):
        self.records = records
        self.promoted_site_map = promoted_site_map or DEFAULT_PROMOTED_SITE_MAP
        self.source_rows = source_rows or []
        self.exact_index = {}
        self.domain_index = {}
        for record in self.records:
            promoted_site_key = LEGACY_SITE_KEY_ALIASES.get(record.promoted_site_key, record.promoted_site_key)
            record.promoted_site_key = promoted_site_key
            exact_key = (record.source_url, promoted_site_key)
            domain_key = (record.source_root_domain, promoted_site_key)
            self.exact_index.setdefault(exact_key, []).append(record)
            if record.source_root_domain:
                self.domain_index.setdefault(domain_key, []).append(record)

    def analyze(self, source_url: str, target_website: str) -> dict:
        promoted_site_key = promoted_site_key_for_target(target_website, self.promoted_site_map)
        normalized_url = normalize_source_url(source_url)
        root_domain = get_root_domain(source_url)

        if not promoted_site_key:
            return {
                "category": "legacy_marker_missing_mapping",
                "promoted_site_key": None,
                "normalized_source_url": normalized_url,
                "source_root_domain": root_domain,
                "exact_matches": [],
                "domain_matches": [],
            }

        exact_matches = self.exact_index.get((normalized_url, promoted_site_key), [])
        if exact_matches:
            return {
                "category": "exact_duplicate_same_site",
                "promoted_site_key": promoted_site_key,
                "normalized_source_url": normalized_url,
                "source_root_domain": root_domain,
                "exact_matches": [asdict(record) for record in exact_matches],
                "domain_matches": [],
            }

        domain_matches = self.domain_index.get((root_domain, promoted_site_key), [])
        if domain_matches:
            return {
                "category": "same_domain_same_site",
                "promoted_site_key": promoted_site_key,
                "normalized_source_url": normalized_url,
                "source_root_domain": root_domain,
                "exact_matches": [],
                "domain_matches": [asdict(record) for record in domain_matches],
            }

        return {
            "category": "no_match",
            "promoted_site_key": promoted_site_key,
            "normalized_source_url": normalized_url,
            "source_root_domain": root_domain,
            "exact_matches": [],
            "domain_matches": [],
        }

    @classmethod
    def from_config(cls, config_path: str = "config.json", force_refresh: bool = False) -> Optional["LegacyFeishuHistoryStore"]:
        config = load_legacy_history_config(config_path)
        if not config.get("enabled"):
            return None

        cache_file = Path(config.get("cache_file", "artifacts/legacy_history/cache.json"))
        cache_ttl_seconds = max(1, int(config.get("cache_ttl_hours", 12))) * 3600
        if not force_refresh and cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                generated_at = float(cached.get("generated_at", 0))
                has_source_rows = "source_rows" in cached
                cached_min_page_ascore = cached.get("min_page_ascore")
                expected_min_page_ascore = config.get("min_page_ascore", 10)
                if (
                    generated_at
                    and has_source_rows
                    and cached_min_page_ascore == expected_min_page_ascore
                    and (time.time() - generated_at) < cache_ttl_seconds
                ):
                    records = [LegacyHistoryRecord(**item) for item in cached.get("records", [])]
                    source_rows = [LegacySourceRow(**item) for item in cached.get("source_rows", [])]
                    return cls(records, promoted_site_map=config.get("promoted_site_map"), source_rows=source_rows)
            except Exception as exc:
                logger.warning("读取旧飞书历史缓存失败，将尝试重新拉取: %s", exc)

        client = create_legacy_auth_client(config_path)
        if not client:
            logger.warning("初始化旧飞书历史库客户端失败。")
            return None

        spreadsheet_token = config.get("spreadsheet_token", "")
        if not spreadsheet_token:
            logger.warning("旧飞书历史库 spreadsheet_token 为空，跳过去重接入。")
            return None

        records, source_rows = fetch_legacy_history_records(
            client=client,
            spreadsheet_token=spreadsheet_token,
            skip_sheet_titles=set(config.get("skip_sheet_titles", [])),
            min_page_ascore=float(config.get("min_page_ascore", 10)),
        )
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "generated_at": time.time(),
                    "record_count": len(records),
                    "records": [asdict(record) for record in records],
                    "source_row_count": len(source_rows),
                    "source_rows": [asdict(row) for row in source_rows],
                    "min_page_ascore": config.get("min_page_ascore", 10),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return cls(records, promoted_site_map=config.get("promoted_site_map"), source_rows=source_rows)


def fetch_sheet_metainfo(client: FeishuClient, spreadsheet_token: str) -> dict:
    response = requests.get(
        f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/metainfo",
        headers=client.build_headers(),
        timeout=client.timeout,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"读取旧飞书历史库元信息失败: {data}")
    return data.get("data", {})


def fetch_legacy_history_records(
    client: FeishuClient,
    spreadsheet_token: str,
    skip_sheet_titles: Optional[set[str]] = None,
    min_page_ascore: float = 10,
) -> tuple[list[LegacyHistoryRecord], list[LegacySourceRow]]:
    meta = fetch_sheet_metainfo(client, spreadsheet_token)
    records = []
    source_rows = []
    skip_titles = skip_sheet_titles or set()

    for sheet in meta.get("sheets", []):
        title = sheet.get("title", "")
        if title in skip_titles:
            continue

        sheet_id = sheet.get("sheetId", "")
        row_count = max(2, int(sheet.get("rowCount", 2)))
        client.attach_spreadsheet(spreadsheet_token, sheet_id)
        rows = client.read_range(f"{sheet_id}!A1:H{row_count}")
        tab_records, tab_source_rows = parse_legacy_tab_rows(rows, title, sheet_id, min_page_ascore=min_page_ascore)
        records.extend(tab_records)
        source_rows.extend(tab_source_rows)

    return records, source_rows


def parse_legacy_tab_rows(
    rows: list[list],
    tab_title: str,
    sheet_id: str,
    min_page_ascore: float = 10,
) -> tuple[list[LegacyHistoryRecord], list[LegacySourceRow]]:
    parsed_records = []
    parsed_source_rows = []
    for row_idx, row in enumerate(rows[1:], start=2):
        source_url = normalize_source_url(extract_cell_url(row[2] if len(row) > 2 else ""))
        if not source_url:
            continue

        source_title = extract_cell_text(row[1] if len(row) > 1 else "")
        page_ascore = extract_cell_text(row[0] if len(row) > 0 else "")
        score_value = parse_page_ascore(page_ascore)
        if score_value is None or score_value <= float(min_page_ascore):
            continue
        root_domain = get_root_domain(source_url)
        n_marker_text = extract_cell_text(row[3] if len(row) > 3 else "")
        b_marker_text = extract_cell_text(row[4] if len(row) > 4 else "")
        parsed_source_rows.append(
            LegacySourceRow(
                legacy_tab_title=tab_title,
                source_url=source_url,
                source_title=source_title,
                source_root_domain=root_domain,
                page_ascore=page_ascore,
                tab_category=extract_cell_text(row[5] if len(row) > 5 else ""),
                n_marker=n_marker_text,
                b_marker=b_marker_text,
                extra_value=extract_cell_text(row[6] if len(row) > 6 else ""),
                source_sheet_id=sheet_id,
                source_row=row_idx,
            )
        )
        markers = {
            "n": n_marker_text,
            "b": b_marker_text,
        }

        for promoted_site_key, marker_value in markers.items():
            posted_flag, posted_date_raw = parse_marker_value(marker_value, promoted_site_key)
            if not posted_flag:
                continue
            parsed_records.append(
                LegacyHistoryRecord(
                    legacy_tab_title=tab_title,
                    source_url=source_url,
                    source_title=source_title,
                    source_root_domain=root_domain,
                    page_ascore=page_ascore,
                    promoted_site_key=promoted_site_key,
                    posted_flag=True,
                    posted_date_raw=posted_date_raw,
                    source_sheet_id=sheet_id,
                    source_row=row_idx,
                )
            )

    return parsed_records, parsed_source_rows
