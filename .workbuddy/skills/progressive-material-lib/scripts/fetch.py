#!/usr/bin/env python3
"""B站 站点工具：命令式入口，引用项目根 scripts/ 的实现，不做命中/归并判断。

用法（在技能根执行）：
    uv run python scripts/fetch.py search 影之刃零
    uv run python scripts/fetch.py --keyword 影之刃零 fetch
    uv run python scripts/fetch.py cover BV17zsd6nE1Y
    uv run python scripts/fetch.py frames BV17zsd6nE1Y --n 4
"""
import argparse
import os
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]  # scripts/fetch.py -> 技能根
sys.path.insert(0, str(_PROJ))
sys.path.insert(0, str(_PROJ / "scripts"))

import bili  # noqa: E402
from build_cache import ensure_tree  # noqa: E402

_DEFAULT_OUT = str(_PROJ / "library")
_SITE = "B站"


def cmd_search(args) -> int:
    videos = bili.search_videos(args.keyword, page=1, pagesize=args.page_size)
    print(f"搜索<{args.keyword}>: {len(videos)} 条（仅列表，未落盘）")
    for v in videos[: args.page_size]:
        print(f"  {v['bvid']} | {v['title'][:40]} | play={v['play']} | {v['author']}")
    return 0


def cmd_fetch(args) -> int:
    """抓取并落到 _inbox：归位（分组/实体）由访问者 LLM 自己判断。"""
    out = args.out
    inbox = ensure_tree(os.path.join(out, "_inbox"), _SITE)
    saved = 0
    for kw in [k for k in (args.keyword or "").split(",") if k.strip()]:
        videos = bili.search_videos(kw, page=1, pagesize=args.page_size)
        kw_md = os.path.join(inbox, kw)
        os.makedirs(kw_md, exist_ok=True)
        for v in videos:
            bvid_dir = os.path.join(kw_md, v["bvid"])
            os.makedirs(bvid_dir, exist_ok=True)
            md = os.path.join(bvid_dir, f"{v['bvid']}.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write(_video_md(v))
            saved += 1
        print(f"[fetch] 关键词「{kw}」:{len(videos)} 条 -> {kw_md}")
    print(f"共落 {saved} 个视频目录到 _inbox（封面按需 cover）: {inbox}")
    print("下一步由你判断：读 md → 决定 分组/实体 → 搬到")
    print(f"  {os.path.join(out, _SITE, '<关键词>', '<分组>', '<实体>', '<BVID>')}")
    return 0


def _video_md(v) -> str:
    url = v.get("url") or f"https://www.bilibili.com/video/{v['bvid']}"
    pic = v.get("pic") or ""
    pic_line = f"**封面URL**: {pic}\n" if pic else ""
    return (
        f"# {v['bvid']}\n\n"
        f"**标题**: {bili.clean_title(v['title'])}\n"
        f"**UP主**: {v.get('author','')}\n"
        f"**播放量**: {v.get('play','')}\n"
        f"**链接**: {url}\n"
        f"**描述**: {bili.clean_title(v.get('description') or '')}\n"
        f"{pic_line}"
        f"**分区**: {v.get('typename','')}\n"
        f"**时长**: {v.get('duration','')}（分:秒字符串，勿当秒数）\n\n"
        f"> 渐进披露：后续可在本目录展开 肖像/处决镜头 等切片。\n"
    )


def cmd_cover(args) -> int:
    md = _find_bvid_md(args.bvid, args.out)
    if not md:
        print(f"未找到 {args.bvid} 缓存目录，先 fetch。")
        return 1
    txt = open(md, encoding="utf-8").read()
    import re

    m = re.search(r"\*\*封面URL\*\* *[：:]\s*(\S+)", txt)
    if not m:
        print(f"{args.bvid}.md 无封面URL字段")
        return 1
    dst = os.path.join(os.path.dirname(md), "cover.png")
    ok = bili.download_cover(m.group(1).strip(")"), dst)
    print(f"{'OK' if ok else 'FAIL'} 封面 -> {dst}")
    return 0


def cmd_frames(args) -> int:
    md = _find_bvid_md(args.bvid, args.out)
    if not md:
        print(f"未找到 {args.bvid} 缓存目录，先 fetch。")
        return 1
    out_dir = os.path.join(os.path.dirname(md), "帧")
    n = bili.sample_frames(args.bvid, out_dir, args.n)
    print(f"匿名抽帧 {n} 张 -> {out_dir}")
    return 0


def _find_bvid_md(bvid: str, out: str):
    for root, dirs, files in os.walk(out):
        if os.path.basename(root) == bvid and os.path.exists(os.path.join(root, f"{bvid}.md")):
            return os.path.join(root, f"{bvid}.md")
    return None


def main() -> None:
    p = argparse.ArgumentParser(prog="fetch", description="B站站点工具（命令式）")
    p.add_argument("--out", default=_DEFAULT_OUT)
    p.add_argument("--page-size", type=int, default=8)
    p.add_argument("--keyword", default="", help="fetch 用的关键词，可逗号分隔")
    p.add_argument("--n", type=int, default=3, help="抽帧数量")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp_search = sub.add_parser("search", help="只搜索列表")
    sp_search.add_argument("keyword", help="搜索关键词")
    sp_search.add_argument("--page-size", dest="sub_page_size", type=int, default=None)
    sp_fetch = sub.add_parser("fetch", help="抓取并落站点缓存")
    sp_fetch.add_argument("--keyword", dest="sub_keyword", default=None,
                          help="关键词，可逗号分隔（也可写在子命令前）")
    sp_fetch.add_argument("--page-size", dest="sub_page_size", type=int, default=None)
    sp_cover = sub.add_parser("cover", help="按需下载封面")
    sp_cover.add_argument("bvid")
    sp_frames = sub.add_parser("frames", help="匿名拉流抽帧")
    sp_frames.add_argument("bvid")
    sp_frames.add_argument("--n", dest="sub_n", type=int, default=None)
    a = p.parse_args()

    # 子命令后的同名参数优先，其次用子命令前的
    if getattr(a, "sub_page_size", None) is not None:
        a.page_size = a.sub_page_size
    if getattr(a, "sub_keyword", None):
        a.keyword = a.sub_keyword
    if getattr(a, "sub_n", None) is not None:
        a.n = a.sub_n
    if a.cmd == "fetch" and not a.keyword:
        p.error("fetch 需要 --keyword")
    dispatch = {
        "search": cmd_search,
        "fetch": cmd_fetch,
        "cover": cmd_cover,
        "frames": cmd_frames,
    }
    dispatch[a.cmd](a)


if __name__ == "__main__":
    main()