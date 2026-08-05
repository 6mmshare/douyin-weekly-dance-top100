#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音每周宠物跳舞/宠物武打 Top100。

本文件直接复用仓库根目录的 weekly_dance_ranker.py，只覆盖宠物项目所需的：
- 当前子目录 config.yaml；
- 子项目独立断点、日志和输出；
- 上一级共享抖音登录 Profile；
- 宠物主体 + 跳舞/武打内容校验；
- 宠物榜单标题；
- 防断电断点保护：旧记录只合并、不降级、不清空；
- generate 命令：不重新搜索，只读取已有数据生成最近7天 Top100。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

APP_DIR = Path(__file__).resolve().parent
PARENT_DIR = APP_DIR.parent
BASE_SCRIPT = PARENT_DIR / "weekly_dance_ranker.py"

if not BASE_SCRIPT.exists():
    raise FileNotFoundError(f"找不到上一级主程序：{BASE_SCRIPT}")

spec = importlib.util.spec_from_file_location("weekly_dance_ranker_shared", BASE_SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"无法加载上一级主程序：{BASE_SCRIPT}")
base = importlib.util.module_from_spec(spec)
# dataclass 在执行模块时会通过 sys.modules 查找所属模块；必须先注册再执行。
sys.modules[spec.name] = base
spec.loader.exec_module(base)

# Python 环境和登录状态共用；宠物项目运行数据独立。
base.APP_DIR = APP_DIR
base.CONFIG_PATH = APP_DIR / "config.yaml"
base.DATA_DIR = APP_DIR / "data"
base.PROFILE_DIR = PARENT_DIR / "data" / "profiles"
base.LOG_DIR = APP_DIR / "logs"
base.LOG_PATH = base.LOG_DIR / "weekly_ranker.log"

_original_load_config = base.load_config


def load_config(path: Path | None = None):
    return _original_load_config(path or base.CONFIG_PATH)


base.load_config = load_config


# ---------------------------------------------------------------------------
# 防断电 / 强制退出保护
# ---------------------------------------------------------------------------
_original_checkpoint_save = base.CollectionCheckpoint.save


def _record_map(records: Iterable[Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for record in records:
        try:
            key = record.key()
        except Exception:
            key = f"unknown:{getattr(record, 'video_id', '')}:{getattr(record, 'url', '')}"
        if key:
            merged[key] = record
    return merged


def protected_checkpoint_save(
    self,
    records,
    *,
    phase: str,
    next_keyword_index: int,
    last_keyword_ids,
    detail_keys,
    completed_detail_keys,
    failed_attempts,
    processed_detail_count: int,
    note: str = "",
):
    incoming_map = _record_map(records)
    old_state = None
    if self.path.exists():
        try:
            old_state = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logging.warning("[%s] 旧断点读取失败，不覆盖原文件：%s", base.PLATFORM_LABELS[self.platform], exc)
            raise

    if old_state:
        compatible = (
            old_state.get("version") == base.CHECKPOINT_VERSION
            and old_state.get("window_slug") == self.window.slug
            and old_state.get("config_fingerprint") == self.fingerprint
        )
        if compatible:
            for raw in old_state.get("records") or []:
                try:
                    record = base.video_record_from_dict(raw)
                    incoming_map.setdefault(record.key(), record)
                except Exception:
                    continue

            next_keyword_index = max(int(next_keyword_index), int(old_state.get("next_keyword_index", 0)))
            processed_detail_count = max(
                int(processed_detail_count), int(old_state.get("processed_detail_count", 0))
            )
            last_keyword_ids = set(last_keyword_ids or []) | set(old_state.get("last_keyword_ids") or [])
            completed_detail_keys = set(completed_detail_keys or []) | set(
                old_state.get("completed_detail_keys") or []
            )
            if not detail_keys:
                detail_keys = old_state.get("detail_keys") or []

            merged_failed = dict(old_state.get("failed_attempts") or {})
            for key, value in dict(failed_attempts or {}).items():
                merged_failed[str(key)] = max(int(value), int(merged_failed.get(str(key), 0)))
            failed_attempts = merged_failed

            old_count = int(old_state.get("record_count", len(old_state.get("records") or [])))
            if len(incoming_map) < old_count:
                raise RuntimeError(
                    f"断点保护触发：准备保存 {len(incoming_map)} 条，磁盘已有 {old_count} 条，已拒绝覆盖。"
                )

    return _original_checkpoint_save(
        self,
        incoming_map.values(),
        phase=phase,
        next_keyword_index=next_keyword_index,
        last_keyword_ids=last_keyword_ids,
        detail_keys=detail_keys,
        completed_detail_keys=completed_detail_keys,
        failed_attempts=failed_attempts,
        processed_detail_count=processed_detail_count,
        note=note,
    )


base.CollectionCheckpoint.save = protected_checkpoint_save


PET_DANCE_TERMS = {
    "宠物跳舞", "萌宠跳舞", "猫咪跳舞", "小猫跳舞", "猫猫跳舞",
    "狗狗跳舞", "小狗跳舞", "狗子跳舞", "跳舞猫", "跳舞狗",
    "舞蹈猫", "舞蹈狗", "会跳舞的狗", "会跳舞的猫", "宠物蹦迪",
    "猫咪蹦迪", "狗狗蹦迪", "萌宠蹦迪", "宠物卡点", "萌宠卡点",
    "卡点萌宠", "猫咪卡点", "狗狗卡点", "宠物科目三", "猫咪科目三",
    "狗狗科目三", "猫咪捂嘴舞", "猫咪捂鼻子舞", "宠物摇摆舞",
    "动物成精", "萌宠成精", "pet dance", "dancing cat", "dancing dog",
    "cat dance", "dog dance",
}

PET_FIGHT_TERMS = {
    "宠物武打", "萌宠武打", "猫咪武打", "狗狗武打", "功夫猫",
    "功夫猫咪", "功夫狗", "猫咪功夫", "狗狗功夫", "猫猫打拳",
    "猫咪打拳", "狗狗打拳", "宠物打拳", "萌宠打拳", "猫咪拳击",
    "狗狗拳击", "宠物拳击", "猫咪对打", "狗狗对打", "猫狗大战",
    "猫咪格斗", "狗狗格斗", "萌宠格斗", "武术猫", "武术狗",
    "kung fu cat", "kung fu dog", "pet kung fu",
}

base.DANCE_TERMS = PET_DANCE_TERMS | PET_FIGHT_TERMS


def evaluate_target_match(record, filters: Mapping[str, Any]):
    content = base.content_text_blob(record)
    source = base.normalize_text(record.source_keyword).casefold()

    exclude_terms = filters.get("exclude_terms", [])
    ai_terms = filters.get("ai_terms", [])
    human_terms = filters.get("human_only_terms", [])
    pet_terms = filters.get("pet_terms", [])
    dance_terms = filters.get("pet_dance_terms", list(PET_DANCE_TERMS))
    fight_terms = filters.get("pet_fight_terms", list(PET_FIGHT_TERMS))
    positive_terms = [*dance_terms, *fight_terms]

    for label, terms in (
        ("教程/合集/搬运/商品内容", exclude_terms),
        ("AI生成或动画内容", ai_terms),
        ("纯人类舞蹈内容", human_terms),
    ):
        hit = base.first_matching_term(content, terms)
        if hit:
            return 0.0, "排除", False, False, False, f"{label}：{hit}"

    pet_hits = len(base.matching_terms(content, pet_terms))
    dance_hits = len(base.matching_terms(content, dance_terms))
    fight_hits = len(base.matching_terms(content, fight_terms))
    source_pet = base.contains_any(source, pet_terms)
    source_action = base.contains_any(source, positive_terms)

    if pet_hits == 0 and not source_pet:
        return 0.0, "排除", False, False, False, "缺少宠物主体信号"

    score = min(42.0, pet_hits * 14.0)
    score += min(44.0, dance_hits * 14.0 + fight_hits * 14.0)
    if dance_hits and fight_hits:
        score += 8.0
    if base.contains_any(content, [
        "宠物跳舞", "猫咪跳舞", "狗狗跳舞", "萌宠跳舞", "会跳舞的狗子",
        "功夫猫", "功夫猫咪", "猫咪功夫", "狗狗功夫", "宠物武打",
        "猫猫打拳", "狗狗打拳", "宠物拳击", "动物成精", "萌宠成精",
    ]):
        score += 18.0
    if source_pet:
        score += 4.0
    if source_action:
        score += 4.0

    score = min(100.0, score)
    level = "A-强匹配" if score >= 68 else "B-较匹配" if score >= 38 else "C-宽松候选"
    return score, level, bool(pet_hits), bool(dance_hits), bool(fight_hits), ""


base.evaluate_target_match = evaluate_target_match


# ---------------------------------------------------------------------------
# 已有数据手动生成最近7天 Top100
# ---------------------------------------------------------------------------
RELATIVE_TIME_PATTERNS = (
    (re.compile(r"(?<!\d)(\d+)\s*天前"), "days"),
    (re.compile(r"(?<!\d)(\d+)\s*小时前"), "hours"),
    (re.compile(r"(?<!\d)(\d+)\s*分钟前"), "minutes"),
)


def infer_publish_time_from_search_title(
    title: str,
    captured_at: Optional[datetime],
    tz: Any,
) -> Optional[datetime]:
    raw = base.normalize_text(title)
    if not raw:
        return None
    captured = captured_at or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    local_base = captured.astimezone(tz)

    if "昨天" in raw:
        return local_base - timedelta(days=1)
    if "前天" in raw:
        return local_base - timedelta(days=2)
    if "刚刚" in raw:
        return local_base

    for pattern, unit in RELATIVE_TIME_PATTERNS:
        matches = list(pattern.finditer(raw))
        if not matches:
            continue
        value = int(matches[-1].group(1))
        if unit == "days":
            if value < 1 or value > 6:
                return None
            return local_base - timedelta(days=value)
        if unit == "hours":
            return local_base - timedelta(hours=value)
        if unit == "minutes":
            return local_base - timedelta(minutes=value)
    return None


def infer_search_card_count(title: str) -> int:
    raw = base.normalize_text(title)
    match = re.match(
        r"^(?:合集\s+)?\d{1,2}:\d{2}(?::\d{2})?\s+"
        r"(-?\d+(?:\.\d+)?\s*[万亿千kKmMbB]?)\b",
        raw,
    )
    return base.parse_count(match.group(1)) if match else 0


def hydrate_search_dom_records(records, tz: Any) -> dict[str, int]:
    stats = {
        "records": len(records),
        "inferred_publish_time": 0,
        "inferred_visible_count": 0,
        "still_unknown_publish_time": 0,
    }
    for record in records:
        if not record.create_time:
            inferred = infer_publish_time_from_search_title(record.title, record.captured_at, tz)
            if inferred is not None:
                record.create_time = inferred.astimezone(timezone.utc)
                record.data_sources.add("derived:search-card-relative-time")
                record.data_quality_notes.append("发布时间由搜索卡片相对时间推算")
                stats["inferred_publish_time"] += 1

        if record.likes <= 0:
            count = infer_search_card_count(record.title)
            if count > 0:
                record.likes = count
                record.data_sources.add("derived:search-card-visible-count")
                record.data_quality_notes.append("点赞数采用搜索卡片可见数字")
                stats["inferred_visible_count"] += 1

        if not record.hashtags:
            record.hashtags = base.extract_hashtags(record.title)
        if not record.create_time:
            stats["still_unknown_publish_time"] += 1
    return stats


def load_existing_candidates_for_generation(config, window):
    output_root = APP_DIR / str(config.get("output_root", "output"))
    candidates: list[tuple[Path, str]] = []

    current_json = output_root / window.slug / "all_candidates.json"
    if current_json.exists():
        candidates.append((current_json, "all_candidates"))
    if output_root.exists():
        for path in output_root.glob("*/all_candidates.json"):
            if path != current_json:
                candidates.append((path, "all_candidates"))

    checkpoint_dir = APP_DIR / "data" / "checkpoints"
    current_checkpoint = checkpoint_dir / f"douyin_{window.slug}.json"
    if current_checkpoint.exists():
        candidates.append((current_checkpoint, "checkpoint"))
    if checkpoint_dir.exists():
        for path in checkpoint_dir.glob("douyin_*.json"):
            if path != current_checkpoint:
                candidates.append((path, "checkpoint"))

    if not candidates:
        raise FileNotFoundError(
            "没有找到已有候选数据，需要 output\\日期\\all_candidates.json "
            "或 data\\checkpoints\\douyin_日期.json。"
        )

    candidates.sort(
        key=lambda item: (
            1 if item[0] in {current_json, current_checkpoint} else 0,
            item[0].stat().st_mtime,
        ),
        reverse=True,
    )

    errors = []
    for path, kind in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw_records = payload.get("records", []) if kind == "checkpoint" else payload
            if not isinstance(raw_records, list) or not raw_records:
                errors.append(f"{path}：没有 records")
                continue
            records = [
                base.video_record_from_dict(raw)
                for raw in raw_records
                if isinstance(raw, Mapping)
            ]
            if records:
                return records, path, kind
        except Exception as exc:
            errors.append(f"{path}：{exc}")

    raise RuntimeError("找到数据文件但均无法读取：\n" + "\n".join(errors[:10]))


def command_generate_existing(config):
    window, tz = base.compute_window(config)
    records, source_path, source_kind = load_existing_candidates_for_generation(config, window)
    hydration = hydrate_search_dom_records(records, tz)

    diagnostics = {
        "generated_at": datetime.now(tz).isoformat(),
        "generation_mode": "existing_data_only",
        "source_file": str(source_path),
        "source_kind": source_kind,
        "window": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "mode": window.mode,
        },
        "douyin": {
            "platform": "douyin",
            "platform_label": "抖音",
            "candidate_count": len(records),
            "with_create_time_after_inference": sum(1 for r in records if r.create_time),
            "with_likes_after_inference": sum(1 for r in records if r.likes > 0),
            **hydration,
        },
    }

    output_dir = base.build_outputs(config, window, tz, records, diagnostics)
    html_path = output_dir / f"{base.report_basename(config)}.html"
    print(
        "\n已从现有数据生成最近7天 Top100：\n"
        f"数据来源：{source_path}\n"
        f"原始候选：{len(records)} 条\n"
        f"成功推算发布时间：{hydration['inferred_publish_time']} 条\n"
        f"结果目录：{output_dir}\n"
        f"网页：{html_path}"
    )
    try:
        os.startfile(str(html_path))
    except Exception:
        pass
    return output_dir


# 保留原人工筛选 Top50 页面，只替换页面标题文字。
_original_write_html = base.write_html


def write_html(path, top_by_platform, diagnostics, window, tz, config):
    _original_write_html(path, top_by_platform, diagnostics, window, tz, config)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        text = text.replace("单人女性热舞候选", "宠物跳舞/武打候选")
        path.write_text(text, encoding="utf-8")


base.write_html = write_html


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "generate":
        try:
            command_generate_existing(load_config())
            raise SystemExit(0)
        except Exception as exc:
            logging.exception("从已有数据生成 Top100 失败：%s", exc)
            print(f"\n生成失败：{exc}")
            raise SystemExit(1)
    raise SystemExit(base.main())
