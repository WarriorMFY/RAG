"""B站 匿名接口封装：搜索 / 封面 / 抽帧。

命令式，只执行、不做命中/归并判断。内部走 WBI 签名 + buvid3 指纹，
匿名（无 Cookie 登录态）即可调用搜索、封面与 360P 拉流。

被 `scripts/fetch.py` 引用：
    search_videos(keyword, page=1, pagesize=8) -> list[dict]
    video_info(bvid) -> dict            # 单条，字段与 search 条目对齐
    clean_title(text) -> str
    download_cover(url, dst) -> bool
    sample_frames(bvid, out_dir, n=3) -> int
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import shutil
import subprocess
import time
import urllib.parse

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com/"
TIMEOUT = 20.0

# wbi 签名的固定置换表
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


class BiliError(RuntimeError):
    """接口失败 / 环境缺失，消息直接给人看。"""


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA, "Referer": REFERER},
        timeout=TIMEOUT,
        follow_redirects=True,
    )


def _get_json(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    allow: tuple[int, ...] = (0,),
) -> dict:
    try:
        r = client.get(url, params=params)
    except httpx.HTTPError as e:
        raise BiliError(f"请求失败 {url}: {e}") from e
    if r.status_code != 200:
        raise BiliError(f"HTTP {r.status_code} {url}")
    try:
        data = r.json()
    except ValueError as e:
        raise BiliError(f"响应不是 JSON: {r.text[:120]}") from e
    code = data.get("code")
    if code is not None and code not in allow:
        raise BiliError(f"接口返回 code={code} message={data.get('message')}")
    return data


def _mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in _MIXIN_KEY_ENC_TAB)[:32]


def _wbi_sign(client: httpx.Client, params: dict) -> dict:
    """取 wbi 密钥并对参数签名，返回带 wts/w_rid 的新 dict。"""
    nav = _get_json(
        client,
        "https://api.bilibili.com/x/web-interface/nav",
        allow=(0, -101),  # 匿名未登录也返回 -101，但 wbi 密钥照样给
    )
    wbi = (nav.get("data") or {}).get("wbi_img") or {}
    img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
    if not img or not sub:
        raise BiliError("nav 未返回 wbi_img，无法签名")
    mixin = _mixin_key(img + sub)

    signed = dict(params)
    signed["wts"] = int(time.time())
    query = urllib.parse.urlencode(
        {k: signed[k] for k in sorted(signed)},
        quote_via=urllib.parse.quote,
    )
    query = re.sub(r"[!'()*]", "", query)
    signed["w_rid"] = hashlib.md5(f"{query}{mixin}".encode()).hexdigest()
    return signed


def _ensure_buvid3(client: httpx.Client) -> None:
    """取一枚匿名指纹 cookie，缺失时接口会风控。"""
    if client.cookies.get("buvid3"):
        return
    data = _get_json(client, "https://api.bilibili.com/x/frontend/finger/spi")
    b3 = (data.get("data") or {}).get("b_3") or ""
    if b3:
        client.cookies.set("buvid3", b3, domain=".bilibili.com")


def clean_title(text: str) -> str:
    """去掉搜索高亮 <em> 标签与 HTML 实体。"""
    if not text:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def search_videos(keyword: str, page: int = 1, pagesize: int = 8) -> list[dict]:
    """按关键词搜索视频，返回标准化列表（不落盘）。"""
    with _client() as client:
        _ensure_buvid3(client)
        params = _wbi_sign(
            client,
            {
                "search_type": "video",
                "keyword": keyword,
                "page": page,
                "page_size": pagesize,
            },
        )
        data = _get_json(
            client, "https://api.bilibili.com/x/web-interface/wbi/search/type", params
        )

    result = (data.get("data") or {}).get("result") or []
    videos = []
    for v in result[:pagesize]:
        bvid = v.get("bvid") or ""
        if not bvid:
            continue
        pic = v.get("pic") or ""
        if pic.startswith("//"):
            pic = "https:" + pic
        videos.append(
            {
                "bvid": bvid,
                "title": clean_title(v.get("title") or ""),
                "author": v.get("author") or "",
                "play": v.get("play") or 0,
                "description": clean_title(v.get("description") or ""),
                "pic": pic,
                "url": f"https://www.bilibili.com/video/{bvid}",
                "typename": v.get("typename") or "",
                "duration": v.get("duration") or "",
            }
        )
    return videos


def _fmt_duration(sec) -> str:
    """秒数转「分:秒」，与搜索结果的 duration 字段同形。"""
    try:
        s = int(sec)
    except (TypeError, ValueError):
        return str(sec or "")
    return f"{s // 60}:{s % 60:02d}"


def video_info(bvid: str) -> dict:
    """按 BVID 取单条视频信息（不落盘），字段与 search_videos 条目对齐。

    比 search_videos 省一级：不搜关键词、不落整页，适合只要一条的场景。
    """
    with _client() as client:
        _ensure_buvid3(client)
        data = _get_json(
            client,
            "https://api.bilibili.com/x/web-interface/view",
            {"bvid": bvid},
            allow=(0, -400, -403, -404),
        )
    code = data.get("code")
    if code == -400:
        raise BiliError(f"未找到视频 {bvid}：BVID 格式或编号有误")
    if code == -404:
        raise BiliError(f"未找到视频 {bvid}：BVID 可能有误，或视频已删除")
    if code == -403:
        raise BiliError(f"{bvid} 不可见（可能是私有/付费/地区限制）")

    d = data.get("data") or {}
    pic = d.get("pic") or ""
    if pic.startswith("//"):
        pic = "https:" + pic
    real = d.get("bvid") or bvid
    return {
        "bvid": real,
        "title": clean_title(d.get("title") or ""),
        "author": (d.get("owner") or {}).get("name") or "",
        "play": (d.get("stat") or {}).get("view") or 0,
        "description": clean_title(d.get("desc") or ""),
        "pic": pic,
        "url": f"https://www.bilibili.com/video/{real}",
        "typename": d.get("tname") or "",
        "duration": _fmt_duration(d.get("duration")),
    }


def download_cover(url: str, dst: str) -> bool:
    """下载封面到 dst，成功返回 True。"""
    if not url:
        return False
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with _client() as client:
        try:
            r = client.get(url)
        except httpx.HTTPError:
            return False
        if r.status_code != 200 or not r.content:
            return False
        with open(dst, "wb") as f:
            f.write(r.content)
    return True


def _view(client: httpx.Client, bvid: str) -> dict:
    data = _get_json(
        client,
        "https://api.bilibili.com/x/web-interface/view",
        {"bvid": bvid},
    )
    return data.get("data") or {}


def _play_url(client: httpx.Client, bvid: str, cid: int) -> str:
    """取 360P 直链（durl 优先，退化取 dash）。"""
    params = _wbi_sign(
        client,
        {"bvid": bvid, "cid": cid, "qn": 16, "fnval": 1, "fourk": 0},
    )
    data = _get_json(
        client, "https://api.bilibili.com/x/player/wbi/playurl", params
    )
    d = data.get("data") or {}
    durl = d.get("durl") or []
    if durl and durl[0].get("url"):
        return durl[0]["url"]
    dash = d.get("dash") or {}
    for v in (dash.get("video") or [])[:1]:
        if v.get("baseUrl"):
            return v["baseUrl"]
    raise BiliError(f"{bvid} 未取到可播放直链")


def sample_frames(bvid: str, out_dir: str, n: int = 3) -> int:
    """匿名拉流并均匀抽 n 帧到 out_dir，返回实际张数。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise BiliError(
            "未找到 ffmpeg：抽帧需要系统安装 ffmpeg，"
            "当前环境不可用。此外的命令（search/fetch/cover）不受影响。"
        )
    n = max(1, int(n))
    os.makedirs(out_dir, exist_ok=True)

    with _client() as client:
        _ensure_buvid3(client)
        view = _view(client, bvid)
        cid = view.get("cid")
        if not cid:
            raise BiliError(f"{bvid} 未取到 cid")
        duration = int(view.get("duration") or 0)
        url = _play_url(client, bvid, cid)

    saved = 0
    total = duration if duration > 0 else n * 10
    for i in range(n):
        ts = round(total * (i + 1) / (n + 1), 2)
        dst = os.path.join(out_dir, f"f_{i + 1:02d}.jpg")
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-headers", f"Referer: {REFERER}\r\nUser-Agent: {UA}\r\n",
            "-ss", str(ts), "-i", url, "-frames:v", "1", dst,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            saved += 1
    if saved == 0:
        raise BiliError(f"{bvid} 抽帧失败（可能是直链过期或 ffmpeg 参数不支持）")
    return saved
