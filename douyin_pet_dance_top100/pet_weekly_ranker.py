#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音每周宠物跳舞/宠物武打 Top100。

本文件复用仓库根目录的 weekly_dance_ranker.py，只覆盖宠物项目所需的：
- 当前子目录 config.yaml；
- 子项目独立断点、日志和输出；
- 上一级共享抖音登录 Profile；
- 宠物主体 + 跳舞/武打内容校验；
- 宠物榜单标题。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Mapping

APP_DIR = Path(__file__).resolve().parent
PARENT_DIR = APP_DIR.parent
BASE_SCRIPT = PARENT_DIR / "weekly_dance_ranker.py"

if not BASE_SCRIPT.exists():
    raise FileNotFoundError(f"找不到上一级主程序：{BASE_SCRIPT}")

spec = importlib.util.spec_from_file_location("weekly_dance_ranker_shared", BASE_SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"无法加载上一级主程序：{BASE_SCRIPT}")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

# 路径：Python环境和登录状态共用，运行数据独立。
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

# 保留原人工筛选Top50页面，只替换页面标题文字。
_original_write_html = base.write_html


def write_html(path, top_by_platform, diagnostics, window, tz, config):
    _original_write_html(path, top_by_platform, diagnostics, window, tz, config)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        text = text.replace("单人女性热舞候选", "宠物跳舞/武打候选")
        path.write_text(text, encoding="utf-8")


base.write_html = write_html

if __name__ == "__main__":
    raise SystemExit(base.main())
