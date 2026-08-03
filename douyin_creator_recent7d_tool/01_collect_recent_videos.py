#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
import traceback
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from playwright.sync_api import Response, sync_playwright
from creator_recent7d_core import *

LOG_PATH = LOG_DIR / "creator_recent7d_collector.log"
CHECKPOINT_VERSION = 2


class VerificationRequired(RuntimeError):
    pass


def setup_logging():
    ensure_dirs()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)], force=True)


def user_candidate(obj: Mapping[str, Any]):
    user = obj
    for key in ("user_info", "userInfo", "user", "author", "author_info"):
        if isinstance(obj.get(key), Mapping):
            user = obj[key]
            break
    unique = norm_account(deep_get(user, "unique_id", "uniqueId", default=""))
    short = norm_account(deep_get(user, "short_id", "shortId", default=""))
    sec = norm(deep_get(user, "sec_uid", "secUid", default=""))
    uid = norm(deep_get(user, "uid", "id", "user_id", default=""))
    if not any((unique, short, sec, uid)):
        return None
    return {"unique": unique, "short": short, "sec": sec, "uid": uid, "name": norm(deep_get(user, "nickname", "name", default="")), "followers": parse_count(deep_get(user, "follower_count", "followerCount", default=0))}


def video_obj(obj: Mapping[str, Any], creator: Creator, source: str):
    stats = deep_get(obj, "statistics", "stats", default={})
    stats = stats if isinstance(stats, Mapping) else {}
    video_id = norm(deep_get(obj, "aweme_id", "awemeId", "item_id", "itemId", default=""))
    if not video_id:
        return None
    author = deep_get(obj, "author", "author_info", "user", default={})
    author = author if isinstance(author, Mapping) else {}
    music = deep_get(obj, "music", "music_info", default={})
    music = music if isinstance(music, Mapping) else {}
    return Video(video_id=video_id, url=f"https://www.douyin.com/video/{video_id}", creator_name=creator.name, creator_account=creator.account, creator_homepage=creator.homepage, author_name=norm(deep_get(author, "nickname", "name", default="")), author_unique_id=norm_account(deep_get(author, "unique_id", "uniqueId", default="")), author_short_id=norm_account(deep_get(author, "short_id", "shortId", default="")), author_uid=norm(deep_get(author, "uid", "id", default="")), author_sec_uid=norm(deep_get(author, "sec_uid", "secUid", default="")), title=norm(deep_get(obj, "desc", "description", "caption", "title", default="")), create_time=parse_dt(deep_get(obj, "create_time", "createTime", default=None)), likes=parse_count(deep_get(stats, "digg_count", "diggCount", "like_count", default=0)), comments=parse_count(deep_get(stats, "comment_count", "commentCount", default=0)), shares=parse_count(deep_get(stats, "share_count", "shareCount", default=0)), favorites=parse_count(deep_get(stats, "collect_count", "collectCount", "favorite_count", default=0)), views=parse_count(deep_get(stats, "play_count", "playCount", "view_count", default=0)), music=norm(deep_get(music, "title", "music_name", "name", default="")), thumbnail=first_url(deep_get(obj, "video.cover", "video.origin_cover", "video.dynamic_cover", "cover", default="")), source=source)


def matches(video: Video, creator: Creator, bound=False):
    if creator.sec_uid and video.author_sec_uid:
        return creator.sec_uid == video.author_sec_uid
    if creator.uid and video.author_uid:
        return creator.uid == video.author_uid
    if creator.key in {account_key(video.author_unique_id), account_key(video.author_short_id)} - {""}:
        return True
    no_identity = not any((video.author_sec_uid, video.author_uid, video.author_unique_id, video.author_short_id))
    return bool(bound and no_identity)


class Collector:
    def __init__(self, config, creators, window, tz, visible, background):
        self.config = config
        self.cfg = config.get("collector", {})
        self.creators = creators
        self.window = window
        self.tz = tz
        self.visible = visible
        self.background = background and not visible
        self.unattended = self.background
        self.mode = "idle"
        self.active: Optional[Creator] = None
        self.users = []
        self.videos = {}
        self.old_streak = 0
        self.has_more = None
        self.verify_count = 0
        self.debug = APP_DIR / "debug_screenshots" / window.slug
        self.debug.mkdir(parents=True, exist_ok=True)

    def launch(self, playwright):
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        args = ["--disable-notifications", "--window-size=1440,1000"]
        args += ["--window-position=-32000,-32000"] if self.background else ["--start-maximized"]
        context = playwright.chromium.launch_persistent_context(user_data_dir=str(PROFILE_DIR), headless=False, locale="zh-CN", viewport={"width": 1440, "height": 1000}, args=args)
        timeout = int(self.cfg.get("navigation_timeout_ms", 60000))
        context.set_default_timeout(timeout)
        context.set_default_navigation_timeout(timeout)
        return context

    def shot(self, page, name):
        try:
            safe = re.sub(r"[^\w-]+", "_", name)[:90]
            page.screenshot(path=str(self.debug / f"{safe}.png"), full_page=False)
        except Exception:
            pass

    def _verification_evidence(self, page):
        try:
            current_url = (page.url or "").casefold()
        except Exception:
            current_url = ""
        strong_url = any(token in current_url for token in ("/captcha/", "verifycenter/captcha", "/verification/", "security-check", "passport/web/captcha"))
        script = r'''() => {
          const vw=Math.max(document.documentElement.clientWidth||0,innerWidth||0),vh=Math.max(document.documentElement.clientHeight||0,innerHeight||0);
          const visible=e=>{if(!e)return false;const s=getComputedStyle(e),r=e.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||1)>.08&&r.width>=80&&r.height>=45&&r.right>0&&r.bottom>0&&r.left<vw&&r.top<vh};
          const area=e=>{const r=e.getBoundingClientRect();return Math.max(0,r.width)*Math.max(0,r.height)};
          let normal=0;for(const sel of ['a[href*="/user/"]','a[href*="/video/"]','[data-e2e="search-user-item"]','[data-e2e="search_video-item"]','[data-e2e="user-post-item"]']){for(const e of document.querySelectorAll(sel)){if(visible(e)){normal++;if(normal>=2)break}}if(normal>=2)break}
          const strong=['请完成验证','请进行安全验证','拖动滑块','向右滑动完成验证','请依次点击','请点击图中','完成拼图','验证以继续','verify you are human','complete the verification','security verification'];
          let frame=false,dialog=false,overlay=false,control=false;
          for(const f of document.querySelectorAll('iframe')){if(!visible(f)||area(f)<18000)continue;const m=`${f.src||''} ${f.id||''} ${f.className||''}`.toLowerCase();if(['captcha','verifycenter','geetest','challenge'].some(t=>m.includes(t))){frame=true;break}}
          for(const e of document.querySelectorAll('[role="dialog"],[aria-modal="true"],[class*="captcha" i],[class*="verify" i],[class*="geetest" i],[id*="captcha" i],[id*="verify" i]')){if(!visible(e)||area(e)<14000)continue;const text=((e.innerText||e.textContent)||'').replace(/\s+/g,' ').trim().toLowerCase(),r=e.getBoundingClientRect();if(strong.some(p=>text.includes(p))){dialog=true;if(r.width>=vw*.45&&r.height>=vh*.35)overlay=true}}
          for(const sel of ['[class*="slider" i]','[class*="slide" i][class*="verify" i]','[class*="puzzle" i]','[class*="geetest_slider" i]','[class*="captcha_verify" i]']){for(const e of document.querySelectorAll(sel)){if(visible(e)&&area(e)>=2500){control=true;break}}if(control)break}
          return {normalVisibleCount:normal,visibleVerificationIframe:frame,visibleDialogStrong:dialog,visibleOverlay:overlay,visibleChallengeControl:control};
        }'''
        try:
            evidence = page.evaluate(script) or {}
        except Exception:
            evidence = {}
        evidence["strong_url"] = strong_url
        return evidence

    def verification(self, page):
        evidence = self._verification_evidence(page)
        normal = int(evidence.get("normalVisibleCount") or 0)
        strong_count = sum(bool(evidence.get(key)) for key in ("strong_url", "visibleDialogStrong", "visibleVerificationIframe", "visibleChallengeControl", "visibleOverlay"))
        if evidence.get("strong_url"):
            return True, "verification_url"
        if evidence.get("visibleDialogStrong") and strong_count >= 2:
            return True, "visible_verification_dialog"
        if evidence.get("visibleVerificationIframe") and (evidence.get("visibleChallengeControl") or evidence.get("visibleOverlay") or evidence.get("visibleDialogStrong")):
            return True, "visible_verification_iframe"
        if normal >= 2:
            return False, "normal_page_loaded"
        return False, ""

    def check(self, page, stage):
        hits = max(2, int(self.cfg.get("verification_required_consecutive_hits", 2)))
        interval = max(.5, float(self.cfg.get("verification_confirm_interval_seconds", .9)))
        reasons = []
        for _ in range(hits):
            detected, reason = self.verification(page)
            if not detected:
                return False
            reasons.append(reason)
            time.sleep(interval)
        self.verify_count += 1
        self.shot(page, f"verify_{self.verify_count}_{stage}")
        logging.warning("检测到真实可见验证码，阶段=%s，证据=%s", stage, ",".join(reasons))
        if self.unattended:
            raise VerificationRequired("后台检测到真实验证码，已保存断点；请运行前台版完成验证")
        print("\n检测到真实可见验证码，程序已暂停。请在浏览器完成验证，无需按回车。")
        deadline = time.monotonic() + int(self.cfg.get("verification_wait_timeout_seconds", 900))
        poll = max(.8, float(self.cfg.get("verification_poll_seconds", 1.5)))
        stable = max(3, float(self.cfg.get("verification_stable_clear_seconds", 5)))
        post = max(2, float(self.cfg.get("verification_post_clear_wait_seconds", 4)))
        clear_since = None
        while time.monotonic() < deadline:
            time.sleep(poll)
            visible, _ = self.verification(page)
            now = time.monotonic()
            if visible:
                clear_since = None
                continue
            if clear_since is None:
                clear_since = now
            if now - clear_since >= stable:
                time.sleep(post)
                if not self.verification(page)[0]:
                    logging.info("验证码已完成，继续当前任务")
                    return True
                clear_since = None
        raise VerificationRequired("验证码等待超时，断点已保留")

    def wait(self, page, seconds, stage):
        deadline = time.monotonic() + max(0, float(seconds))
        while time.monotonic() < deadline:
            self.check(page, stage)
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        self.check(page, stage + "_end")

    def on_response(self, response: Response):
        try:
            if response.request.resource_type not in {"xhr", "fetch", "document"}:
                return
            url = response.url.casefold()
            if self.mode == "user":
                if "search" not in url or any(token in url for token in ("suggest", "search_sug", "history")):
                    return
                payload = response.json()
                for obj in walk_dicts(payload):
                    candidate = user_candidate(obj)
                    if candidate:
                        self.users.append(candidate)
            elif self.mode == "profile" and self.active:
                if not any(token in url for token in ("aweme/post", "user/post", "profile/aweme", "/post/")):
                    return
                payload = response.json(); creator = self.active
                bound = bool((creator.sec_uid and creator.sec_uid.casefold() in url) or (creator.uid and creator.uid.casefold() in url))
                dates = []
                for obj in walk_dicts(payload):
                    video = video_obj(obj, creator, "profile_api")
                    if video and matches(video, creator, bound):
                        self.videos[video.video_id] = video
                        if video.create_time: dates.append(video.create_time)
                if dates:
                    self.old_streak = self.old_streak + 1 if all(item.astimezone(self.tz) < self.window.start for item in dates) else 0
                for obj in walk_dicts(payload):
                    if "has_more" in obj: self.has_more = bool(obj["has_more"])
                    elif "hasMore" in obj: self.has_more = bool(obj["hasMore"])
        except Exception as exc:
            logging.debug("响应解析失败：%s", exc)

    def user_dom(self, page):
        try:
            rows = page.evaluate(r'''() => [...document.querySelectorAll('a[href*="/user/"]')].map(a=>{const r=a.getBoundingClientRect(),s=getComputedStyle(a);if(!r.width||!r.height||s.display==='none'||s.visibility==='hidden')return null;let n=a,t='';for(let i=0;i<5&&n;i++,n=n.parentElement){const x=(n.innerText||'').trim();if(x.length>t.length&&x.length<1500)t=x}return {href:a.href||'',text:t}}).filter(Boolean).slice(0,100)''')
        except Exception:
            return
        for row in rows or []:
            account = re.search(r"抖音号\s*[:：]\s*([^\s]+)", norm(row.get("text")))
            sec = re.search(r"/user/([^/?#]+)", norm(row.get("href")))
            if account and sec:
                self.users.append({"unique": norm_account(account.group(1)), "short": "", "sec": sec.group(1), "uid": "", "name": norm(row.get("text")).split("抖音号", 1)[0], "followers": 0})

    def resolve(self, page, creator):
        self.users = []; self.mode = "user"
        logging.info("解析主页 %s | %s", creator.name, creator.account)
        try:
            page.goto(f"https://www.douyin.com/search/{urllib.parse.quote(creator.account)}?type=user", wait_until="domcontentloaded")
            self.wait(page, 4, "user_load")
            try:
                page.get_by_text("用户", exact=True).first.click(timeout=2500); self.wait(page, 2, "user_tab")
            except Exception:
                pass
            self.user_dom(page)
            for _ in range(2):
                page.evaluate("window.scrollBy(0,Math.max(900,innerHeight))"); self.wait(page, 1.2, "user_scroll"); self.user_dom(page)
        finally:
            self.mode = "idle"
        dedup = {}
        for item in self.users:
            key = (account_key(item.get("unique")), account_key(item.get("short")), norm(item.get("sec")))
            if key not in dedup: dedup[key] = item
        exact = [item for item in dedup.values() if creator.key in {account_key(item.get("unique")), account_key(item.get("short"))}]
        if not exact:
            creator.status = "not_found"; creator.note = "未找到抖音号完全一致的账号，未使用昵称模糊匹配"; return
        exact.sort(key=lambda item: (abs(parse_count(item.get("followers")) - creator.expected_followers) if creator.expected_followers and parse_count(item.get("followers")) else 0, -parse_count(item.get("followers"))))
        chosen = exact[0]
        creator.sec_uid = norm(chosen.get("sec")); creator.uid = norm(chosen.get("uid")); creator.resolved_name = norm(chosen.get("name")) or creator.name; creator.resolved_account = norm_account(chosen.get("unique") or chosen.get("short")); creator.current_followers = parse_count(chosen.get("followers"))
        if not creator.sec_uid:
            creator.status = "not_found"; creator.note = "精确账号缺少sec_uid"; return
        creator.homepage = f"https://www.douyin.com/user/{creator.sec_uid}"; creator.status = "resolved"; creator.note = "抖音号精确匹配"
        logging.info("主页：%s", creator.homepage)

    def collect_creator(self, page, creator):
        if creator.status != "resolved": return []
        before = set(self.videos); self.active = creator; self.mode = "profile"; self.old_streak = 0; self.has_more = None
        try:
            page.goto(creator.homepage, wait_until="domcontentloaded"); self.wait(page, 4, "profile_load")
            try:
                page.get_by_text("作品", exact=True).first.click(timeout=2500); self.wait(page, 2, "works_tab")
            except Exception:
                pass
            previous = len(self.videos); stable = 0
            for index in range(int(self.cfg.get("max_profile_scrolls", 35))):
                page.evaluate("window.scrollBy(0,Math.max(1600,innerHeight*1.8))")
                self.wait(page, random.uniform(float(self.cfg.get("profile_scroll_pause_min_seconds", 1.3)), float(self.cfg.get("profile_scroll_pause_max_seconds", 2.4))), f"profile_scroll_{index}")
                current = len(self.videos); stable = stable + 1 if current == previous else 0; previous = current
                if self.old_streak >= int(self.cfg.get("old_batch_stop_count", 2)) or self.has_more is False or stable >= int(self.cfg.get("profile_no_new_scrolls", 5)): break
        finally:
            self.mode = "idle"; self.active = None
        recent = [self.videos[video_id] for video_id in set(self.videos) - before if in_window(self.videos[video_id].create_time, self.window, self.tz)]
        recent.sort(key=lambda item: item.create_time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        logging.info("%s 最近7天 %d 条", creator.account, len(recent))
        return recent


def save_checkpoint(fp, window, creators, videos, next_index, phase, tz, note=""):
    atomic_json(CHECKPOINT_PATH, {"version": CHECKPOINT_VERSION, "fingerprint": fp, "window": window.to_dict(), "phase": phase, "next_index": next_index, "creators": [item.to_dict() for item in creators], "videos": [item.to_dict(tz) for item in videos], "note": note, "updated_at": datetime.now(timezone.utc).isoformat()})


def restore(config, creators, fresh):
    fp = fingerprint(creators, config)
    if not fresh and CHECKPOINT_PATH.exists():
        raw = load_json(CHECKPOINT_PATH, {}) or {}
        if raw.get("version") == CHECKPOINT_VERSION and raw.get("fingerprint") == fp and raw.get("phase") != "complete":
            return Window.from_dict(raw["window"]), resolve_tz(str(config.get("timezone", "Asia/Shanghai"))), [Creator.from_dict(item) for item in raw.get("creators", [])], [Video.from_dict(item) for item in raw.get("videos", [])], int(raw.get("next_index", 0)), fp
    window, tz = compute_window(config)
    return window, tz, creators, [], 0, fp


def run(args):
    config = load_config(Path(args.config)); creators = read_creators(Path(args.creators)); window, tz, creators, restored, next_index, fp = restore(config, creators, args.fresh)
    all_videos = {item.video_id: item for item in restored}; diagnostics = {"source_file": str(Path(args.creators).resolve()), "window": window.to_dict(), "creator_count": len(creators), "errors": []}
    with Lock(float(config.get("collector", {}).get("lock_stale_hours", 12))):
        with sync_playwright() as playwright:
            collector = Collector(config, creators, window, tz, args.visible, args.background); context = collector.launch(playwright); page = context.pages[0] if context.pages else context.new_page(); page.on("response", collector.on_response)
            try:
                for index in range(next_index, len(creators)):
                    creator = creators[index]
                    try:
                        if creator.status != "resolved": collector.resolve(page, creator)
                        for video in collector.collect_creator(page, creator): all_videos[video.video_id] = video
                    except VerificationRequired:
                        save_checkpoint(fp, window, creators, list(all_videos.values()), index, "collect", tz, "验证码"); raise
                    except Exception as exc:
                        creator.note = f"采集失败：{str(exc)[:250]}"; diagnostics["errors"].append({"account": creator.account, "error": str(exc)}); logging.warning("%s 失败：%s", creator.account, exc); collector.shot(page, f"error_{index}_{creator.account}")
                    save_checkpoint(fp, window, creators, list(all_videos.values()), index + 1, "collect", tz, f"完成{index+1}/{len(creators)}")
                    if index + 1 < len(creators): collector.wait(page, random.uniform(float(config.get("collector", {}).get("creator_delay_min_seconds", 5)), float(config.get("collector", {}).get("creator_delay_max_seconds", 10))), "between_creators")
            finally:
                context.close()
    videos = [item for item in all_videos.values() if in_window(item.create_time, window, tz)]; videos.sort(key=lambda item: item.create_time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    output = output_dir(config, window); diagnostics.update({"resolved_count": sum(item.status == "resolved" for item in creators), "unresolved_count": sum(item.status != "resolved" for item in creators), "video_count": len(videos), "verification_count": collector.verify_count})
    paths = write_collection(output, creators, videos, window, tz, diagnostics); set_latest(paths["json"]); save_checkpoint(fp, window, creators, videos, len(creators), "complete", tz, "完成")
    print(f"\n采集完成：{output}\n博主 {len(creators)}，解析成功 {diagnostics['resolved_count']}，最近7天视频 {len(videos)}\n下一步运行 02_生成最近7天Top100.bat")
    return 0


def login(args):
    config = load_config(Path(args.config)); window, tz = compute_window(config)
    with Lock(float(config.get("collector", {}).get("lock_stale_hours", 12))):
        with sync_playwright() as playwright:
            collector = Collector(config, [], window, tz, True, False); context = collector.launch(playwright); page = context.pages[0] if context.pages else context.new_page(); page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
            print(f"当前使用上一级共享登录目录：{PROFILE_DIR}"); print("如果页面已登录，直接回到黑框按回车；只有登录失效时才需要扫码。"); input(); context.close()
    return 0


def main():
    setup_logging(); parser = argparse.ArgumentParser(); parser.add_argument("--config", default=str(CONFIG_PATH)); parser.add_argument("--creators", default=str(CREATORS_PATH)); sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect"); mode = collect.add_mutually_exclusive_group(); mode.add_argument("--visible", action="store_true"); mode.add_argument("--background", action="store_true"); collect.add_argument("--fresh", action="store_true")
    sub.add_parser("login"); sub.add_parser("validate-input"); sub.add_parser("progress"); reset = sub.add_parser("reset"); reset.add_argument("--yes", action="store_true"); args = parser.parse_args()
    try:
        if args.command == "collect": return run(args)
        if args.command == "login": return login(args)
        if args.command == "validate-input":
            creators = read_creators(Path(args.creators)); print(f"读取成功：{len(creators)} 个已启用账号")
            for creator in creators[:8]: print(f"- {creator.name} | {creator.account} | 参考粉丝 {creator.expected_followers:,}")
            return 0
        if args.command == "progress":
            raw = load_json(CHECKPOINT_PATH, None); print("当前没有断点。" if not raw else f"阶段：{raw.get('phase')}\n博主进度：{raw.get('next_index',0)}/{len(raw.get('creators',[]))}\n已保存视频：{len(raw.get('videos',[]))}\n窗口：{raw.get('window',{}).get('label','')}"); return 0
        if args.command == "reset":
            if not args.yes: raise ValueError("请追加 --yes")
            CHECKPOINT_PATH.unlink(missing_ok=True); print("断点已删除"); return 0
    except KeyboardInterrupt:
        logging.warning("用户中止，断点会保留"); return 130
    except Exception as exc:
        logging.error("运行失败：%s", exc); logging.error(traceback.format_exc()); print(f"\n运行失败，日志：{LOG_PATH}"); return 1


if __name__ == "__main__":
    raise SystemExit(main())
