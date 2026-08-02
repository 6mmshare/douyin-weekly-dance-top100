#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "weekly_dance_ranker.py"
CONFIG = ROOT / "config.yaml"

OLD_QUEUE_BLOCK = r'''                prefiltered = [record for record in relevant_store.values() if prefilter_record(record, self.config)]
                diagnostics["candidate_count"] = len(relevant_store)
                diagnostics["prefilter_count"] = len(prefiltered)
                diagnostics["excluded_by_text"] = len(relevant_store) - len(prefiltered)
                if not detail_keys:
                    prefiltered.sort(key=lambda record: (record.likes + record.shares * 4 + record.favorites * 3 + record.comments * 2, record.target_match_score), reverse=True)
                    detail_keys = [record.key() for record in prefiltered[:max_details]]
                save_checkpoint("detail", note="详情队列已建立")
                logging.info("[%s] 搜索候选 %d 条，文字初筛可补全 %d 条，排除 %d 条；本轮详情上限 %d", self.label, len(relevant_store), len(prefiltered), len(relevant_store) - len(prefiltered), len(detail_keys))
'''

NEW_QUEUE_BLOCK = r'''                prefiltered = [record for record in relevant_store.values() if prefilter_record(record, self.config)]
                require_known_time = bool(self.config.get("filters", {}).get("require_known_publish_time", True))
                known_in_window: list[VideoRecord] = []
                unknown_publish_time: list[VideoRecord] = []
                outside_window: list[VideoRecord] = []
                for record in prefiltered:
                    if record.create_time is None:
                        unknown_publish_time.append(record)
                    elif within_window(record, self.window, self.tz):
                        known_in_window.append(record)
                    else:
                        outside_window.append(record)

                # 周榜严格模式：只允许搜索接口已经给出、且明确位于统计窗口内的视频进入详情队列。
                # 这样不会为了“确认日期”而打开去年或更早的视频。
                detail_candidates = known_in_window if require_known_time else known_in_window + unknown_publish_time

                diagnostics["candidate_count"] = len(relevant_store)
                diagnostics["prefilter_count"] = len(prefiltered)
                diagnostics["excluded_by_text"] = len(relevant_store) - len(prefiltered)
                diagnostics["known_in_window_before_detail"] = len(known_in_window)
                diagnostics["unknown_publish_time_skipped"] = len(unknown_publish_time) if require_known_time else 0
                diagnostics["outside_window_skipped"] = len(outside_window)
                if not detail_keys:
                    detail_candidates.sort(key=lambda record: (record.likes + record.shares * 4 + record.favorites * 3 + record.comments * 2, record.target_match_score), reverse=True)
                    detail_keys = [record.key() for record in detail_candidates[:max_details]]
                save_checkpoint("detail", note="严格时间窗口详情队列已建立")
                logging.info(
                    "[%s] 搜索候选 %d 条，文字初筛 %d 条；时间窗口内 %d 条，发布时间缺失跳过 %d 条，窗口外跳过 %d 条；本轮详情 %d 条",
                    self.label,
                    len(relevant_store),
                    len(prefiltered),
                    len(known_in_window),
                    len(unknown_publish_time) if require_known_time else 0,
                    len(outside_window),
                    len(detail_keys),
                )
'''

OLD_DETAIL_BLOCK = r'''                            self.enrich_record(record)
                            failed_attempts[key] = attempt + 1
                            success = bool(record.title or record.likes or record.comments or record.shares or record.favorites or record.create_time)
                            if success:
                                break
                            last_error = "详情没有返回可验证字段"
'''

NEW_DETAIL_BLOCK = r'''                            self.enrich_record(record)
                            failed_attempts[key] = attempt + 1
                            page_has_data = bool(record.title or record.likes or record.comments or record.shares or record.favorites)
                            if record.create_time is None:
                                record.data_quality_notes.append("发布时间缺失，按严格周榜规则排除")
                                success = True
                                break
                            if not within_window(record, self.window, self.tz):
                                record.data_quality_notes.append("发布时间超出统计窗口，已排除")
                                success = True
                                break
                            success = page_has_data
                            if success:
                                break
                            last_error = "详情没有返回可验证字段"
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"找不到代码锚点：{label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = SCRIPT.read_text(encoding="utf-8-sig")
    if "CHECKPOINT_VERSION = 5" not in text:
        if "CHECKPOINT_VERSION = 4" not in text:
            raise RuntimeError("找不到 CHECKPOINT_VERSION = 4")
        text = text.replace("CHECKPOINT_VERSION = 4", "CHECKPOINT_VERSION = 5", 1)
    text = replace_once(text, OLD_QUEUE_BLOCK, NEW_QUEUE_BLOCK, "详情队列")
    text = replace_once(text, OLD_DETAIL_BLOCK, NEW_DETAIL_BLOCK, "详情日期复核")
    SCRIPT.write_text(text, encoding="utf-8", newline="\n")
    py_compile.compile(str(SCRIPT), doraise=True)

    config_text = CONFIG.read_text(encoding="utf-8-sig")
    if "require_known_publish_time:" not in config_text:
        anchor = "  exclude_minor_text_signals: true"
        if anchor not in config_text:
            raise RuntimeError("config.yaml 中找不到 exclude_minor_text_signals")
        config_text = config_text.replace(
            anchor,
            anchor + "\n  # 严格周榜：搜索结果没有明确发布时间时，不打开详情页，也不进入榜单。\n  require_known_publish_time: true",
            1,
        )
        CONFIG.write_text(config_text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
