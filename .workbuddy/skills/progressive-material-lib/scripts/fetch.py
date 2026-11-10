#!/usr/bin/env python3
"""B站 站点工具：命令式入口，引用项目根 scripts/ 的实现，不做命中/归并判断。

用法（在技能根执行）：
    python scripts/fetch.py status                          # 盘点全库，不联网
    python scripts/fetch.py search 影之刃零
    python scripts/fetch.py --bvid BV17zsd6nE1Y fetch        # 单条，最省
    python scripts/fetch.py --keyword 影之刃零 fetch          # 整页 8 条
    python scripts/fetch.py cover BV17zsd6nE1Y
    python scripts/fetch.py frames BV17zsd6nE1Y --n 4
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]  # scripts/fetch.py -> 技能根
sys.path.insert(0, str(_PROJ))
sys.path.insert(0, str(_PROJ / "scripts"))

from build_cache import ensure_tree  # noqa: E402

_BILI = None


def _bili():
    """按需导入 bili：status 只读文件系统，不该被 httpx 拖住。"""
    global _BILI
    if _BILI is None:
        try:
            import bili
        except ModuleNotFoundError as e:
            if e.name == "httpx":
                sys.exit(
                    "缺少依赖 httpx（search / fetch / cover / frames 都要它；status 不需要）。\n"
                    "装法任选，都不动仓库：\n"
                    "  python -m pip install httpx\n"
                    "  python -m pip install --target <临时目录> httpx\n"
                    "  PYTHONPATH=<临时目录> python scripts/fetch.py <子命令>\n"
                )
            raise
        _BILI = bili
    return _BILI

_DEFAULT_OUT = str(_PROJ / "library")
_SITE = "B站"


def _scan_site(site_dir: Path) -> list[dict]:
    """扫一个站点：以「含 <BVID>.md 的 BV 目录」为准，父目录即为实体。"""
    rows = {}
    for vdir in sorted(site_dir.rglob("BV*")):
        if not vdir.is_dir() or not (vdir / f"{vdir.name}.md").exists():
            continue
        rel = vdir.relative_to(site_dir)
        parts = rel.parts[:-1]  # 去掉 BV 目录本身
        if len(parts) > 1:
            kw, ent = parts[0], "/".join(parts[1:])
        else:
            kw, ent = "", (parts[0] if parts else "（站点根）")
        row = rows.setdefault(
            (kw, ent), {"keyword": kw, "entity": ent, "videos": 0, "covers": 0, "frames": 0}
        )
        row["videos"] += 1
        row["covers"] += sum(
            1 for f in vdir.iterdir() if f.is_file() and f.stem.lower() == "cover"
        )
        fdir = vdir / "帧"
        if fdir.is_dir():
            row["frames"] += sum(1 for f in fdir.iterdir() if f.is_file())
    return sorted(rows.values(), key=lambda r: (r["keyword"], r["entity"]))


def _tool_status() -> dict:
    try:
        import httpx

        hx = {"ready": True, "detail": getattr(httpx, "__version__", "")}
    except ModuleNotFoundError:
        hx = {"ready": False, "detail": "search / fetch / cover / frames 不可用"}
    ff = shutil.which("ffmpeg")
    return {
        "httpx": hx,
        "ffmpeg": {"ready": bool(ff), "detail": "" if ff else "不能抽帧"},
    }


def cmd_status(args) -> int:
    """一眼看完：库在哪、有什么、工具能不能用。只读文件系统，不联网。"""
    lib = Path(args.out)
    sites, inbox = [], 0
    if lib.is_dir():
        for site_dir in sorted(p for p in lib.iterdir() if p.is_dir() and not p.name.startswith("_")):
            rows = _scan_site(site_dir)
            sites.append(
                {
                    "site": site_dir.name,
                    "entities": rows,
                    "videos": sum(r["videos"] for r in rows),
                    "covers": sum(r["covers"] for r in rows),
                    "frames": sum(r["frames"] for r in rows),
                }
            )
        inbox_dir = lib / "_inbox"
        if inbox_dir.is_dir():
            inbox = sum(1 for p in inbox_dir.rglob("BV*") if p.is_dir())
    tools = _tool_status()

    if args.json:
        print(json.dumps(
            {"root": str(lib), "sites": sites, "inbox": inbox, "tools": tools},
            ensure_ascii=False, indent=2,
        ))
        return 0

    print(f"[status] 库根: {lib}")
    if not sites:
        print("  （空库：还没有任何站点素材）")
        print("  下一步：search <关键词> 看有没有 → fetch --bvid <BVID> 落一条")
    for s in sites:
        kws = len({r["keyword"] for r in s["entities"] if r["keyword"]})
        print(
            f"{s['site']}  关键词 {kws} | 实体 {len(s['entities'])} | "
            f"视频 {s['videos']} | 封面 {s['covers']} | 帧 {s['frames']}"
        )
        for r in s["entities"]:
            where = f"{r['keyword']} → {r['entity']}" if r["keyword"] else r["entity"]
            print(
                f"  · {where}   视频 {r['videos']}  封面 {r['covers']}  帧 {r['frames']}"
            )
    print(f"未归位 _inbox: {inbox} 条" + ("（记得归位）" if inbox else ""))
    hx, ff = tools["httpx"], tools["ffmpeg"]
    print(
        "工具 "
        + ("httpx 就绪" + (f" ({hx['detail']})" if hx["detail"] else "") if hx["ready"] else f"httpx 缺失（{hx['detail']}）")
        + " | ffmpeg " + ("就绪" if ff["ready"] else f"缺失（{ff['detail']}）")
    )
    return 0


def cmd_search(args) -> int:
    videos = _bili().search_videos(args.keyword, page=1, pagesize=args.page_size)
    print(f"搜索<{args.keyword}>: {len(videos)} 条（仅列表，未落盘）")
    for v in videos[: args.page_size]:
        print(f"  {v['bvid']} | {v['title'][:40]} | play={v['play']} | {v['author']}")
    return 0


def _bvid_list(raw: str) -> list[str]:
    """逗号/空格/顿号分隔的 BVID 串 -> 去重列表。"""
    parts = [p.strip() for p in raw.replace("，", ",").replace("、", ",").split(",")]
    seen, out = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _land_video(parent_dir: str, v: dict) -> str:
    """把一条视频落到 parent_dir/<BVID>/<BVID>.md，返回目录。"""
    bvid_dir = os.path.join(parent_dir, v["bvid"])
    os.makedirs(bvid_dir, exist_ok=True)
    with open(os.path.join(bvid_dir, f"{v['bvid']}.md"), "w", encoding="utf-8") as f:
        f.write(_video_md(v))
    return bvid_dir


def cmd_fetch(args) -> int:
    """抓取并落到 _inbox：归位（分组/实体）由访问者 LLM 自己判断。"""
    out = args.out
    inbox = ensure_tree(os.path.join(out, "_inbox"), _SITE)
    saved = 0
    failed = 0

    for bvid in _bvid_list(args.bvid):
        try:
            v = _bili().video_info(bvid)
        except _bili().BiliError as e:
            failed += 1
            print(f"[fetch] 跳过 {bvid}：{e}")
            continue
        bvid_dir = _land_video(inbox, v)
        saved += 1
        print(f"[fetch] 单条 {v['bvid']} | {v['title'][:30]} -> {bvid_dir}")

    for kw in [k for k in (args.keyword or "").split(",") if k.strip()]:
        videos = _bili().search_videos(kw, page=1, pagesize=args.page_size)
        kw_md = os.path.join(inbox, kw)
        os.makedirs(kw_md, exist_ok=True)
        for v in videos:
            _land_video(kw_md, v)
            saved += 1
        print(f"[fetch] 关键词「{kw}」:{len(videos)} 条 -> {kw_md}")

    print(f"共落 {saved} 个视频目录到 _inbox（封面按需 cover）: {inbox}")
    if failed:
        print(f"另有 {failed} 条未能取到，已逐条列出原因。")
    print("下一步由你判断：读 md → 决定 分组/实体 → 搬到")
    print(f"  {os.path.join(out, _SITE, '<关键词>', '<分组>', '<实体>', '<BVID>')}")
    return 1 if failed and not saved else 0


def _video_md(v) -> str:
    """渲染叶子 md：空字段不占行，避免把「取不到」写成「是空的」。"""
    url = v.get("url") or f"https://www.bilibili.com/video/{v['bvid']}"
    pic = v.get("pic") or ""
    lines = [
        f"# {v['bvid']}",
        "",
        f"**标题**: {_bili().clean_title(v['title'])}",
    ]
    if v.get("author"):
        lines.append(f"**UP主**: {v['author']}")
    if v.get("play"):
        lines.append(f"**播放量**: {v['play']}")
    lines.append(f"**链接**: {url}")
    if v.get("description"):
        lines.append(f"**描述**: {_bili().clean_title(v['description'])}")
    if pic:
        lines.append(f"**封面URL**: {pic}")
    if v.get("typename"):
        lines.append(f"**分区**: {v['typename']}")
    if v.get("duration"):
        lines.append(f"**时长**: {v['duration']}（分:秒字符串，勿当秒数）")
    lines += ["", "> 渐进披露：后续可在本目录展开 肖像/处决镜头 等切片。", ""]
    return "\n".join(lines)


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
    ok = _bili().download_cover(m.group(1).strip(")"), dst)
    print(f"{'OK' if ok else 'FAIL'} 封面 -> {dst}")
    return 0


def cmd_frames(args) -> int:
    md = _find_bvid_md(args.bvid, args.out)
    if not md:
        print(f"未找到 {args.bvid} 缓存目录，先 fetch。")
        return 1
    out_dir = os.path.join(os.path.dirname(md), "帧")
    n = _bili().sample_frames(args.bvid, out_dir, args.n)
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
    p.add_argument("--bvid", default="", help="fetch 用的 BVID，可逗号分隔（单条最省）")
    p.add_argument("--n", type=int, default=3, help="抽帧数量")
    p.add_argument("--debug", action="store_true", help="出错时打印完整堆栈（默认只给一行原因）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp_search = sub.add_parser("search", help="只搜索列表")
    sp_search.add_argument("keyword", help="搜索关键词")
    sp_search.add_argument("--page-size", dest="sub_page_size", type=int, default=None)
    sp_fetch = sub.add_parser("fetch", help="抓取并落站点缓存")
    sp_fetch.add_argument("--keyword", dest="sub_keyword", default=None,
                          help="关键词，可逗号分隔（也可写在子命令前）")
    sp_fetch.add_argument("--bvid", dest="sub_bvid", default=None,
                          help="只落这几条 BVID，逗号分隔；比 --keyword 少落 7 条")
    sp_fetch.add_argument("--page-size", dest="sub_page_size", type=int, default=None)
    sp_cover = sub.add_parser("cover", help="按需下载封面")
    sp_cover.add_argument("bvid")
    sp_frames = sub.add_parser("frames", help="匿名拉流抽帧")
    sp_frames.add_argument("bvid")
    sp_frames.add_argument("--n", dest="sub_n", type=int, default=None)
    sp_status = sub.add_parser("status", help="盘点：库在哪、有什么、工具能不能用（不联网）")
    sp_status.add_argument("--json", action="store_true", help="输出 JSON，便于程序读")
    a = p.parse_args()

    # 子命令后的同名参数优先，其次用子命令前的
    if getattr(a, "sub_page_size", None) is not None:
        a.page_size = a.sub_page_size
    if getattr(a, "sub_keyword", None):
        a.keyword = a.sub_keyword
    if getattr(a, "sub_bvid", None):
        a.bvid = a.sub_bvid
    if getattr(a, "sub_n", None) is not None:
        a.n = a.sub_n
    if a.cmd == "fetch" and not (a.keyword or a.bvid):
        p.error("fetch 需要 --bvid（单条，最省）或 --keyword（整页 8 条）")
    dispatch = {
        "search": cmd_search,
        "fetch": cmd_fetch,
        "cover": cmd_cover,
        "frames": cmd_frames,
        "status": cmd_status,
    }
    if a.cmd != "status":
        _bili()  # 缺 httpx 时在这里一次性给出装法，而不是抛栈
    try:
        rc = dispatch[a.cmd](a)
    except Exception as e:
        mod = _BILI
        if mod is None or not isinstance(e, mod.BiliError):
            raise
        if a.debug:
            raise
        sys.exit(f"[{a.cmd}] {e}")
    sys.exit(rc)


if __name__ == "__main__":
    main()