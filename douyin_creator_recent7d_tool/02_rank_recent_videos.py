#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import logging
import sys
import traceback
from pathlib import Path
from creator_recent7d_core import *

LOG_PATH = LOG_DIR / "creator_recent7d_ranker.log"

def main():
    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
        force=True,
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--input", default="")
    args = parser.parse_args()
    try:
        config = load_config(Path(args.config))
        source = get_latest(Path(args.input) if args.input else None)
        window, creators, videos, diagnostics = read_collection(source)
        tz = resolve_tz(str(config.get("timezone", "Asia/Shanghai")))
        ranked = rank_videos(videos, window, tz, config)
        paths = write_rank(source.parent, ranked, window, tz, config)
        top_n = int(config.get("ranking", {}).get("top_n", 100))
        print(
            f"\n排名完成：{source.parent}\n参与排名 {len(ranked)} 条，输出 {min(top_n,len(ranked))} 条\n"
            f"Excel：{paths['xlsx']}\n网页：{paths['html']}"
        )
        return 0
    except Exception as exc:
        logging.error("排名失败：%s", exc)
        logging.error(traceback.format_exc())
        print(f"\n排名失败，日志：{LOG_PATH}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
