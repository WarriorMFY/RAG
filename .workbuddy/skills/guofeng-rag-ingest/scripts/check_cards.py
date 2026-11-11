#!/usr/bin/env python3
"""校验国风 RAG 三库卡片合规性（术语库 / 序列库 / 画面库分开校验）。

用法：
    python check_cards.py RAG            # 一次跑三库
    python check_cards.py RAG/术语库
    python check_cards.py RAG/序列库/结构/一把醉剑走江湖·切点节奏.md

按路径自动判定库别：
    含 序列库 → 序列卡：front-matter(name+描述+来源) + 一个两列表（自变量|因变量）
                 一张卡只记一个因变量：景别/运镜/速度/光线/状态/事件/节奏…
    含 画面库 → 画面卡：front-matter(只 name) + 一个 2 列表（可见特征|证据）
    其他      → 术语卡：front-matter(只 name) + 一个 2 列表（在观测上下文中的含义|出处）

通用检查：
    front-matter 必须有 name；不得声明 分类/轨/史创/有效时间/热度证据 等旧字段
    序列卡额外要求 描述（一段话，≥40 字）与 来源（含链接）
    正文必须且仅有一个表格（表格外不得有自由内容、裸编号列表、引用块）
    逐行：术语/画面首列须有【标签】、末列（证据/出处）非空
    序列卡：两列均非空、自变量以时间码/数字/「全片」开头、首行时间码须从 00:00 起、
            **不得出现弹幕/评论/点赞**

体积上限：
    术语卡 4096B（4500B WARN）；画面卡 4096B（4500B WARN）；序列卡 8192B（9216B WARN）

退出码：0 = 全部通过（可有 WARN），1 = 存在 FAIL。
"""

import io
import os
import re
import sys

BANNED_FM_KEYS = ["分类", "主轨", "轨", "条目数", "条目", "别名", "关联",
                  "有效时间", "热度证据", "卡片状态", "建立时间", "最后修改时间"]
BANNED_ROW_FIELDS = ["轨：", "史/", "【史】", "【创】", "有效时间：", "热度证据：",
                     "一句话描述：", "权威层级："]
LINK_RE = re.compile(r"https?://|\[.+?\]\(.+?\)|同上")
EVID_RE = re.compile(r"https?://|\[.+?\]\(.+?\)|同上|封面|帧|待核|《|\d{2}:\d{2}")
TC_RE = re.compile(r"^(\d{2}:\d{2}|全片)")

KIND = {
    "term": {"header": "在观测上下文中的含义", "cols": 2, "last": "出处", "size": 4096, "warn": 4500},
    "visual": {"header": "可见特征", "cols": 2, "last": "证据", "size": 4096, "warn": 4500},
    "seq": {"size": 8192, "warn": 9216, "last": "证据"},
}

# 序列卡只有一种表头：自变量｜因变量（一张卡一个因变量）
SEQ_HEADER = ["自变量", "因变量"]


def kind_of(path):
    p = path.replace("\\", "/")
    if "序列库" in p:
        return "seq"
    if "画面库" in p:
        return "visual"
    return "term"


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return m.group(1) if m else ""


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_first_table(text):
    """返回正文第一个表格 (header_cells, rows, (start, end))。"""
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1]):
            header = split_row(l)
            rows, j = [], i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append(lines[j])
                j += 1
            return header, rows, (i, j)
    return None, [], (None, None)


def parse_table_by_header(text, header_key):
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("|") and header_key in l:
            header = split_row(l)
            rows, j = [], i + 1
            if j < len(lines) and re.match(r"^\|[\s\-|]+\|$", lines[j]):
                j += 1
            while j < len(lines) and lines[j].startswith("|"):
                rows.append(lines[j])
                j += 1
            return header, rows, (i, j)
    return None, [], (None, None)


def common_head(text, kind, fails, warns, size, fm):
    """front-matter 与体积检查（三库共用）。"""
    if size > KIND[kind]["warn"]:
        fails.append("体积 %dB 超上限 %dB（拆卡，不压缩事实）" % (size, KIND[kind]["warn"]))
    elif size > KIND[kind]["size"]:
        warns.append("体积 %dB 略超上限（容忍区）" % size)

    if not fm:
        fails.append("缺 front-matter")
        return
    if not re.search(r"^name:\s*(\S.*)$", fm, re.M):
        fails.append("front-matter 缺 name")
    for key in BANNED_FM_KEYS:
        if re.search(r"^%s:" % re.escape(key), fm, re.M):
            fails.append("front-matter 声明了 %s（应从证据推断）" % key)
    if kind == "seq":
        if not re.search(r"^来源:\s*\S", fm, re.M):
            fails.append("front-matter 缺 来源（链接）")
        elif not LINK_RE.search(fm):
            fails.append("来源里没有链接")
        # 描述支持单行 `描述: xxx` 与块写法 `描述: >-` + 缩进行
        desc = re.search(r"^描述:[ \t]*(?:>-?|\|)?[ \t]*(.*(?:\n[ \t]+\S.*)*)", fm, re.M)
        dtext = re.sub(r"\s+", "", desc.group(1)) if desc else ""
        if not dtext:
            fails.append("front-matter 缺 描述（一段话：怎么变/何时用/复刻要点/采集方式）")
        elif len(dtext) < 40:
            fails.append("描述仅 %d 字，应写成一段话" % len(dtext))
    elif re.search(r"^(来源|素材|用途):", fm, re.M):
        warns.append("front-matter 多了 name 之外的字段（画面库/术语库只写 name）")


def check_body_sanity(body, fails):
    """正文只许有一个表格：表格前后的内容、裸编号列表、引用块都算违规。"""
    header, rows, (start, end) = parse_first_table(body)
    if start is None:
        fails.append("未找到表格")
        return
    lines = body.split("\n")
    stray = ("\n".join(lines[:start]) + "\n" + "\n".join(lines[end:])).strip()
    if stray:
        fails.append("表格外有自由内容 %dB（正文只应有表格）" % len(stray.encode("utf-8")))
    zone = "\n".join(lines[end:])
    n_num = len(re.findall(r"^\d+\. ", zone, re.M))
    if n_num:
        fails.append("%d 处裸编号列表" % n_num)
    n_quote = len(re.findall(r"^> ", zone, re.M))
    if n_quote:
        fails.append("%d 处引用块" % n_quote)


def check_seq(text, body, fails, warns):
    """序列卡：两列「自变量｜因变量」，一张卡只记一个因变量。返回 (卡型, 行数)。"""
    header, rows, span = parse_first_table(text)
    if header is None:
        fails.append("未找到任何表格")
        return "双列", 0

    # 列名允许带括注说明，如「自变量（时间/空间）」
    plain = [re.sub(r"[（(].*?[）)]", "", c).strip() for c in header]
    if plain != SEQ_HEADER:
        fails.append("序列表头应为「自变量｜因变量」（实际：%s）" % "｜".join(header))
        return "双列", len(rows)

    for idx, row in enumerate(rows, 1):
        cells = split_row(row)
        if len(cells) != 2:
            fails.append("第%d行 %d 列，应为 2 列" % (idx, len(cells)))
            continue
        indep, dep = cells[0], cells[1]
        if not indep:
            fails.append("第%d行 自变量为空" % idx)
        elif not re.match(r"^(\d|全片)", indep):
            fails.append("第%d行 自变量应以时间码/序号/「全片」开头" % idx)
        if not dep:
            fails.append("第%d行 因变量为空" % idx)
        blob = " ".join(cells)
        for word in ("弹幕", "评论", "点赞"):
            if word in blob:
                fails.append("第%d行 含「%s」——序列卡只写画面演的" % (idx, word))
                break

    # 零点：自变量从 0 起算（全片卡 00:00=片头，片段卡 00:00=本段起点）
    if rows:
        first = split_row(rows[0])[0].strip()
        if TC_RE.match(first) and not re.match(r"^00:00", first):
            fails.append("首行自变量应从 00:00 起（实际：%s）——片段卡以本段起点为 00:00，"
                         "原片绝对区间写进描述；首行之前无取值时补一行负结果" % first)

    check_body_sanity(body, fails)
    return "双列", len(rows)


def check_file(path):
    kind = kind_of(path)
    text = io.open(path, encoding="utf-8").read()
    size = len(text.encode("utf-8"))
    fm = frontmatter(text)
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    fails, warns = [], []

    common_head(text, kind, fails, warns, size, fm)

    if kind == "seq":
        label, nrows = check_seq(text, body, fails, warns)
        return {"path": path, "kind": "seq/" + label, "size": size,
                "rows": nrows, "fails": fails, "warns": warns}

    spec = KIND[kind]
    header, rows, span = parse_table_by_header(text, spec["header"])
    if header is None:
        fails.append("未找到表头含「%s」的表格" % spec["header"])
        return {"path": path, "kind": kind, "size": size, "rows": 0,
                "fails": fails, "warns": warns}

    if len(header) != spec["cols"]:
        fails.append("表格 %d 列，应为 %d 列" % (len(header), spec["cols"]))
    if spec["last"] not in header:
        fails.append("表头缺「%s」列" % spec["last"])

    for idx, row in enumerate(rows, 1):
        cells = split_row(row)
        if len(cells) != spec["cols"]:
            fails.append("第%d行 %d 列，应为 %d 列" % (idx, len(cells), spec["cols"]))
            continue
        first, last = cells[0], cells[-1]
        if not re.match(r"^【.+?】", first):
            fails.append("第%d行 缺行首标签" % idx)
        if not last:
            fails.append("第%d行 末列（%s）为空" % (idx, spec["last"]))
        elif not EVID_RE.search(last):
            fails.append("第%d行 末列无链接/时间帧/「同上」/《卡名》" % idx)

        if kind == "term":
            for f in BANNED_ROW_FIELDS:
                if f in first:
                    fails.append("第%d行 含旧格式字段「%s」" % (idx, f))
            if len(first) < 30:
                warns.append("第%d行 含义仅 %d 字，疑过薄" % (idx, len(first)))

    check_body_sanity(body, fails)
    return {"path": path, "kind": kind, "size": size, "rows": len(rows),
            "fails": fails, "warns": warns}


def collect(target):
    if os.path.isfile(target):
        return [target]
    files = []
    for root, _, names in os.walk(target):
        for n in sorted(names):
            if n.endswith(".md"):
                full = os.path.join(root, n)
                base = os.path.basename(full)
                if base.upper() in ("README.MD", "AGENTS.MD") or base.startswith("_"):
                    continue
                if "参考作品" in full.replace("\\", "/") or "作品库" in full.replace("\\", "/"):  # 配方卡另有格式，不在三库校验内（目录曾名"参考作品"，后改名"作品库"）
                    continue
                files.append(full)
    return sorted(files)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    files = collect(sys.argv[1])
    if not files:
        print("未找到可校验的卡片：%s" % sys.argv[1])
        return 1

    hard = total = 0
    print("%-26s %8s %7s %4s  %s" % ("卡片", "库/卡型", "体积B", "行", "结论"))
    print("-" * 68)
    for f in files:
        r = check_file(f)
        total += r["size"]
        status = "FAIL" if r["fails"] else ("WARN" if r["warns"] else "OK")
        hard += 1 if r["fails"] else 0
        name = os.path.splitext(os.path.basename(f))[0]
        label = {"term": "术语", "visual": "画面"}.get(r["kind"], r["kind"])
        print("%-26s %8s %7d %4d  %s" % (name, label, r["size"], r["rows"], status))
        for msg in r["fails"]:
            print("    x %s" % msg)
        for msg in r["warns"]:
            print("    ! %s" % msg)

    print("-" * 68)
    print("合计 %d 卡 / %d B，FAIL %d 张" % (len(files), total, hard))
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
