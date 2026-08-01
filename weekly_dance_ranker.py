#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音每周单人女性热舞候选 Top100（断点续跑与风控保护版）。

设计原则：
- 使用 Playwright 正常浏览器会话，不绕过验证码或平台签名。
- 监听页面公开返回的 JSON/XHR，并以 DOM 链接作为兜底。
- 对缺失字段如实标记；不伪造发布时间或互动数据。
- 只根据标题、话题、账号自述中的文字信号做内容初筛，
  不从人物外貌推断年龄或性别。
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import html
import json
import hashlib
import logging
import math
import os
import random
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from playwright.sync_api import BrowserContext, Page, Playwright, Response, TimeoutError as PlaywrightTimeoutError, sync_playwright

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.yaml"
DATA_DIR = APP_DIR / "data"
PROFILE_DIR = DATA_DIR / "profiles"
LOG_DIR = APP_DIR / "logs"
LOG_PATH = LOG_DIR / "weekly_ranker.log"

PLATFORM_LABELS = {"douyin": "抖音", "kuaishou": "快手", "tiktok": "TikTok"}
PLATFORM_HOME = {
    "douyin": "https://www.douyin.com/",
    "kuaishou": "https://www.kuaishou.com/",
    "tiktok": "https://www.tiktok.com/",
}


class CollectorNeedsAttention(RuntimeError):
    """采集需要人工处理时主动停止，避免生成看似成功的空榜。"""


class VerificationRequiredError(CollectorNeedsAttention):
    """后台模式检测到验证码或安全验证。"""


class EmptyDataError(CollectorNeedsAttention):
    """连续搜索没有返回可验证候选，通常是登录失效、风控或页面改版。"""


def enabled_platforms(config: Mapping[str, Any]) -> tuple[str, ...]:
    """返回配置中启用的平台，保持固定展示顺序。"""
    platform_config = config.get("platforms", {})
    return tuple(
        platform
        for platform in ("douyin", "kuaishou", "tiktok")
        if platform_config.get(platform, {}).get("enabled", True)
    )


def report_basename(config: Mapping[str, Any]) -> str:
    total = int(config.get("top_n_per_platform", 50)) * len(enabled_platforms(config))
    return f"weekly_dance_top{total}"

DANCE_TERMS = {
    "舞蹈", "跳舞", "热舞", "独舞", "单人舞", "女团舞", "翻跳",
    "扭腰", "扭胯", "摆胯", "高跟鞋舞", "椅子舞", "慢摇", "对镜热舞", "dance",
    "dancing", "solo dance", "sexy dance", "heels dance", "dance cover", "choreography",
}

SEXY_STYLE_TERMS = {
    "性感", "热辣", "辣妹", "纯欲", "妩媚", "撩人", "御姐", "甜辣", "氛围感", "高级感",
    "扭腰", "扭胯", "摆胯", "腰胯", "身材", "高跟鞋", "椅子舞", "慢摇",
    "sexy", "hot dance", "heels", "body wave", "waacking", "girl crush",
}

FEMALE_TARGET_TERMS = {
    "小姐姐", "美女", "辣妹", "御姐", "女神", "姐姐", "女生", "女性", "girl", "woman",
    "female", "beauty", "queen",
}

SOLO_TARGET_TERMS = {
    "单人", "独舞", "solo", "一个人跳", "个人舞", "对镜", "自拍", "全身舞", "一镜到底",
}

MALE_EXCLUDE_TERMS = {
    "男生跳舞", "男舞者", "男团", "小哥哥跳舞", "正太", "男孩跳舞", "哥哥舞蹈", "猛男舞",
}

GROUP_EXCLUDE_TERMS = {
    "群舞", "多人舞", "双人舞", "三人舞", "团舞", "团播", "群像", "姐妹合跳", "情侣舞",
    "兄妹舞", "合跳", "齐舞", "组合舞", "多人版", "女团",
}

FORMAT_EXCLUDE_TERMS = {
    "舞蹈教程", "教学", "分解教学", "慢动作教学", "镜面教学", "跟练", "动作分解", "教程版",
    "合集", "盘点", "混剪", "影视剪辑", "综艺片段", "reaction", "二创", "搬运", "直播录屏",
}

NON_HUMAN_EXCLUDE_TERMS = {
    "ai舞蹈", "ai美女", "动漫", "动画", "游戏角色", "虚拟人", "数字人", "换脸",
}


GENERIC_TOPIC_STOPWORDS = {
    "舞蹈", "跳舞", "挑战", "热门", "推荐", "教程", "翻跳", "卡点", "热舞", "视频",
    "dance", "dancing", "challenge", "viral", "trend", "trending", "cover", "tutorial",
    "fyp", "foryou", "foryoupage", "tiktok", "douyin", "kuaishou", "girl", "girls",
    "小姐姐", "美女", "女生", "女孩", "女团", "姐姐", "姐妹",
}

COUNT_PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)\s*([万亿千kKmMbB]?)")
HASHTAG_PATTERN = re.compile(r"[#＃]([\w\-\u4e00-\u9fff]+)", re.UNICODE)
CJK_RUN_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}")
WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{1,}")


@dataclass
class VideoRecord:
    platform: str
    video_id: str = ""
    url: str = ""
    title: str = ""
    author_name: str = ""
    author_id: str = ""
    create_time: Optional[datetime] = None
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    favorites: int = 0
    followers: int = 0
    music: str = ""
    hashtags: list[str] = field(default_factory=list)
    thumbnail: str = ""
    source_keyword: str = ""
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data_sources: set[str] = field(default_factory=set)
    data_quality_notes: list[str] = field(default_factory=list)

    # 内容匹配与评分字段
    dance_relevance: float = 0.0
    target_match_score: float = 0.0
    match_level: str = ""
    female_text_signal: bool = False
    solo_text_signal: bool = False
    sexy_style_signal: bool = False
    exclusion_reason: str = ""
    engagement_rate: float = 0.0
    engagement_basis: str = ""
    velocity_per_hour: float = 0.0  # 兼容旧字段；严格版不参与评分
    likes_per_hour: float = 0.0
    cross_platform_score: float = 0.0
    like_percentile: float = 0.0
    comment_percentile: float = 0.0
    share_percentile: float = 0.0
    favorite_percentile: float = 0.0
    share_favorite_percentile: float = 0.0
    engagement_percentile: float = 0.0
    velocity_percentile: float = 0.0
    target_percentile: float = 0.0
    final_score: float = 0.0
    rank: int = 0

    def key(self) -> str:
        if self.video_id:
            return f"{self.platform}:{self.video_id}"
        return f"{self.platform}:{canonicalize_url(self.url)}"

    def local_create_time(self, tz: ZoneInfo) -> Optional[datetime]:
        if not self.create_time:
            return None
        dt = self.create_time
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz)

    def to_dict(self, tz: Optional[ZoneInfo] = None) -> dict[str, Any]:
        obj = dataclasses.asdict(self)
        obj["data_sources"] = sorted(self.data_sources)
        if self.create_time:
            obj["create_time"] = (self.local_create_time(tz) if tz else self.create_time).isoformat()
        if self.captured_at:
            obj["captured_at"] = self.captured_at.isoformat()
        return obj


@dataclass
class DateWindow:
    start: datetime
    end: datetime
    mode: str

    @property
    def slug(self) -> str:
        return f"{self.start:%Y-%m-%d}_{(self.end - timedelta(seconds=1)):%Y-%m-%d}"

    @property
    def label(self) -> str:
        return f"{self.start:%Y-%m-%d %H:%M} 至 {self.end:%Y-%m-%d %H:%M}（左闭右开）"


CHECKPOINT_VERSION = 4


def video_record_from_dict(raw: Mapping[str, Any]) -> VideoRecord:
    """Rebuild a VideoRecord from an atomic checkpoint JSON object."""
    allowed = {field.name for field in dataclasses.fields(VideoRecord)}
    values = {key: value for key, value in dict(raw).items() if key in allowed}
    values["create_time"] = parse_datetime(values.get("create_time"))
    values["captured_at"] = parse_datetime(values.get("captured_at")) or datetime.now(timezone.utc)
    values["data_sources"] = set(values.get("data_sources") or [])
    values["hashtags"] = list(values.get("hashtags") or [])
    values["data_quality_notes"] = list(values.get("data_quality_notes") or [])
    return VideoRecord(**values)


def atomic_save_json(path: Path, payload: Any) -> None:
    """Write JSON atomically so a power loss cannot leave a half-written checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def collection_fingerprint(platform: str, config: Mapping[str, Any], platform_config: Mapping[str, Any]) -> str:
    """Invalidate a checkpoint when keywords, filters or ranking settings change."""
    browser = config.get("browser", {})
    relevant = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "platform": platform,
        "keywords": platform_config.get("keywords", []),
        "filters": config.get("filters", {}),
        "scoring": config.get("scoring", {}),
        "limits": {
            "max_candidates": browser.get("max_candidates_per_platform"),
            "max_details": browser.get("max_detail_visits_per_platform"),
            "scrolls": browser.get("scrolls_per_keyword"),
        },
    }
    encoded = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


class CollectionCheckpoint:
    def __init__(
        self,
        platform: str,
        window: DateWindow,
        tz: Any,
        config: Mapping[str, Any],
        platform_config: Mapping[str, Any],
    ):
        self.platform = platform
        self.window = window
        self.tz = tz
        self.fingerprint = collection_fingerprint(platform, config, platform_config)
        self.path = DATA_DIR / "checkpoints" / f"{platform}_{window.slug}.json"

    def load(self) -> Optional[dict[str, Any]]:
        if not self.path.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception as exc:
            logging.warning("[%s] 断点文件损坏，忽略并重新采集：%s", PLATFORM_LABELS[self.platform], exc)
            return None
        if state.get("version") != CHECKPOINT_VERSION:
            logging.info("[%s] 断点版本已过期，重新采集。", PLATFORM_LABELS[self.platform])
            return None
        if state.get("window_slug") != self.window.slug or state.get("config_fingerprint") != self.fingerprint:
            logging.info("[%s] 搜索词/配置或统计周已变化，旧断点不会复用。", PLATFORM_LABELS[self.platform])
            return None
        return state

    def save(
        self,
        records: Iterable[VideoRecord],
        *,
        phase: str,
        next_keyword_index: int,
        last_keyword_ids: Iterable[str],
        detail_keys: Iterable[str],
        completed_detail_keys: Iterable[str],
        failed_attempts: Mapping[str, int],
        processed_detail_count: int,
        note: str = "",
    ) -> None:
        payload = {
            "version": CHECKPOINT_VERSION,
            "platform": self.platform,
            "window_slug": self.window.slug,
            "window_label": self.window.label,
            "config_fingerprint": self.fingerprint,
            "phase": phase,
            "next_keyword_index": int(next_keyword_index),
            "last_keyword_ids": sorted(set(last_keyword_ids)),
            "detail_keys": list(detail_keys),
            "completed_detail_keys": sorted(set(completed_detail_keys)),
            "failed_attempts": {str(k): int(v) for k, v in failed_attempts.items()},
            "processed_detail_count": int(processed_detail_count),
            "record_count": len(list(records)) if not isinstance(records, list) else len(records),
            "updated_at": datetime.now(self.tz).isoformat(),
            "note": note,
            "records": [record.to_dict() for record in records],
        }
        atomic_save_json(self.path, payload)

    def delete(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            return f'"{pid}"' in result.stdout
        os.kill(pid, 0)
        return True
    except Exception:
        return False


@contextmanager
def acquire_run_lock(window: DateWindow, config: Mapping[str, Any]):
    """Prevent two collectors from using the same persistent browser profile at once."""
    lock_dir = DATA_DIR / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"weekly_{window.slug}.lock"
    stale_hours = float(config.get("browser", {}).get("lock_stale_hours", 12))
    token = f"{os.getpid()}-{time.time_ns()}"
    if lock_path.exists():
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        pid = int(existing.get("pid", 0) or 0)
        age_hours = max(0.0, (time.time() - lock_path.stat().st_mtime) / 3600.0)
        if _process_is_alive(pid) and age_hours < stale_hours:
            raise CollectorNeedsAttention(
                f"已有采集任务正在运行（PID {pid}）。请勿同时双击 run_weekly.bat 和 run_visible.bat。"
            )
        logging.warning("发现失效任务锁，已自动清理：%s", lock_path)
        lock_path.unlink(missing_ok=True)
    atomic_save_json(lock_path, {
        "pid": os.getpid(), "token": token, "created_at": datetime.now(timezone.utc).isoformat()
    })
    try:
        yield lock_path
    finally:
        try:
            current = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
            if current.get("token") == token:
                lock_path.unlink(missing_ok=True)
        except Exception:
            pass


class RecordStore:
    def __init__(self, platform: str):
        self.platform = platform
        self._records: dict[str, VideoRecord] = {}

    def add(self, record: VideoRecord) -> None:
        if record.platform != self.platform:
            return
        if not record.video_id and not record.url:
            return
        key = record.key()
        existing = self._records.get(key)
        if existing is None:
            self._records[key] = record
            return
        self._records[key] = merge_records(existing, record)

    def values(self) -> list[VideoRecord]:
        return list(self._records.values())

    def __len__(self) -> int:
        return len(self._records)


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
        force=True,
    )


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"缺少配置文件：{CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    return config


def resolve_timezone(name: str):
    """Load an IANA timezone and provide a safe Windows fallback for China time.

    Windows Python installations do not always include the IANA timezone database.
    The tzdata package is installed by requirements.txt, but the fixed UTC+8 fallback
    keeps the Douyin-only workflow usable even if that package is missing.
    """
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        fixed_offsets = {
            "Asia/Shanghai": 8,
            "Asia/Chongqing": 8,
            "Asia/Hong_Kong": 8,
            "Asia/Taipei": 8,
        }
        if name in fixed_offsets:
            logging.warning(
                "Timezone database is unavailable; using fixed UTC%+d fallback for %s.",
                fixed_offsets[name],
                name,
            )
            return timezone(timedelta(hours=fixed_offsets[name]), name=name)
        raise RuntimeError(
            f"无法加载时区 {name!r}。请运行 install.bat，或在虚拟环境中安装 tzdata："
            r".venv\Scripts\python.exe -m pip install tzdata"
        )


def compute_window(config: Mapping[str, Any], now: Optional[datetime] = None) -> tuple[DateWindow, Any]:
    tz = resolve_timezone(str(config.get("timezone", "Asia/Shanghai")))
    now_local = (now or datetime.now(tz)).astimezone(tz)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    mode = str(config.get("window_mode", "previous_7_complete_days"))
    if mode == "previous_calendar_week":
        this_monday = today_start - timedelta(days=today_start.weekday())
        start = this_monday - timedelta(days=7)
        end = this_monday
    elif mode == "previous_7_complete_days":
        end = today_start
        start = end - timedelta(days=7)
    else:
        raise ValueError(f"不支持的 window_mode：{mode}")
    return DateWindow(start=start, end=end, mode=mode), tz


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_count(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        if math.isnan(value) if isinstance(value, float) else False:
            return 0
        return max(0, int(value))
    text = str(value).strip().replace(",", "").replace("+", "")
    if not text:
        return 0
    try:
        return max(0, int(float(text)))
    except ValueError:
        pass
    match = COUNT_PATTERN.search(text)
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = {
        "": 1,
        "千": 1_000,
        "k": 1_000,
        "万": 10_000,
        "m": 1_000_000,
        "亿": 100_000_000,
        "b": 1_000_000_000,
    }.get(unit, 1)
    return max(0, int(number * multiplier))


def parse_datetime(value: Any) -> Optional[datetime]:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        try:
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000.0
            if number < 946684800:  # 2000-01-01 前大概率不是有效短视频时间戳
                return None
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    text = text.replace("Z", "+00:00")
    for parser in (
        lambda t: datetime.fromisoformat(t),
        lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M:%S"),
        lambda t: datetime.strptime(t, "%Y-%m-%d"),
    ):
        try:
            dt = parser(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def deep_get(obj: Mapping[str, Any], *paths: str, default: Any = None) -> Any:
    for path in paths:
        cur: Any = obj
        ok = True
        for part in path.split("."):
            if not isinstance(cur, Mapping) or part not in cur:
                ok = False
                break
            cur = cur[part]
        if ok and cur not in (None, ""):
            return cur
    return default


def first_url(value: Any) -> str:
    if isinstance(value, str):
        return value if value.startswith("http") else ""
    if isinstance(value, list):
        for item in value:
            url = first_url(item)
            if url:
                return url
    if isinstance(value, Mapping):
        for key in ("url_list", "urlList", "urls", "url", "src"):
            if key in value:
                url = first_url(value[key])
                if url:
                    return url
    return ""


def walk_dicts(obj: Any) -> Iterator[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_dicts(item)


def extract_hashtags(title: str, extras: Iterable[Any] = ()) -> list[str]:
    tags: list[str] = []
    for tag in HASHTAG_PATTERN.findall(title or ""):
        cleaned = normalize_text(tag).lstrip("#＃")
        if cleaned:
            tags.append(cleaned)
    for extra in extras:
        if isinstance(extra, str):
            cleaned = normalize_text(extra).lstrip("#＃")
            if cleaned:
                tags.append(cleaned)
        elif isinstance(extra, Mapping):
            candidate = deep_get(extra, "title", "name", "cha_name", "challengeName", default="")
            cleaned = normalize_text(candidate).lstrip("#＃")
            if cleaned:
                tags.append(cleaned)
    return unique_preserve(tags)


def unique_preserve(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit((parsed.scheme or "https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))
    except Exception:
        return url


def merge_records(a: VideoRecord, b: VideoRecord) -> VideoRecord:
    def better_text(x: str, y: str) -> str:
        return y if len(normalize_text(y)) > len(normalize_text(x)) else x

    a.video_id = a.video_id or b.video_id
    a.url = a.url or b.url
    if b.url and len(b.url) < len(a.url):
        a.url = b.url
    a.title = better_text(a.title, b.title)
    a.author_name = better_text(a.author_name, b.author_name)
    a.author_id = better_text(a.author_id, b.author_id)
    a.create_time = a.create_time or b.create_time
    a.views = max(a.views, b.views)
    a.likes = max(a.likes, b.likes)
    a.comments = max(a.comments, b.comments)
    a.shares = max(a.shares, b.shares)
    a.favorites = max(a.favorites, b.favorites)
    a.followers = max(a.followers, b.followers)
    a.music = better_text(a.music, b.music)
    a.hashtags = unique_preserve([*a.hashtags, *b.hashtags])
    a.thumbnail = a.thumbnail or b.thumbnail
    source_keywords = []
    for raw_keyword in (a.source_keyword, b.source_keyword):
        for keyword in [part.strip() for part in raw_keyword.split("|") if part.strip()]:
            if keyword.casefold() not in {item.casefold() for item in source_keywords}:
                source_keywords.append(keyword)
    a.source_keyword = " | ".join(source_keywords)
    a.data_sources.update(b.data_sources)
    a.data_quality_notes = unique_preserve([*a.data_quality_notes, *b.data_quality_notes])
    return a


def build_tiktok_url(video_id: str, author_id: str = "") -> str:
    if author_id:
        author_id = author_id.lstrip("@")
        return f"https://www.tiktok.com/@{urllib.parse.quote(author_id)}/video/{video_id}"
    return f"https://www.tiktok.com/video/{video_id}"


def build_platform_url(platform: str, video_id: str, author_id: str = "") -> str:
    if not video_id:
        return ""
    if platform == "douyin":
        return f"https://www.douyin.com/video/{video_id}"
    if platform == "kuaishou":
        return f"https://www.kuaishou.com/short-video/{video_id}"
    if platform == "tiktok":
        return build_tiktok_url(video_id, author_id)
    return ""


def find_platform_video_url(obj: Mapping[str, Any], platform: str) -> str:
    candidates: list[str] = []
    for path in (
        "share_url", "shareUrl", "web_url", "webUrl", "jumpUrl", "photoUrl", "itemUrl", "url",
    ):
        value = deep_get(obj, path, default="")
        if isinstance(value, str) and value.startswith("http"):
            candidates.append(value)
    for url in candidates:
        low = url.lower()
        if platform == "douyin" and "douyin.com" in low and "/video/" in low:
            return canonicalize_url(url)
        if platform == "kuaishou" and ("kuaishou.com" in low or "kwai.com" in low) and ("short-video" in low or "photo" in low):
            return canonicalize_url(url)
        if platform == "tiktok" and "tiktok.com" in low and "/video/" in low:
            return canonicalize_url(url)
    return ""


def normalize_tiktok_dict(obj: Mapping[str, Any], keyword: str, source: str) -> Optional[VideoRecord]:
    stats = deep_get(obj, "stats", "statistics", "statsV2", default={})
    if not isinstance(stats, Mapping):
        stats = {}
    video_id = normalize_text(deep_get(obj, "id", "itemId", "videoId", "item_id", default=""))
    title = normalize_text(deep_get(obj, "desc", "description", "text", "caption", default=""))
    has_stats = any(k in stats for k in ("playCount", "diggCount", "commentCount", "shareCount", "collectCount"))
    if not video_id or not (title or has_stats or deep_get(obj, "createTime", "create_time")):
        return None

    author = deep_get(obj, "author", "authorInfo", "user", default={})
    if not isinstance(author, Mapping):
        author = {}
    author_id = normalize_text(deep_get(author, "uniqueId", "unique_id", "username", "id", default=""))
    author_name = normalize_text(deep_get(author, "nickname", "displayName", "name", default=author_id))
    author_stats = deep_get(obj, "authorStats", "author.stats", "authorStatsV2", default={})
    if not isinstance(author_stats, Mapping):
        author_stats = {}
    challenges = deep_get(obj, "challenges", "textExtra", "hashtags", default=[])
    if not isinstance(challenges, list):
        challenges = []
    music_obj = deep_get(obj, "music", "musicInfo", default={})
    music = ""
    if isinstance(music_obj, Mapping):
        music = normalize_text(deep_get(music_obj, "title", "musicName", "name", default=""))
        music_author = normalize_text(deep_get(music_obj, "authorName", "author", default=""))
        if music_author and music_author.casefold() not in music.casefold():
            music = f"{music} - {music_author}" if music else music_author
    thumbnail = first_url(deep_get(obj, "video.cover", "video.dynamicCover", "video.originCover", "cover", default=""))
    url = find_platform_video_url(obj, "tiktok") or build_tiktok_url(video_id, author_id)
    return VideoRecord(
        platform="tiktok",
        video_id=video_id,
        url=url,
        title=title,
        author_name=author_name,
        author_id=author_id,
        create_time=parse_datetime(deep_get(obj, "createTime", "create_time", "create_time_iso", default=None)),
        views=parse_count(deep_get(stats, "playCount", "viewCount", "play_count", default=0)),
        likes=parse_count(deep_get(stats, "diggCount", "likeCount", "digg_count", default=0)),
        comments=parse_count(deep_get(stats, "commentCount", "comment_count", default=0)),
        shares=parse_count(deep_get(stats, "shareCount", "share_count", default=0)),
        favorites=parse_count(deep_get(stats, "collectCount", "favoriteCount", "saveCount", default=0)),
        followers=parse_count(deep_get(author_stats, "followerCount", "followers", default=deep_get(author, "followerCount", default=0))),
        music=music,
        hashtags=extract_hashtags(title, challenges),
        thumbnail=thumbnail,
        source_keyword=keyword,
        data_sources={source},
    )


def normalize_douyin_dict(obj: Mapping[str, Any], keyword: str, source: str) -> Optional[VideoRecord]:
    # 常见数据对象可能在 aweme_info / aweme / item 下，walk_dicts 会继续进入，这里只处理本层。
    stats = deep_get(obj, "statistics", "stats", default={})
    if not isinstance(stats, Mapping):
        stats = {}
    video_id = normalize_text(deep_get(obj, "aweme_id", "awemeId", "item_id", "itemId", default=""))
    title = normalize_text(deep_get(obj, "desc", "description", "caption", "title", default=""))
    has_stats = any(k in stats for k in ("play_count", "digg_count", "comment_count", "share_count", "collect_count"))
    if not video_id or not (title or has_stats or deep_get(obj, "create_time", "createTime")):
        return None

    author = deep_get(obj, "author", "author_info", "user", default={})
    if not isinstance(author, Mapping):
        author = {}
    author_id = normalize_text(deep_get(author, "unique_id", "short_id", "sec_uid", "uid", default=""))
    author_name = normalize_text(deep_get(author, "nickname", "name", default=author_id))
    cha_list = deep_get(obj, "cha_list", "challenges", "text_extra", "hashtags", default=[])
    if not isinstance(cha_list, list):
        cha_list = []
    music_obj = deep_get(obj, "music", "music_info", default={})
    music = normalize_text(deep_get(music_obj, "title", "music_name", "name", default="")) if isinstance(music_obj, Mapping) else ""
    thumbnail = first_url(deep_get(obj, "video.cover", "video.origin_cover", "video.dynamic_cover", "cover", default=""))
    url = find_platform_video_url(obj, "douyin") or build_platform_url("douyin", video_id, author_id)
    return VideoRecord(
        platform="douyin",
        video_id=video_id,
        url=url,
        title=title,
        author_name=author_name,
        author_id=author_id,
        create_time=parse_datetime(deep_get(obj, "create_time", "createTime", default=None)),
        views=parse_count(deep_get(stats, "play_count", "playCount", "view_count", default=0)),
        likes=parse_count(deep_get(stats, "digg_count", "diggCount", "like_count", default=0)),
        comments=parse_count(deep_get(stats, "comment_count", "commentCount", default=0)),
        shares=parse_count(deep_get(stats, "share_count", "shareCount", default=0)),
        favorites=parse_count(deep_get(stats, "collect_count", "collectCount", "favorite_count", default=0)),
        followers=parse_count(deep_get(author, "follower_count", "followerCount", default=0)),
        music=music,
        hashtags=extract_hashtags(title, cha_list),
        thumbnail=thumbnail,
        source_keyword=keyword,
        data_sources={source},
    )


def normalize_kuaishou_dict(obj: Mapping[str, Any], keyword: str, source: str) -> Optional[VideoRecord]:
    # 快手 GraphQL 中经常有 photo 子对象；walk_dicts 会递归处理 photo，本层也兼容扁平对象。
    stats = deep_get(obj, "statistics", "stats", default={})
    if not isinstance(stats, Mapping):
        stats = {}
    video_id = normalize_text(deep_get(obj, "photoId", "photo_id", "photoid", "workId", "itemId", default=""))
    title = normalize_text(deep_get(obj, "caption", "title", "description", "desc", default=""))
    direct_stat_keys = ("viewCount", "playCount", "likeCount", "commentCount", "shareCount")
    has_stats = any(k in obj for k in direct_stat_keys) or bool(stats)
    if not video_id or not (title or has_stats or deep_get(obj, "timestamp", "createTime", "create_time")):
        return None

    author = deep_get(obj, "author", "user", "userInfo", "owner", default={})
    if not isinstance(author, Mapping):
        author = {}
    author_id = normalize_text(deep_get(author, "kwaiId", "userId", "id", "principalId", default=""))
    author_name = normalize_text(deep_get(author, "name", "user_name", "nickname", default=author_id))
    tags = deep_get(obj, "hashtags", "tags", "topics", default=[])
    if not isinstance(tags, list):
        tags = []
    music_obj = deep_get(obj, "music", "musicInfo", "soundTrack", default={})
    music = normalize_text(deep_get(music_obj, "name", "title", "musicName", default="")) if isinstance(music_obj, Mapping) else ""
    thumbnail = first_url(deep_get(obj, "coverUrl", "coverUrls", "cover", "thumbnailUrl", default=""))
    url = find_platform_video_url(obj, "kuaishou") or build_platform_url("kuaishou", video_id, author_id)
    return VideoRecord(
        platform="kuaishou",
        video_id=video_id,
        url=url,
        title=title,
        author_name=author_name,
        author_id=author_id,
        create_time=parse_datetime(deep_get(obj, "timestamp", "createTime", "create_time", "uploadTime", default=None)),
        views=parse_count(deep_get(obj, "viewCount", "playCount", "play_count", default=deep_get(stats, "viewCount", "playCount", default=0))),
        likes=parse_count(deep_get(obj, "likeCount", "realLikeCount", "like_count", default=deep_get(stats, "likeCount", default=0))),
        comments=parse_count(deep_get(obj, "commentCount", "comment_count", default=deep_get(stats, "commentCount", default=0))),
        shares=parse_count(deep_get(obj, "shareCount", "share_count", default=deep_get(stats, "shareCount", default=0))),
        favorites=parse_count(deep_get(obj, "collectCount", "favoriteCount", "collect_count", default=deep_get(stats, "collectCount", default=0))),
        followers=parse_count(deep_get(author, "fan", "fansCount", "followerCount", "followers", default=0)),
        music=music,
        hashtags=extract_hashtags(title, tags),
        thumbnail=thumbnail,
        source_keyword=keyword,
        data_sources={source},
    )


NORMALIZERS: dict[str, Callable[[Mapping[str, Any], str, str], Optional[VideoRecord]]] = {
    "douyin": normalize_douyin_dict,
    "kuaishou": normalize_kuaishou_dict,
    "tiktok": normalize_tiktok_dict,
}


def parse_payload(platform: str, payload: Any, keyword: str, source: str, store: RecordStore) -> int:
    before = len(store)
    normalizer = NORMALIZERS[platform]
    for obj in walk_dicts(payload):
        try:
            record = normalizer(obj, keyword, source)
            if record:
                store.add(record)
        except Exception as exc:
            logging.debug("normalize failed %s: %s", platform, exc)
    return len(store) - before


def extract_video_id_from_url(platform: str, url: str) -> str:
    patterns = {
        "douyin": [r"/video/(\d+)", r"modal_id=(\d+)"],
        "kuaishou": [r"/short-video/([A-Za-z0-9_-]+)", r"photoId=([A-Za-z0-9_-]+)"],
        "tiktok": [r"/video/(\d+)"],
    }
    for pattern in patterns.get(platform, []):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def is_platform_video_url(platform: str, url: str) -> bool:
    low = url.lower()
    if platform == "douyin":
        return "douyin.com" in low and ("/video/" in low or "modal_id=" in low)
    if platform == "kuaishou":
        return ("kuaishou.com" in low or "kwai.com" in low) and ("short-video" in low or "photoid=" in low)
    if platform == "tiktok":
        return "tiktok.com" in low and "/video/" in low
    return False


def search_url(platform: str, keyword: str) -> str:
    encoded = urllib.parse.quote(keyword)
    if platform == "douyin":
        return f"https://www.douyin.com/search/{encoded}?type=video"
    if platform == "kuaishou":
        return f"https://www.kuaishou.com/search/video?searchKey={encoded}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/search/video?q={encoded}"
    raise ValueError(platform)


class PlatformCollector:
    def __init__(
        self,
        playwright: Playwright,
        platform: str,
        config: Mapping[str, Any],
        platform_config: Mapping[str, Any],
        debug_dir: Path,
        headless_override: Optional[bool] = None,
        background_offscreen: bool = False,
    ):
        self.playwright = playwright
        self.platform = platform
        self.config = config
        self.platform_config = platform_config
        self.browser_config = config.get("browser", {})
        self.profile_path = PROFILE_DIR / platform
        self.debug_dir = debug_dir / platform
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.store = RecordStore(platform)
        self.current_keyword = ""
        self.response_seen = 0
        self.response_parsed = 0
        self.response_errors = 0
        self.captcha_count = 0
        self.capture_mode = "idle"
        self.active_store: RecordStore = self.store
        self.active_video_id = ""
        self.active_search_response_count = 0
        self.last_keyword_ids: set[str] = set()
        configured_headless = bool(self.browser_config.get("headless", True))
        self.is_background_offscreen = bool(background_offscreen)
        # Douyin may return empty search payloads in true headless mode.  The weekly
        # background launcher therefore uses a normal headed Chromium window placed
        # far outside the visible desktop.  It keeps normal rendering/login behavior
        # while remaining unattended.
        if self.is_background_offscreen:
            self.is_headless = False
        else:
            self.is_headless = configured_headless if headless_override is None else bool(headless_override)
        self.is_unattended = self.is_headless or self.is_background_offscreen

    def launch_context(self) -> BrowserContext:
        self.profile_path.mkdir(parents=True, exist_ok=True)
        browser_args = ["--disable-notifications"]
        if self.is_background_offscreen:
            # Keep a fully rendered, headed browser but place its window outside the
            # visible desktop.  This is more compatible with Douyin than true headless.
            x = int(self.browser_config.get("background_window_x", -32000))
            y = int(self.browser_config.get("background_window_y", -32000))
            browser_args.extend([f"--window-position={x},{y}", "--window-size=1440,1000"])
        elif self.is_headless:
            browser_args.append("--window-size=1440,1000")
        else:
            browser_args.append("--start-maximized")
        context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_path),
            headless=self.is_headless,
            slow_mo=int(self.browser_config.get("slow_mo_ms", 0)),
            locale=str(self.platform_config.get("locale", "zh-CN")),
            viewport={"width": 1440, "height": 1000},
            args=browser_args,
        )
        context.set_default_timeout(int(self.browser_config.get("navigation_timeout_ms", 60000)))
        context.set_default_navigation_timeout(int(self.browser_config.get("navigation_timeout_ms", 60000)))
        return context

    def _request_text(self, response: Response) -> str:
        parts = [response.url]
        try:
            post_data = response.request.post_data or ""
            if post_data:
                parts.append(post_data)
        except Exception:
            pass
        return urllib.parse.unquote_plus(" ".join(parts)).casefold()

    def _is_search_response(self, response: Response) -> bool:
        """Only accept real search endpoints while collecting a keyword.

        The previous version accepted any URL containing feed/item/detail/api, which
        accidentally imported recommendations and unrelated page data.
        """
        text = self._request_text(response)
        if "search" not in text:
            return False
        blocked = ("search/sug", "search_sug", "suggest", "hot/search", "history")
        return not any(token in text for token in blocked)

    def _is_detail_response(self, response: Response) -> bool:
        text = self._request_text(response)
        if not self.active_video_id or self.active_video_id not in text:
            return False
        return any(token in text for token in ("detail", "aweme", "item"))

    def response_handler(self, response: Response) -> None:
        try:
            resource_type = response.request.resource_type
            if resource_type not in {"xhr", "fetch", "document"}:
                return
            if self.capture_mode == "search":
                if not self._is_search_response(response):
                    return
                target_store = self.active_store
            elif self.capture_mode == "detail":
                if not self._is_detail_response(response):
                    return
                target_store = RecordStore(self.platform)
            else:
                return

            content_type = (response.headers.get("content-type") or "").lower()
            self.response_seen += 1
            content_length = parse_count(response.headers.get("content-length", 0))
            if content_length > 15_000_000:
                return
            try:
                payload = response.json()
            except Exception:
                if "json" not in content_type:
                    return
                body = response.body()
                if len(body) > 15_000_000:
                    return
                raw = body.decode("utf-8", errors="ignore").strip()
                if not raw or raw[0] not in "[{":
                    return
                payload = json.loads(raw)

            added = parse_payload(
                self.platform,
                payload,
                self.current_keyword,
                f"network:{short_url(response.url)}",
                target_store,
            )
            if self.capture_mode == "search":
                self.active_search_response_count += 1
                if added:
                    self.response_parsed += 1
            else:
                for candidate in target_store.values():
                    if candidate.video_id == self.active_video_id:
                        self.store.add(candidate)
                        self.response_parsed += 1
        except Exception as exc:
            self.response_errors += 1
            logging.debug("[%s] response parse failed: %s", self.platform, exc)

    def check_verification(self, page: Page, stage: str) -> None:
        try:
            title = page.title()
        except Exception:
            title = ""
        try:
            body = page.locator("body").inner_text(timeout=5000)[:5000]
        except Exception:
            body = ""
        text = f"{title}\n{body}".casefold()
        markers = ["验证码", "安全验证", "完成验证", "verify you are human", "captcha", "security verification"]
        if any(marker.casefold() in text for marker in markers):
            self.captcha_count += 1
            self.save_screenshot(page, f"verification_{stage}_{self.captcha_count}")
            if self.is_unattended:
                raise VerificationRequiredError(
                    f"[{PLATFORM_LABELS[self.platform]}] 检测到验证码或安全验证。"
                    "后台模式已停止；请运行 run_visible.bat 完成验证，或重新运行 login_once.bat。"
                )
            print(
                f"\n[{PLATFORM_LABELS[self.platform]}] 检测到验证码或安全验证。"
                "请在已打开的浏览器中手动完成，完成后回到此窗口按回车继续。"
            )
            input()

    def save_screenshot(self, page: Page, name: str) -> None:
        if not self.browser_config.get("save_debug_screenshots", True):
            return
        try:
            safe = re.sub(r"[^\w\-]+", "_", name)[:80]
            page.screenshot(path=str(self.debug_dir / f"{safe}.png"), full_page=False)
        except Exception:
            pass

    def parse_embedded_json(self, page: Page, source_prefix: str, target_store: Optional[RecordStore] = None) -> None:
        target_store = target_store or self.active_store
        selectors = [
            "script[type='application/json']",
            "script#__UNIVERSAL_DATA_FOR_REHYDRATION__",
            "script#SIGI_STATE",
            "script#RENDER_DATA",
            "script#__NEXT_DATA__",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = min(locator.count(), 30)
                for i in range(count):
                    raw = (locator.nth(i).text_content(timeout=3000) or "").strip()
                    if not raw:
                        continue
                    variants = [raw]
                    if "%7B" in raw or "%22" in raw:
                        variants.append(urllib.parse.unquote(raw))
                    for candidate in variants:
                        candidate = candidate.strip()
                        if not candidate or candidate[0] not in "[{":
                            continue
                        try:
                            payload = json.loads(candidate)
                        except json.JSONDecodeError:
                            continue
                        parse_payload(
                            self.platform, payload, self.current_keyword,
                            f"{source_prefix}:{selector}", target_store
                        )
                        break
            except Exception as exc:
                logging.debug("[%s] embedded JSON failed %s: %s", self.platform, selector, exc)

    def parse_dom_links(self, page: Page, source: str, target_store: Optional[RecordStore] = None) -> None:
        """Extract only visible video cards in the central search-results area.

        This deliberately excludes hidden anchors, navigation links and most right-hand
        recommendations. It is a fallback; the real search XHR remains the preferred source.
        """
        target_store = target_store or self.active_store
        try:
            rows = page.evaluate(
                """
                () => {
                  const vw = window.innerWidth || 1440;
                  const vh = window.innerHeight || 1000;
                  const links = Array.from(document.querySelectorAll(
                    'a[href*="/video/"], a[href*="modal_id="]'
                  ));
                  return links.map(a => {
                    const rect = a.getBoundingClientRect();
                    const style = getComputedStyle(a);
                    if (!rect.width || !rect.height || style.display === 'none' || style.visibility === 'hidden') return null;
                    const cx = rect.left + rect.width / 2;
                    if (cx < 110 || cx > vw * 0.88 || rect.bottom < -200 || rect.top > vh + 6000) return null;
                    let node = a;
                    let text = '';
                    let img = '';
                    for (let i = 0; i < 7 && node; i++, node = node.parentElement) {
                      const t = (node.innerText || '').trim();
                      const media = node.querySelector && (node.querySelector('img') || node.querySelector('video'));
                      if (!img && media) img = media.poster || media.src || '';
                      if (!text && t.length >= 4 && t.length <= 1200) text = t;
                      if (text && img) break;
                    }
                    return {href: a.href || '', text, img, x: rect.left, y: rect.top};
                  }).filter(Boolean);
                }
                """
            )
        except Exception as exc:
            logging.debug("[%s] DOM link extraction failed: %s", self.platform, exc)
            return
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            url = canonicalize_url(normalize_text(row.get("href")))
            if not is_platform_video_url(self.platform, url):
                continue
            video_id = extract_video_id_from_url(self.platform, url)
            if not video_id:
                continue
            card_text = normalize_text(row.get("text"))
            record = VideoRecord(
                platform=self.platform,
                video_id=video_id,
                url=url,
                title=card_text[:500],
                thumbnail=normalize_text(row.get("img")),
                source_keyword=self.current_keyword,
                data_sources={source},
                data_quality_notes=["搜索页DOM兜底，互动数据由详情页补全"],
            )
            target_store.add(record)

    def _find_search_input(self, page: Page):
        selectors = [
            "input[placeholder*='搜索']",
            "input[type='search']",
            "input[data-e2e*='search']",
            "input",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector)
                for i in range(min(locator.count(), 12)):
                    item = locator.nth(i)
                    if item.is_visible():
                        return item
            except Exception:
                continue
        return None

    def submit_search_via_ui(self, page: Page, keyword: str) -> bool:
        search_input = self._find_search_input(page)
        if search_input is None:
            return False
        try:
            search_input.click(timeout=5000)
            search_input.fill(keyword, timeout=5000)
            search_input.press("Enter")
            page.wait_for_timeout(3500)
            try:
                page.get_by_text("视频", exact=True).first.click(timeout=2500)
                page.wait_for_timeout(1800)
            except Exception:
                pass
            return True
        except Exception as exc:
            logging.debug("[%s] UI search submit failed: %s", self.platform, exc)
            return False

    def _collect_keyword_once(self, page: Page, keyword: str, index: int, use_ui: bool) -> RecordStore:
        keyword_store = RecordStore(self.platform)
        self.active_store = keyword_store
        self.capture_mode = "search"
        self.active_search_response_count = 0
        if use_ui:
            if not self.submit_search_via_ui(page, keyword):
                page.goto(search_url(self.platform, keyword), wait_until="domcontentloaded")
        else:
            page.goto(search_url(self.platform, keyword), wait_until="domcontentloaded")
        page.wait_for_timeout(3800)
        self.check_verification(page, f"search_{index}_{'ui' if use_ui else 'url'}")
        try:
            page.wait_for_function(
                "document.querySelectorAll('a[href*=\"/video/\"],a[href*=\"modal_id=\"]').length > 0",
                timeout=12000,
            )
        except Exception:
            pass
        scrolls = int(self.browser_config.get("scrolls_per_keyword", 12))
        scroll_pause_ms = int(float(self.browser_config.get("scroll_pause_seconds", 1.3)) * 1000)
        for step in range(scrolls):
            page.evaluate("window.scrollBy(0, Math.max(1800, window.innerHeight * 1.8))")
            page.wait_for_timeout(scroll_pause_ms)
            if step in {1, 3, 6, scrolls - 1}:
                self.parse_dom_links(page, f"dom:search:{keyword}", keyword_store)
        self.parse_embedded_json(page, f"search:{keyword}", keyword_store)
        self.parse_dom_links(page, f"dom:search:{keyword}", keyword_store)
        self.capture_mode = "idle"
        return keyword_store

    @staticmethod
    def _id_set(store: RecordStore) -> set[str]:
        return {r.video_id for r in store.values() if r.video_id}

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def parse_meta(self, page: Page, record: VideoRecord) -> None:
        def attr(selector: str, name: str) -> str:
            try:
                return normalize_text(page.locator(selector).first.get_attribute(name, timeout=2500))
            except Exception:
                return ""

        title = attr("meta[property='og:title']", "content") or attr("meta[name='description']", "content")
        image = attr("meta[property='og:image']", "content")
        canonical = attr("link[rel='canonical']", "href")
        merged = VideoRecord(
            platform=self.platform,
            video_id=record.video_id or extract_video_id_from_url(self.platform, canonical),
            url=canonicalize_url(canonical or record.url),
            title=title,
            thumbnail=image,
            source_keyword=record.source_keyword,
            data_sources={"detail:meta"},
        )
        self.store.add(merged)

    def collect(self) -> tuple[list[VideoRecord], dict[str, Any]]:
        keywords = [normalize_text(k) for k in self.platform_config.get("keywords", []) if normalize_text(k)]
        max_candidates = int(self.browser_config.get("max_candidates_per_platform", 1200))
        search_delay = float(self.browser_config.get("request_delay_seconds", 0.8))
        min_keyword_results = int(self.browser_config.get("minimum_keyword_result_count", 5))
        repeated_threshold = float(self.browser_config.get("repeated_result_jaccard_threshold", 0.88))
        empty_abort_after = max(1, int(self.browser_config.get("headless_abort_after_empty_keywords", 4)))

        checkpoint_batch_size = max(1, int(self.browser_config.get("checkpoint_batch_size", 20)))
        detail_delay_min = max(0.0, float(self.browser_config.get("detail_delay_min_seconds", 2.2)))
        detail_delay_max = max(detail_delay_min, float(self.browser_config.get("detail_delay_max_seconds", 4.8)))
        batch_rest_min = max(0.0, float(self.browser_config.get("batch_rest_min_seconds", 18)))
        batch_rest_max = max(batch_rest_min, float(self.browser_config.get("batch_rest_max_seconds", 40)))
        max_detail_attempts = max(1, int(self.browser_config.get("max_detail_attempts", 2)))
        failure_backoff_base = max(0.5, float(self.browser_config.get("failure_backoff_base_seconds", 3)))
        failure_backoff_max = max(failure_backoff_base, float(self.browser_config.get("failure_backoff_max_seconds", 45)))
        failure_cooldown_after = max(1, int(self.browser_config.get("consecutive_failure_cooldown_after", 4)))
        failure_abort_after = max(failure_cooldown_after + 1, int(self.browser_config.get("consecutive_failure_abort_after", 8)))
        cooldown_min = max(0.0, float(self.browser_config.get("cooldown_min_seconds", 45)))
        cooldown_max = max(cooldown_min, float(self.browser_config.get("cooldown_max_seconds", 90)))

        window, tz = compute_window(self.config)
        checkpoint = CollectionCheckpoint(self.platform, window, tz, self.config, self.platform_config)
        state = checkpoint.load()
        phase = "search"
        next_keyword_index = 0
        detail_keys: list[str] = []
        completed_detail_keys: set[str] = set()
        failed_attempts: dict[str, int] = {}
        processed_detail_count = 0
        empty_streak = 0

        if state:
            for raw in state.get("records", []):
                try:
                    self.store.add(video_record_from_dict(raw))
                except Exception as exc:
                    logging.debug("[%s] 忽略无法恢复的断点记录：%s", self.platform, exc)
            phase = str(state.get("phase", "search"))
            next_keyword_index = max(0, int(state.get("next_keyword_index", 0)))
            self.last_keyword_ids = set(str(x) for x in state.get("last_keyword_ids", []))
            detail_keys = [str(x) for x in state.get("detail_keys", [])]
            completed_detail_keys = set(str(x) for x in state.get("completed_detail_keys", []))
            failed_attempts = {str(k): int(v) for k, v in dict(state.get("failed_attempts", {})).items()}
            processed_detail_count = int(state.get("processed_detail_count", len(completed_detail_keys)))
            logging.info(
                "[%s] 已恢复断点：阶段=%s，搜索进度=%d/%d，详情已处理=%d，记录=%d",
                PLATFORM_LABELS[self.platform], phase, next_keyword_index, len(keywords),
                processed_detail_count, len(self.store)
            )

        logging.info(
            "[%s] 启动采集，关键词 %d 个，模式：%s（断点续跑与风控保护版）",
            PLATFORM_LABELS[self.platform], len(keywords),
            "后台兼容（屏幕外正常浏览器）" if self.is_background_offscreen else ("真正无头" if self.is_headless else "可见")
        )
        context = self.launch_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.on("response", self.response_handler)

        def save_progress(note: str = "") -> None:
            checkpoint.save(
                self.store.values(),
                phase=phase,
                next_keyword_index=next_keyword_index,
                last_keyword_ids=self.last_keyword_ids,
                detail_keys=detail_keys,
                completed_detail_keys=completed_detail_keys,
                failed_attempts=failed_attempts,
                processed_detail_count=processed_detail_count,
                note=note,
            )

        try:
            if phase == "search":
                for zero_index in range(next_keyword_index, len(keywords)):
                    index = zero_index + 1
                    keyword = keywords[zero_index]
                    if len(self.store) >= max_candidates:
                        logging.info("[%s] 已达到候选上限 %d", PLATFORM_LABELS[self.platform], max_candidates)
                        next_keyword_index = len(keywords)
                        break
                    self.current_keyword = keyword
                    logging.info("[%s] %d/%d 搜索：%s", PLATFORM_LABELS[self.platform], index, len(keywords), keyword)
                    keyword_finished = False
                    try:
                        keyword_store = self._collect_keyword_once(page, keyword, index, use_ui=False)
                        current_ids = self._id_set(keyword_store)
                        repeated = self._jaccard(current_ids, self.last_keyword_ids)
                        if len(keyword_store) < min_keyword_results or repeated >= repeated_threshold:
                            logging.warning(
                                "[%s] 关键词“%s”首轮仅 %d 条或与上一词重复 %.0f%%，改用搜索框重试",
                                PLATFORM_LABELS[self.platform], keyword, len(keyword_store), repeated * 100
                            )
                            self.save_screenshot(page, f"retry_keyword_{index}")
                            keyword_store = self._collect_keyword_once(page, keyword, index, use_ui=True)
                            current_ids = self._id_set(keyword_store)
                            repeated = self._jaccard(current_ids, self.last_keyword_ids)

                        before_count = len(self.store)
                        for record in keyword_store.values():
                            self.store.add(record)
                        added_count = len(self.store) - before_count
                        logging.info(
                            "[%s] 本词抓到 %d 条，搜索响应 %d 个，全局去重 %d，本词新增 %d，和上一词重复 %.0f%%",
                            PLATFORM_LABELS[self.platform], len(keyword_store), self.active_search_response_count,
                            len(self.store), added_count, repeated * 100
                        )
                        if current_ids:
                            self.last_keyword_ids = current_ids
                        if added_count <= 0 or repeated >= repeated_threshold:
                            empty_streak += 1
                            self.save_screenshot(page, f"stalled_search_{index}_{empty_streak}")
                        else:
                            empty_streak = 0
                        if self.is_unattended and empty_streak >= empty_abort_after:
                            raise EmptyDataError(
                                f"[{PLATFORM_LABELS[self.platform]}] 连续 {empty_streak} 个关键词没有产生新的搜索结果，"
                                f"当前全局候选 {len(self.store)} 条。已保存搜索断点；请运行 run_visible.bat。"
                            )
                        keyword_finished = True
                    except CollectorNeedsAttention:
                        next_keyword_index = zero_index
                        save_progress(f"搜索关键词 {index}/{len(keywords)} 遇到验证，未跳过本关键词")
                        raise
                    except PlaywrightTimeoutError:
                        logging.warning("[%s] 搜索页面超时：%s", PLATFORM_LABELS[self.platform], keyword)
                        self.save_screenshot(page, f"timeout_search_{index}")
                        keyword_finished = True
                    except Exception as exc:
                        logging.warning("[%s] 搜索失败 %s：%s", PLATFORM_LABELS[self.platform], keyword, exc)
                        self.save_screenshot(page, f"error_search_{index}")
                        keyword_finished = True
                    finally:
                        if keyword_finished:
                            next_keyword_index = index
                            save_progress(f"搜索关键词 {index}/{len(keywords)} 后保存")
                    time.sleep(search_delay + random.uniform(0.15, 0.8))

                records = self.store.values()
                filters = self.config.get("filters", {})
                detail_pool: list[VideoRecord] = []
                skipped_by_text = 0
                for record in records:
                    score, level, female, solo, sexy, reason = evaluate_target_match(record, filters)
                    record.target_match_score = score
                    record.match_level = level
                    record.female_text_signal = female
                    record.solo_text_signal = solo
                    record.sexy_style_signal = sexy
                    record.exclusion_reason = reason
                    if reason:
                        record.data_quality_notes.append(f"文本初筛排除：{reason}")
                        skipped_by_text += 1
                        continue
                    detail_pool.append(record)
                detail_pool.sort(
                    key=lambda r: (
                        -r.target_match_score,
                        0 if r.create_time is None else 1,
                        -(r.likes + r.comments * 3 + r.shares * 4 + r.favorites * 3),
                    )
                )
                max_details = min(int(self.browser_config.get("max_detail_visits_per_platform", 360)), len(detail_pool))
                detail_keys = [record.key() for record in detail_pool[:max_details]]
                phase = "detail"
                save_progress("搜索完成，已冻结详情补全顺序")
                logging.info(
                    "[%s] 搜索候选 %d 条；文字初筛可补全 %d 条，排除 %d 条；本轮详情上限 %d",
                    PLATFORM_LABELS[self.platform], len(records), len(detail_pool), skipped_by_text, max_details
                )

            if phase == "complete":
                logging.info("[%s] 本统计周已经补全完成，直接使用断点数据生成报告。", PLATFORM_LABELS[self.platform])
            else:
                records_by_key = {record.key(): record for record in self.store.values()}
                # Old/partial checkpoint safety: rebuild the order when it is missing.
                if not detail_keys:
                    filters = self.config.get("filters", {})
                    rebuilt: list[VideoRecord] = []
                    for record in self.store.values():
                        score, level, female, solo, sexy, reason = evaluate_target_match(record, filters)
                        record.target_match_score = score
                        record.match_level = level
                        record.female_text_signal = female
                        record.solo_text_signal = solo
                        record.sexy_style_signal = sexy
                        record.exclusion_reason = reason
                        if not reason:
                            rebuilt.append(record)
                    rebuilt.sort(key=lambda r: (-r.target_match_score, -(r.likes + r.comments * 3 + r.shares * 4 + r.favorites * 3)))
                    detail_keys = [r.key() for r in rebuilt[:int(self.browser_config.get("max_detail_visits_per_platform", 360))]]
                    save_progress("重建详情补全顺序")

                total_details = len(detail_keys)
                remaining = sum(1 for key in detail_keys if key not in completed_detail_keys and failed_attempts.get(key, 0) < max_detail_attempts)
                logging.info(
                    "[%s] 开始/继续详情补全：总计 %d，已完成 %d，本次待处理 %d；每 %d 条原子保存一次",
                    PLATFORM_LABELS[self.platform], total_details, len(completed_detail_keys), remaining, checkpoint_batch_size
                )
                detail_wait_ms = int(float(self.browser_config.get("detail_wait_seconds", 1.5)) * 1000)
                since_checkpoint = 0
                consecutive_failures = 0

                for position, key in enumerate(detail_keys, start=1):
                    if key in completed_detail_keys:
                        continue
                    if failed_attempts.get(key, 0) >= max_detail_attempts:
                        continue
                    record = records_by_key.get(key)
                    if not record or not record.url:
                        completed_detail_keys.add(key)
                        processed_detail_count += 1
                        since_checkpoint += 1
                        continue

                    record_succeeded = False
                    while failed_attempts.get(key, 0) < max_detail_attempts and not record_succeeded:
                        self.current_keyword = record.source_keyword
                        self.capture_mode = "detail"
                        self.active_video_id = record.video_id
                        try:
                            page.goto(record.url, wait_until="domcontentloaded")
                            page.wait_for_timeout(detail_wait_ms)
                            self.check_verification(page, f"detail_{position}")
                            detail_store = RecordStore(self.platform)
                            self.parse_embedded_json(page, f"detail:{record.video_id or position}", detail_store)
                            for candidate in detail_store.values():
                                if candidate.video_id == record.video_id:
                                    self.store.add(candidate)
                            self.parse_meta(page, record)
                            completed_detail_keys.add(key)
                            record_succeeded = True
                            consecutive_failures = 0
                        except CollectorNeedsAttention:
                            save_progress(f"详情 {position}/{total_details} 遇到验证，安全停机")
                            raise
                        except Exception as exc:
                            failed_attempts[key] = failed_attempts.get(key, 0) + 1
                            consecutive_failures += 1
                            logging.warning(
                                "[%s] 详情失败 %d/%d（本条第 %d/%d 次）：%s",
                                PLATFORM_LABELS[self.platform], position, total_details,
                                failed_attempts[key], max_detail_attempts, str(exc)[:180]
                            )
                            if failed_attempts[key] < max_detail_attempts:
                                backoff = min(
                                    failure_backoff_max,
                                    failure_backoff_base * (2 ** max(0, consecutive_failures - 1)),
                                )
                                logging.info("[%s] %.1f 秒后重试当前视频。", PLATFORM_LABELS[self.platform], backoff)
                                time.sleep(backoff + random.uniform(0.3, 1.8))
                            else:
                                record.data_quality_notes.append(f"详情补全失败，已尝试 {max_detail_attempts} 次")

                            if consecutive_failures == failure_cooldown_after:
                                cooldown = random.uniform(cooldown_min, cooldown_max)
                                logging.warning(
                                    "[%s] 连续失败 %d 次，冷却 %.0f 秒后继续。",
                                    PLATFORM_LABELS[self.platform], consecutive_failures, cooldown
                                )
                                save_progress("连续失败触发冷却")
                                time.sleep(cooldown)
                            if consecutive_failures >= failure_abort_after:
                                save_progress("连续失败达到停机阈值")
                                raise EmptyDataError(
                                    f"[{PLATFORM_LABELS[self.platform]}] 连续 {consecutive_failures} 次详情加载失败。"
                                    "程序已保存断点并停止，避免继续触发风控；稍后直接重跑即可续传。"
                                )
                        finally:
                            self.capture_mode = "idle"
                            self.active_video_id = ""

                    processed_detail_count += 1
                    since_checkpoint += 1

                    if since_checkpoint >= checkpoint_batch_size:
                        save_progress(f"详情批次保存：已处理 {processed_detail_count}/{total_details}")
                        logging.info(
                            "[%s] 断点已保存：已处理 %d/%d，成功 %d，最大重试失败 %d",
                            PLATFORM_LABELS[self.platform], processed_detail_count, total_details,
                            len(completed_detail_keys),
                            sum(1 for item in detail_keys if failed_attempts.get(item, 0) >= max_detail_attempts),
                        )
                        since_checkpoint = 0
                        if any(item not in completed_detail_keys and failed_attempts.get(item, 0) < max_detail_attempts for item in detail_keys):
                            rest = random.uniform(batch_rest_min, batch_rest_max)
                            logging.info("[%s] 批次冷却 %.0f 秒，降低连续访问风险。", PLATFORM_LABELS[self.platform], rest)
                            time.sleep(rest)

                    time.sleep(random.uniform(detail_delay_min, detail_delay_max))

                phase = "complete"
                save_progress("详情补全完成")
                logging.info(
                    "[%s] 详情补全结束：成功 %d/%d，达到最大重试仍失败 %d",
                    PLATFORM_LABELS[self.platform], len(completed_detail_keys), len(detail_keys),
                    sum(1 for key in detail_keys if failed_attempts.get(key, 0) >= max_detail_attempts)
                )
        except BaseException:
            try:
                save_progress("异常/中止前最后保存")
            except Exception as save_exc:
                logging.error("[%s] 异常前保存断点失败：%s", PLATFORM_LABELS[self.platform], save_exc)
            raise
        finally:
            self.capture_mode = "idle"
            context.close()

        final_records = self.store.values()
        diagnostics = {
            "platform": self.platform,
            "platform_label": PLATFORM_LABELS[self.platform],
            "keywords": keywords,
            "candidate_count": len(final_records),
            "with_create_time": sum(1 for r in final_records if r.create_time),
            "with_views": sum(1 for r in final_records if r.views > 0),
            "with_likes": sum(1 for r in final_records if r.likes > 0),
            "with_followers": sum(1 for r in final_records if r.followers > 0),
            "response_seen": self.response_seen,
            "response_parsed": self.response_parsed,
            "response_errors": self.response_errors,
            "verification_count": self.captcha_count,
            "checkpoint": str(checkpoint.path),
            "checkpoint_phase": phase,
            "detail_total": len(detail_keys),
            "detail_completed": len(completed_detail_keys),
            "detail_failed_max_retries": sum(1 for key in detail_keys if failed_attempts.get(key, 0) >= max_detail_attempts),
        }
        return final_records, diagnostics


def short_url(url: str, max_len: int = 90) -> str:
    return url if len(url) <= max_len else url[: max_len - 3] + "..."


def login_platform(playwright: Playwright, platform: str, config: Mapping[str, Any]) -> None:
    platform_config = config.get("platforms", {}).get(platform, {})
    collector = PlatformCollector(
        playwright,
        platform,
        config,
        platform_config,
        APP_DIR / "debug_screenshots" / "login",
        headless_override=False,
    )
    context = collector.launch_context()
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(PLATFORM_HOME[platform], wait_until="domcontentloaded")
        print(f"\n已打开 {PLATFORM_LABELS[platform]}。请在浏览器中正常登录，确认首页可用后回到此窗口按回车。")
        input()
    finally:
        context.close()


def content_text_blob(record: VideoRecord) -> str:
    """只包含视频/作者自身文字，不把我们的搜索关键词当成内容证据。"""
    return " ".join([record.title, record.author_name, record.author_id, record.music, " ".join(record.hashtags)]).casefold()


def text_blob(record: VideoRecord) -> str:
    return f"{content_text_blob(record)} {record.source_keyword}".casefold()


def compute_dance_relevance(record: VideoRecord) -> float:
    # Search keyword is provenance, not proof. Most of the score must come from the
    # video's own title, hashtags, author text or music.
    content = content_text_blob(record)
    source = record.source_keyword.casefold()
    matches = sum(1 for term in DANCE_TERMS if term.casefold() in content)
    score = min(70.0, matches * 15.0)
    if record.hashtags:
        tag_text = " ".join(record.hashtags).casefold()
        if any(term.casefold() in tag_text for term in DANCE_TERMS):
            score += 20.0
    if any(term.casefold() in source for term in DANCE_TERMS):
        score += 10.0
    return min(100.0, score)


def contains_any(text: str, terms: Iterable[str]) -> bool:
    low = text.casefold()
    return any(normalize_text(term).casefold() in low for term in terms if normalize_text(term))



def matching_terms(text: str, terms: Iterable[str]) -> list[str]:
    low = normalize_text(text).casefold()
    return [term for term in terms if normalize_text(term).casefold() in low]


def first_matching_term(text: str, terms: Iterable[str]) -> str:
    matches = matching_terms(text, terms)
    return matches[0] if matches else ""


def evaluate_target_match(record: VideoRecord, filters: Mapping[str, Any]) -> tuple[float, str, bool, bool, bool, str]:
    """Conservative text relevance filter.

    Search terms are only a small recall hint. The old implementation gave up to 74
    points merely because a random recommendation was captured under a good keyword;
    that is why landscapes, films and tutorials ranked highly.
    """
    content = content_text_blob(record)
    source = normalize_text(record.source_keyword).casefold()

    minor_terms = filters.get("minor_terms", [])
    male_terms = filters.get("male_terms", list(MALE_EXCLUDE_TERMS))
    group_terms = filters.get("group_terms", list(GROUP_EXCLUDE_TERMS))
    format_terms = filters.get("format_exclude_terms", list(FORMAT_EXCLUDE_TERMS))
    non_human_terms = filters.get("non_human_terms", list(NON_HUMAN_EXCLUDE_TERMS))
    female_terms = filters.get("female_text_terms", list(FEMALE_TARGET_TERMS))
    solo_terms = filters.get("solo_terms", list(SOLO_TARGET_TERMS))
    sexy_terms = filters.get("sexy_style_terms", list(SEXY_STYLE_TERMS))

    for label, terms in (
        ("疑似未成年人文字信号", minor_terms),
        ("男性舞者文字信号", male_terms),
        ("多人/双人/群舞文字信号", group_terms),
        ("教程/合集/搬运文字信号", format_terms),
        ("AI/动漫/虚拟角色文字信号", non_human_terms),
    ):
        hit = first_matching_term(content, terms)
        if hit:
            return 0.0, "排除", False, False, False, f"{label}：{hit}"

    female_signal = contains_any(content, female_terms)
    solo_signal = contains_any(content, solo_terms)
    sexy_signal = contains_any(content, sexy_terms)
    dance_hits = sum(1 for term in DANCE_TERMS if term.casefold() in content)
    sexy_hits = len(matching_terms(content, sexy_terms))
    female_hits = len(matching_terms(content, female_terms))
    solo_hits = len(matching_terms(content, solo_terms))

    score = 0.0
    score += min(32.0, sexy_hits * 10.0)
    score += min(20.0, female_hits * 10.0)
    score += min(18.0, solo_hits * 9.0)
    score += min(24.0, dance_hits * 8.0)
    if contains_any(content, [
        "辣妹热舞", "小姐姐热舞", "单人热舞", "御姐热舞", "扭胯舞",
        "摇胯舞", "高跟鞋舞", "椅子舞", "纯御舞", "性感纯欲舞"
    ]):
        score += 12.0

    # Retrieval hint only. It can never turn unrelated content into a strong match.
    if any(term.casefold() in source for term in DANCE_TERMS):
        score += 4.0
    if contains_any(source, sexy_terms):
        score += 4.0
    if contains_any(source, female_terms):
        score += 2.0

    score = min(100.0, score)
    if score >= 62:
        level = "A-强匹配"
    elif score >= 34:
        level = "B-较匹配"
    else:
        level = "C-宽松候选"
    return score, level, female_signal, solo_signal, sexy_signal, ""


def topic_tokens(record: VideoRecord) -> set[str]:
    tokens: set[str] = set()
    raw_parts = [record.music, record.source_keyword, *record.hashtags, record.title]
    for raw in raw_parts:
        text = normalize_text(raw).casefold()
        if not text:
            continue
        for word in WORD_PATTERN.findall(text):
            cleaned = word.strip("_- ").casefold()
            if cleaned and cleaned not in GENERIC_TOPIC_STOPWORDS and len(cleaned) >= 3:
                tokens.add(cleaned)
        for run in CJK_RUN_PATTERN.findall(text):
            if run not in GENERIC_TOPIC_STOPWORDS:
                tokens.add(run)
            # 加入 2～4 字片段，提高中英文跨平台音乐名/挑战名匹配能力。
            for n in (2, 3, 4):
                if len(run) >= n:
                    for i in range(len(run) - n + 1):
                        piece = run[i : i + n]
                        if piece not in GENERIC_TOPIC_STOPWORDS:
                            tokens.add(piece)
        for tag in HASHTAG_PATTERN.findall(text):
            cleaned = tag.casefold()
            if cleaned not in GENERIC_TOPIC_STOPWORDS:
                tokens.add(cleaned)
    return tokens


def normalized_music(music: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", normalize_text(music).casefold())


def assign_cross_platform_scores(records: list[VideoRecord]) -> None:
    by_platform: dict[str, list[tuple[VideoRecord, set[str], str]]] = defaultdict(list)
    for record in records:
        by_platform[record.platform].append((record, topic_tokens(record), normalized_music(record.music)))

    for record in records:
        tokens = topic_tokens(record)
        music = normalized_music(record.music)
        matched_platforms: set[str] = set()
        for other_platform, rows in by_platform.items():
            if other_platform == record.platform:
                continue
            for other, other_tokens, other_music in rows:
                music_match = bool(music and other_music and len(music) >= 4 and (music == other_music or music in other_music or other_music in music))
                if tokens and other_tokens:
                    intersection = len(tokens & other_tokens)
                    union = len(tokens | other_tokens)
                    jaccard = intersection / union if union else 0.0
                else:
                    jaccard = 0.0
                if music_match or jaccard >= 0.18 or len(tokens & other_tokens) >= 2:
                    matched_platforms.add(other_platform)
                    break
        if len(matched_platforms) >= 2:
            record.cross_platform_score = 100.0
        elif len(matched_platforms) == 1:
            record.cross_platform_score = 70.0
        else:
            record.cross_platform_score = 0.0


def percentile_ranks(values: list[float]) -> list[float]:
    if not values:
        return []
    if len(values) == 1:
        return [100.0]
    sorted_pairs = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(sorted_pairs):
        j = i
        while j + 1 < len(sorted_pairs) and sorted_pairs[j + 1][1] == sorted_pairs[i][1]:
            j += 1
        average_position = (i + j) / 2
        percentile = average_position / (len(values) - 1) * 100.0
        for k in range(i, j + 1):
            ranks[sorted_pairs[k][0]] = percentile
        i = j + 1
    return ranks


def score_records(records: list[VideoRecord], config: Mapping[str, Any], window: DateWindow, tz: ZoneInfo) -> tuple[list[VideoRecord], dict[str, list[VideoRecord]]]:
    filters = config.get("filters", {})
    min_target = float(filters.get("minimum_target_match", 8))
    min_dance = float(filters.get("minimum_dance_relevance", 12))

    eligible: list[VideoRecord] = []
    for record in records:
        record.dance_relevance = compute_dance_relevance(record)
        score, level, female, solo, sexy, reason = evaluate_target_match(record, filters)
        record.target_match_score = score
        record.match_level = level
        record.female_text_signal = female
        record.solo_text_signal = solo
        record.sexy_style_signal = sexy
        record.exclusion_reason = reason
        if reason:
            if not any(reason in note for note in record.data_quality_notes):
                record.data_quality_notes.append(f"文本初筛排除：{reason}")
            continue
        if record.dance_relevance < min_dance:
            record.data_quality_notes.append(f"舞蹈相关度低于阈值 {min_dance:g}，未进入候选榜")
            continue
        if record.target_match_score < min_target:
            record.data_quality_notes.append(f"目标匹配度低于阈值 {min_target:g}，未进入候选榜")
            continue
        local_time = record.local_create_time(tz)
        if not local_time:
            record.data_quality_notes.append("缺少可验证发布时间，未进入正式周榜")
            continue
        if not (window.start <= local_time < window.end):
            continue

        age_hours = max((window.end - local_time).total_seconds() / 3600.0, 1.0)
        record.likes_per_hour = record.likes / age_hours if record.likes > 0 else 0.0
        record.velocity_per_hour = record.likes_per_hour
        # 旧字段保留用于兼容，但不再用缺失的粉丝/播放构造失真互动率。
        total_interactions = record.likes + record.comments + record.shares + record.favorites
        if record.views > 0:
            record.engagement_rate = total_interactions / record.views
            record.engagement_basis = "views"
        else:
            record.engagement_rate = 0.0
            record.engagement_basis = "not_scored"
        eligible.append(record)

    by_platform: dict[str, list[VideoRecord]] = defaultdict(list)
    for record in eligible:
        by_platform[record.platform].append(record)

    weights = config.get("scoring", {})
    w_likes = float(weights.get("likes", 0.25))
    w_comments = float(weights.get("comments", 0.10))
    w_shares = float(weights.get("shares", 0.20))
    w_favorites = float(weights.get("favorites", 0.15))
    w_velocity = float(weights.get("likes_velocity", 0.20))
    w_target = float(weights.get("target_match", 0.10))

    for platform, rows in by_platform.items():
        likes = [math.log1p(r.likes) for r in rows]
        comments = [math.log1p(r.comments) for r in rows]
        shares = [math.log1p(r.shares) for r in rows]
        favorites = [math.log1p(r.favorites) for r in rows]
        velocity = [math.log1p(r.likes_per_hour) for r in rows]
        targets = [r.target_match_score for r in rows]
        pr_likes = percentile_ranks(likes)
        pr_comments = percentile_ranks(comments)
        pr_shares = percentile_ranks(shares)
        pr_favorites = percentile_ranks(favorites)
        pr_velocity = percentile_ranks(velocity)
        pr_target = percentile_ranks(targets)
        for i, record in enumerate(rows):
            record.like_percentile = pr_likes[i]
            record.comment_percentile = pr_comments[i]
            record.share_percentile = pr_shares[i]
            record.favorite_percentile = pr_favorites[i]
            record.share_favorite_percentile = (pr_shares[i] + pr_favorites[i]) / 2
            record.velocity_percentile = pr_velocity[i]
            record.target_percentile = pr_target[i]
            record.final_score = round(
                record.like_percentile * w_likes
                + record.comment_percentile * w_comments
                + record.share_percentile * w_shares
                + record.favorite_percentile * w_favorites
                + record.velocity_percentile * w_velocity
                + record.target_percentile * w_target,
                2,
            )
        rows.sort(
            key=lambda r: (r.final_score, r.target_match_score, r.likes, r.shares, r.favorites),
            reverse=True,
        )
        for rank, record in enumerate(rows, start=1):
            record.rank = rank

    eligible.sort(key=lambda r: (r.platform, r.rank))
    return eligible, by_platform


def data_quality(record: VideoRecord) -> str:
    missing: list[str] = []
    if not record.create_time:
        missing.append("发布时间")
    if record.likes <= 0:
        missing.append("点赞")
    if record.comments <= 0:
        missing.append("评论")
    if record.shares <= 0:
        missing.append("分享")
    if record.favorites <= 0:
        missing.append("收藏")
    if missing:
        return "缺：" + "、".join(missing)
    return "核心字段完整"


REPORT_COLUMNS = [
    ("排名", "rank"),
    ("总分", "final_score"),
    ("匹配等级", "match_level"),
    ("目标匹配分", "target_match_score"),
    ("发布时间", "publish_time"),
    ("标题/文案", "title"),
    ("作者", "author_name"),
    ("作者ID", "author_id"),
    ("原视频链接", "url"),
    ("点赞", "likes"),
    ("评论", "comments"),
    ("分享", "shares"),
    ("收藏", "favorites"),
    ("发布后每小时点赞", "likes_per_hour"),
    ("点赞百分位", "like_percentile"),
    ("评论百分位", "comment_percentile"),
    ("分享百分位", "share_percentile"),
    ("收藏百分位", "favorite_percentile"),
    ("速度百分位", "velocity_percentile"),
    ("匹配度百分位", "target_percentile"),
    ("女性文字信号", "female_text_signal"),
    ("单人文字信号", "solo_text_signal"),
    ("性感/妩媚风格信号", "sexy_style_signal"),
    ("音乐", "music"),
    ("话题", "hashtags"),
    ("来源关键词", "source_keyword"),
    ("排除原因", "exclusion_reason"),
    ("人工复核", "manual_review"),
    ("数据质量", "data_quality"),
    ("备注", "notes"),
]


def report_row(record: VideoRecord, tz: ZoneInfo) -> dict[str, Any]:
    local_time = record.local_create_time(tz)
    return {
        "rank": record.rank,
        "final_score": record.final_score,
        "match_level": record.match_level,
        "target_match_score": round(record.target_match_score, 2),
        "publish_time": local_time.strftime("%Y-%m-%d %H:%M:%S") if local_time else "",
        "title": record.title,
        "author_name": record.author_name,
        "author_id": record.author_id,
        "url": record.url,
        "likes": record.likes,
        "comments": record.comments,
        "shares": record.shares,
        "favorites": record.favorites,
        "likes_per_hour": round(record.likes_per_hour, 2),
        "like_percentile": round(record.like_percentile, 2),
        "comment_percentile": round(record.comment_percentile, 2),
        "share_percentile": round(record.share_percentile, 2),
        "favorite_percentile": round(record.favorite_percentile, 2),
        "velocity_percentile": round(record.velocity_percentile, 2),
        "target_percentile": round(record.target_percentile, 2),
        "female_text_signal": "有" if record.female_text_signal else "未发现（需看图）",
        "solo_text_signal": "有" if record.solo_text_signal else "未发现（需看图）",
        "sexy_style_signal": "有" if record.sexy_style_signal else "主要来自搜索词",
        "music": record.music,
        "hashtags": " #".join(record.hashtags) if record.hashtags else "",
        "source_keyword": record.source_keyword,
        "exclusion_reason": record.exclusion_reason,
        "manual_review": "待看封面/视频",
        "data_quality": data_quality(record),
        "notes": "；".join(unique_preserve(record.data_quality_notes)),
        "thumbnail": record.thumbnail,
    }


def write_csv(path: Path, records: list[VideoRecord], tz: ZoneInfo) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[key for _, key in REPORT_COLUMNS])
        writer.writeheader()
        for record in records:
            row = report_row(record, tz)
            writer.writerow({key: row.get(key, "") for _, key in REPORT_COLUMNS})


def style_sheet(ws, max_row: int, max_col: int) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    top_fill = PatternFill("solid", fgColor="FFF2CC")
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"
    for row in range(2, min(max_row, 11) + 1):
        for col in range(1, max_col + 1):
            ws.cell(row=row, column=col).fill = top_fill
    for row in ws.iter_rows(min_row=2, max_row=max_row):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    widths = {
        1: 7, 2: 9, 3: 14, 4: 13, 5: 20, 6: 52, 7: 18, 8: 18, 9: 56,
        10: 12, 11: 12, 12: 12, 13: 12, 14: 18, 15: 13, 16: 13, 17: 13,
        18: 13, 19: 13, 20: 14, 21: 16, 22: 16, 23: 19, 24: 24, 25: 34,
        26: 18, 27: 24, 28: 18, 29: 20, 30: 46,
    }
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = width
    if max_row >= 2:
        ws.conditional_formatting.add(
            f"B2:B{max_row}",
            ColorScaleRule(start_type="min", start_color="F8696B", mid_type="percentile", mid_value=50, mid_color="FFEB84", end_type="max", end_color="63BE7B"),
        )


def write_excel(path: Path, top_by_platform: Mapping[str, list[VideoRecord]], all_eligible: list[VideoRecord], all_candidates: list[VideoRecord], diagnostics: Mapping[str, Any], window: DateWindow, tz: ZoneInfo, config: Mapping[str, Any]) -> None:
    platforms = enabled_platforms(config)
    top_n = int(config.get("top_n_per_platform", 50))
    total = top_n * len(platforms)
    wb = Workbook()
    summary = wb.active
    summary.title = "说明与汇总"
    summary["A1"] = f"{' / '.join(PLATFORM_LABELS[p] for p in platforms)} 每周舞蹈 Top{total}"
    summary["A1"].font = Font(size=18, bold=True)
    summary["A3"] = "统计窗口"
    summary["B3"] = window.label
    summary["A4"] = "窗口模式"
    summary["B4"] = window.mode
    summary["A5"] = "生成时间"
    summary["B5"] = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    summary["A6"] = "评分"
    summary["B6"] = "点赞25% + 分享20% + 收藏15% + 评论10% + 每小时点赞20% + 目标匹配度10%"
    summary["A7"] = "重要限制"
    summary["B7"] = "脚本先排除明确男性、多人、教程、未成年人、AI/动漫文字信号，再输出100条大候选池。单人、女性、成年和风格仍必须人工看封面/视频复核。"
    summary["A9"] = "平台"
    summary["B9"] = "候选数"
    summary["C9"] = "时间窗口内合格数"
    summary["D9"] = "正式榜数量"
    for cell in summary[9]:
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.font = Font(color="FFFFFF", bold=True)
    for idx, platform in enumerate(platforms, start=10):
        diag = diagnostics.get(platform, {}) if isinstance(diagnostics, Mapping) else {}
        summary.cell(idx, 1, PLATFORM_LABELS[platform])
        summary.cell(idx, 2, int(diag.get("candidate_count", 0)))
        summary.cell(idx, 3, len([r for r in all_eligible if r.platform == platform]))
        summary.cell(idx, 4, len(top_by_platform.get(platform, [])))
    summary.column_dimensions["A"].width = 20
    summary.column_dimensions["B"].width = 82
    summary.column_dimensions["C"].width = 20
    summary.column_dimensions["D"].width = 18
    summary["B7"].alignment = Alignment(wrap_text=True)

    def add_record_sheet(name: str, records: list[VideoRecord]) -> None:
        ws = wb.create_sheet(name)
        ws.append([label for label, _ in REPORT_COLUMNS])
        for record in records:
            row = report_row(record, tz)
            ws.append([row.get(key, "") for _, key in REPORT_COLUMNS])
        for row_idx in range(2, ws.max_row + 1):
            link_cell = ws.cell(row=row_idx, column=9)
            if link_cell.value:
                link_cell.hyperlink = str(link_cell.value)
                link_cell.font = Font(color="0563C1", underline="single")
            for col in range(2, 5):
                ws.cell(row=row_idx, column=col).number_format = "0.00"
            for col in range(14, 21):
                ws.cell(row=row_idx, column=col).number_format = "0.00"
        style_sheet(ws, ws.max_row, ws.max_column)

    for platform in platforms:
        add_record_sheet(f"{PLATFORM_LABELS[platform]}_Top{top_n}", top_by_platform.get(platform, []))
    add_record_sheet("窗口内全部合格", sorted(all_eligible, key=lambda r: (r.platform, r.rank)))

    # 全候选用于排查为什么某条未进榜；不强行给缺时间记录排名。
    all_sorted = sorted(all_candidates, key=lambda r: (r.platform, -(r.likes + r.comments + r.views)))
    add_record_sheet("全部候选_含未入榜", all_sorted)
    wb.save(path)


def html_table(records: list[VideoRecord], tz: ZoneInfo) -> str:
    rows: list[str] = []
    for record in records:
        r = report_row(record, tz)
        thumb = f'<img loading="lazy" src="{html.escape(record.thumbnail)}" alt="封面">' if record.thumbnail else '<div class="noimg">无封面</div>'
        title = html.escape(record.title or "（无标题）")
        author = html.escape(record.author_name or record.author_id or "未知")
        link = html.escape(record.url)
        tags = html.escape(" #".join(record.hashtags))
        signals = " / ".join([
            "女性词✓" if record.female_text_signal else "女性词?",
            "单人词✓" if record.solo_text_signal else "单人词?",
            "风格词✓" if record.sexy_style_signal else "风格来自搜索词",
        ])
        rows.append(
            f"""
            <article class="card" data-url="{link}" data-rank="{record.rank}">
              <label class="keep"><input type="checkbox" checked> 保留候选</label>
              <a class="cover" href="{link}" target="_blank" rel="noopener">{thumb}</a>
              <div class="cardbody">
                <div class="topline"><b>#{record.rank}</b><span class="score">{record.final_score:.2f}</span><span class="level">{html.escape(record.match_level)}</span></div>
                <a class="title" href="{link}" target="_blank" rel="noopener">{title}</a>
                <div class="author">{author}</div>
                <div class="stats">赞 {record.likes:,}　评 {record.comments:,}　分享 {record.shares:,}　收藏 {record.favorites:,}</div>
                <div class="stats">每小时点赞 {record.likes_per_hour:,.0f}　匹配分 {record.target_match_score:.0f}</div>
                <div class="signals">{html.escape(signals)}</div>
                <div class="meta">{html.escape(r['publish_time'])}｜来源：{html.escape(record.source_keyword)}</div>
                <div class="meta">{html.escape(record.music)} {tags}</div>
              </div>
            </article>
            """
        )
    if not rows:
        return '<div class="empty">没有足够的可验证候选，请查看诊断文件。</div>'
    return "\n".join(rows)


def write_html(path: Path, top_by_platform: Mapping[str, list[VideoRecord]], diagnostics: Mapping[str, Any], window: DateWindow, tz: ZoneInfo, config: Mapping[str, Any]) -> None:
    platforms = enabled_platforms(config)
    total = int(config.get("top_n_per_platform", 100)) * len(platforms)
    title = f"{' / '.join(PLATFORM_LABELS[p] for p in platforms)} 单人女性热舞候选 Top{total}"
    cards = "".join(html_table(top_by_platform.get(platform, []), tz) for platform in platforms)
    diag_text = html.escape(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
*{{box-sizing:border-box}} body{{font-family:Arial,"Microsoft YaHei",sans-serif;margin:0;background:#f3f5f8;color:#17212b}}
header{{background:#172b4d;color:white;padding:22px 28px;position:sticky;top:0;z-index:9}} header h1{{margin:0 0 8px;font-size:25px}}
header p{{margin:4px 0;color:#dce6f2}} main{{padding:18px}}
.notice{{background:#fff6d9;border-left:5px solid #d69e2e;padding:13px 15px;margin-bottom:14px;line-height:1.65}}
.toolbar{{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 16px;position:sticky;top:105px;z-index:8;background:#f3f5f8;padding:8px 0}}
button{{border:0;border-radius:7px;padding:10px 15px;cursor:pointer;font-weight:700;background:#1f4e78;color:white}}
button.secondary{{background:#64748b}} #counter{{padding:10px 0;font-weight:700}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(285px,1fr));gap:15px}}
.card{{background:white;border:2px solid transparent;border-radius:10px;overflow:hidden;box-shadow:0 2px 9px rgba(0,0,0,.08);position:relative}}
.card.removed{{opacity:.35;border-color:#dc3545}} .card.hidden{{display:none}}
.keep{{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.72);color:white;padding:6px 9px;border-radius:6px;z-index:2;font-size:13px}}
.cover{{display:block;background:#ddd;height:360px}} .cover img{{width:100%;height:100%;object-fit:cover;display:block}} .noimg{{height:100%;display:grid;place-items:center;color:#666}}
.cardbody{{padding:12px}} .topline{{display:flex;gap:8px;align-items:center;margin-bottom:8px}} .score{{font-size:20px;font-weight:800;color:#157347}}
.level{{font-size:12px;background:#e8eef5;padding:4px 7px;border-radius:10px}} .title{{display:block;color:#0b61a4;font-weight:700;line-height:1.45;text-decoration:none}}
.author,.meta,.signals{{font-size:12px;color:#66788a;margin-top:6px;line-height:1.4}} .stats{{font-size:13px;margin-top:7px}} details{{margin-top:22px;background:white;padding:14px}} pre{{white-space:pre-wrap;word-break:break-all;font-size:12px}}
@media(max-width:700px){{header{{position:static}} .toolbar{{top:0}} .cover{{height:430px}}}}
</style></head>
<body><header><h1>{html.escape(title)}</h1><p>统计窗口：{html.escape(window.label)}</p><p>评分：点赞25% + 分享20% + 收藏15% + 评论10% + 每小时点赞20% + 目标匹配度10%</p></header>
<main><div class="notice"><b>用途：</b>这是100条“大候选池”，方便你人工删到最终Top50。程序只采集真正的搜索结果，并排除标题中明确的男性、多人/双人/群舞、教程/合集、未成年人及AI/动漫内容；但不会仅凭外貌自动断言性别或成年，仍要点开视频复核。</div>
<div class="toolbar"><button id="showAll">显示全部</button><button id="hideRemoved" class="secondary">隐藏已删除</button><button id="exportCsv">导出保留链接CSV</button><button id="reset" class="secondary">重置选择</button><span id="counter"></span></div>
<div class="grid">{cards}</div>
<details><summary>采集诊断</summary><pre>{diag_text}</pre></details></main>
<script>
const cards=[...document.querySelectorAll('.card')];
const key='douyin_top100_review_'+location.pathname;
let state={{}}; try{{state=JSON.parse(localStorage.getItem(key)||'{{}}')}}catch(e){{state={{}}}}
function update(){{let kept=0; cards.forEach(c=>{{const cb=c.querySelector('input'); const url=c.dataset.url; if(url in state) cb.checked=!!state[url]; c.classList.toggle('removed',!cb.checked); if(cb.checked) kept++;}}); document.getElementById('counter').textContent=`保留 ${{kept}} / ${{cards.length}} 条`; try{{localStorage.setItem(key,JSON.stringify(state))}}catch(e){{}}}}
cards.forEach(c=>c.querySelector('input').addEventListener('change',e=>{{state[c.dataset.url]=e.target.checked; update();}}));
document.getElementById('hideRemoved').onclick=()=>cards.forEach(c=>c.classList.toggle('hidden',!c.querySelector('input').checked));
document.getElementById('showAll').onclick=()=>cards.forEach(c=>c.classList.remove('hidden'));
document.getElementById('reset').onclick=()=>{{if(confirm('确认恢复全部为保留状态？')){{state={{}}; cards.forEach(c=>c.querySelector('input').checked=true); update();}}}};
document.getElementById('exportCsv').onclick=()=>{{const rows=[['排名','视频链接','标题']]; cards.filter(c=>c.querySelector('input').checked).forEach(c=>rows.push([c.dataset.rank,c.dataset.url,c.querySelector('.title').textContent.trim()])); const csv=String.fromCharCode(65279)+rows.map(r=>r.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\\r\\n'); const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([csv],{{type:'text/csv;charset=utf-8'}})); a.download='douyin_top100_kept.csv'; a.click(); URL.revokeObjectURL(a.href);}};
update();
</script></body></html>"""
    path.write_text(doc, encoding="utf-8")


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def build_outputs(
    config: Mapping[str, Any],
    window: DateWindow,
    tz: ZoneInfo,
    all_candidates: list[VideoRecord],
    diagnostics: dict[str, Any],
) -> Path:
    eligible, by_platform = score_records(all_candidates, config, window, tz)
    top_n = int(config.get("top_n_per_platform", 50))
    platforms = enabled_platforms(config)
    top_by_platform: dict[str, list[VideoRecord]] = {}
    for platform in platforms:
        top_by_platform[platform] = by_platform.get(platform, [])[:top_n]

    output_root = APP_DIR / str(config.get("output_root", "output"))
    output_dir = output_root / window.slug
    output_dir.mkdir(parents=True, exist_ok=True)
    basename = report_basename(config)

    write_excel(
        output_dir / f"{basename}.xlsx",
        top_by_platform,
        eligible,
        all_candidates,
        diagnostics,
        window,
        tz,
        config,
    )
    write_html(output_dir / f"{basename}.html", top_by_platform, diagnostics, window, tz, config)
    for platform in platforms:
        write_csv(output_dir / f"{platform}_top{top_n}.csv", top_by_platform[platform], tz)
    save_json(output_dir / "all_candidates.json", [r.to_dict(tz) for r in all_candidates])
    save_json(output_dir / "ranked_eligible.json", [r.to_dict(tz) for r in eligible])
    save_json(output_dir / "collection_diagnostics.json", diagnostics)

    logging.info("报告已生成：%s", output_dir)
    for platform in platforms:
        logging.info("%s 正式榜：%d 条", PLATFORM_LABELS[platform], len(top_by_platform[platform]))
    return output_dir


def demo_records(window: DateWindow, platforms: Iterable[str]) -> list[VideoRecord]:
    records: list[VideoRecord] = []
    topics = ["扇风舞", "梦的翅膀受了伤", "电摆舞", "Apple Dance", "Dai Dai"]
    for platform_index, platform in enumerate(platforms):
        for i in range(1, 131):
            topic = topics[i % len(topics)]
            created = window.end - timedelta(hours=6 + (i * 2) % 150)
            author_id = f"demo_{platform}_{i:02d}"
            video_id = f"{platform_index + 7}{i:018d}"
            records.append(
                VideoRecord(
                    platform=platform,
                    video_id=video_id,
                    url=build_platform_url(platform, video_id, author_id),
                    title=f"#辣妹热舞 #小姐姐 #单人热舞 第{i}条演示数据",
                    author_name=f"演示作者{i:02d}",
                    author_id=author_id,
                    create_time=created.astimezone(timezone.utc),
                    views=50_000 + i * i * 3_700 + platform_index * 10_000,
                    likes=3_000 + i * 731,
                    comments=80 + i * 17,
                    shares=40 + i * 9,
                    favorites=70 + i * 13,
                    followers=10_000 + i * 1_200,
                    music=topic,
                    hashtags=[topic, "舞蹈挑战"],
                    source_keyword="单人美女热舞" if platform != "tiktok" else "solo sexy dance",
                    data_sources={"demo"},
                )
            )
    return records


def run_collection(config: Mapping[str, Any], visible: bool = False, background: bool = False) -> Path:
    window, tz = compute_window(config)
    logging.info("统计窗口：%s", window.label)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    debug_dir = APP_DIR / "debug_screenshots" / window.slug
    diagnostics: dict[str, Any] = {
        "generated_at": datetime.now(tz).isoformat(),
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat(), "mode": window.mode},
            }
    all_candidates: list[VideoRecord] = []

    with acquire_run_lock(window, config):
        with sync_playwright() as playwright:
            for platform in enabled_platforms(config):
                platform_config = config.get("platforms", {}).get(platform, {})
                if not platform_config.get("enabled", True):
                    continue
                collector = PlatformCollector(
                    playwright,
                    platform,
                    config,
                    platform_config,
                    debug_dir,
                    headless_override=False if visible else None,
                    background_offscreen=bool(background and not visible),
                )
                try:
                    records, diag = collector.collect()
                    all_candidates.extend(records)
                    diagnostics[platform] = diag
                except CollectorNeedsAttention as exc:
                    logging.error("[%s] 采集已主动停止：%s", PLATFORM_LABELS[platform], exc)
                    diagnostics[platform] = {
                        "platform": platform,
                        "error": str(exc),
                        "candidate_count": len(collector.store),
                        "verification_count": collector.captcha_count,
                        "needs_attention": True,
                    }
                    save_json(debug_dir / "collection_stopped.json", diagnostics)
                    raise
                except Exception as exc:
                    logging.error("[%s] 平台采集失败：%s", PLATFORM_LABELS[platform], exc)
                    logging.debug(traceback.format_exc())
                    diagnostics[platform] = {"platform": platform, "error": str(exc), "candidate_count": 0}

    return build_outputs(config, window, tz, all_candidates, diagnostics)


def command_progress(config: Mapping[str, Any]) -> None:
    window, tz = compute_window(config)
    found = False
    for platform in enabled_platforms(config):
        manager = CollectionCheckpoint(platform, window, tz, config, config.get("platforms", {}).get(platform, {}))
        state = manager.load()
        if not state:
            print(f"{PLATFORM_LABELS[platform]}：当前统计周没有可恢复断点。")
            continue
        found = True
        print(
            f"{PLATFORM_LABELS[platform]}：阶段={state.get('phase')}，"
            f"搜索={state.get('next_keyword_index', 0)}/{len(config.get('platforms', {}).get(platform, {}).get('keywords', []))}，"
            f"详情已处理={state.get('processed_detail_count', 0)}/{len(state.get('detail_keys', []))}，"
            f"成功={len(state.get('completed_detail_keys', []))}，记录={state.get('record_count', 0)}\n"
            f"断点文件：{manager.path}"
        )
    if not found:
        print("没有发现当前统计周的断点。")


def command_reset_progress(config: Mapping[str, Any], yes: bool) -> None:
    if not yes:
        raise ValueError("重置会删除当前统计周的断点。请使用 reset-progress --yes 明确确认。")
    window, tz = compute_window(config)
    lock_path = DATA_DIR / "locks" / f"weekly_{window.slug}.lock"
    if lock_path.exists():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            lock = {}
        pid = int(lock.get("pid", 0) or 0)
        if _process_is_alive(pid):
            raise CollectorNeedsAttention(f"采集任务 PID {pid} 仍在运行，不能重置断点。请先正常关闭它。")
        lock_path.unlink(missing_ok=True)
    for platform in enabled_platforms(config):
        manager = CollectionCheckpoint(platform, window, tz, config, config.get("platforms", {}).get(platform, {}))
        manager.delete()
        print(f"已删除 {PLATFORM_LABELS[platform]} 当前统计周断点：{manager.path}")


def command_login(config: Mapping[str, Any], platform: str) -> None:
    platforms = enabled_platforms(config) if platform == "all" else (platform,)
    with sync_playwright() as playwright:
        for item in platforms:
            login_platform(playwright, item, config)
    print("登录会话已保存到当前项目的 data\\profiles 目录。")


def command_demo(config: Mapping[str, Any]) -> Path:
    window, tz = compute_window(config)
    platforms = enabled_platforms(config)
    records = demo_records(window, platforms)
    diagnostics = {
        "generated_at": datetime.now(tz).isoformat(),
        "demo": True,
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat(), "mode": window.mode},
    }
    for platform in platforms:
        diagnostics[platform] = {"candidate_count": sum(1 for r in records if r.platform == platform), "demo": True}
    output = build_outputs(config, window, tz, records, diagnostics)
    print(f"演示报告已生成：{output}")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抖音每周单人女性热舞候选 Top100")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="执行真实采集并生成报告")
    mode_group = run_parser.add_mutually_exclusive_group()
    mode_group.add_argument("--visible", action="store_true", help="显示浏览器，用于人工完成验证或排查空数据")
    mode_group.add_argument("--background", action="store_true", help="使用屏幕外正常浏览器后台运行，兼容抖音搜索")
    sub.add_parser("progress", help="查看当前统计周的断点进度")
    reset_parser = sub.add_parser("reset-progress", help="删除当前统计周断点并从头采集")
    reset_parser.add_argument("--yes", action="store_true", help="确认删除当前统计周断点")
    login = sub.add_parser("login", help="首次或登录失效时保存登录会话")
    login.add_argument("--platform", choices=["douyin", "kuaishou", "tiktok", "all"], default="all")
    sub.add_parser("demo", help="不访问平台，生成一份演示报告以验证安装")
    return parser.parse_args()


def main() -> int:
    setup_logging()
    try:
        config = load_config()
        args = parse_args()
        if args.command == "login":
            command_login(config, args.platform)
        elif args.command == "demo":
            command_demo(config)
        elif args.command == "progress":
            command_progress(config)
        elif args.command == "reset-progress":
            command_reset_progress(config, bool(args.yes))
        elif args.command == "run":
            output = run_collection(config, visible=bool(args.visible), background=bool(args.background))
            print(f"\n完成：{output}")
        return 0
    except KeyboardInterrupt:
        logging.warning("用户中止运行。")
        return 130
    except Exception as exc:
        logging.error("运行失败：%s", exc)
        logging.error(traceback.format_exc())
        print(f"\n运行失败。详细日志：{LOG_PATH}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
