# -*- coding: utf-8 -*-
"""Parse 'Opere di Mao Tse-tung — Volume 1' bilingual Markdown into the
grimm-reader data format (one NN entry per work).

Output:
  data/st/<NN>.js            window.__GRIMM_STORIES__['NN'] = {...};
  data/stories.js           window.__GRIMM__ = {meta, stories:[...]}
  data/index.js             window.__GRIMM_INDEX__ = {meta, stories:[...]}
  build/audio_manifest.json  [{id, paras:[{pid, text}]}]   (it text for TTS)
  audio/<NN>/               empty dirs (audio filled later)
"""
import io, json, os, re

ROOT = r"D:/意大利语材料/opere-mao"
SRC = r"D:/workbuddy工作区/2026-10-11-22-28-06/Opere_di_Mao_Tse-tung_Volume_1_zh.md"

AUTHOR = "Mao Tse-tung (毛泽东)"
VOICE_IT = "it-IT-ElsaNeural"
VOICE_ZH = "zh-CN-XiaoxiaoNeural"


def strip_italic(s):
    s = s.strip()
    if len(s) >= 2 and s.startswith("*") and s.endswith("*") and not s.startswith("**"):
        return s[1:-1].strip()
    return s


def strip_heading_marks(s):
    """Strip leading '> ', markdown heading '#' marks, and surrounding '*'
    from a chinese title line so only the bare title text remains."""
    s = s.strip()
    if s.startswith(">"):
        s = s[1:].strip()
    while s and s[0] == "#":
        s = s[1:]
    return strip_italic(s)


def looks_like_title_line(s):
    s = s.strip()
    if not s:
        return False
    # italic chinese title: *中文*  (but not **bold**)
    if s.startswith("*") and s.endswith("*") and not s.startswith("**"):
        return True
    # blockquote chinese title: > 中文  / > ## 中文  / > ### 中文
    if s.startswith(">"):
        return True
    return False


def collect_blockquote_block(lines, i):
    """Starting at a '>' line, collect consecutive '>' lines (skipping a single
    blank line between two '>' lines). Returns (segments, next_index)."""
    seg = []
    j = i
    n = len(lines)
    while j < n:
        cur = lines[j].strip()
        if cur.startswith(">"):
            inner = cur[1:].strip()
            seg.append(strip_italic(inner))
            j += 1
        elif cur == "" and j + 1 < n and lines[j + 1].strip().startswith(">"):
            j += 1  # skip blank between two blockquote lines
        else:
            break
    return seg, j


def parse():
    lines = io.open(SRC, encoding="utf-8").read().split("\n")
    # locate first work heading (## ... but not 'Indice delle opere')
    start = 0
    for k, L in enumerate(lines):
        s = L.strip()
        if s.startswith("## ") and s != "## Indice delle opere":
            start = k
            break

    works = []          # list of dicts {title_it, title_zh, paras:[]}
    cur = None
    sec = 1
    n = len(lines)
    i = start
    while i < n:
        L = lines[i]
        s = L.strip()
        if s == "":
            i += 1
            continue
        # skip stray TOC / year markers / hr that may have slipped into content
        if s.startswith("- ") and "](" in s:
            i += 1; continue
        if re.match(r"^\*\*\d{4}\*\*$", s) or s == "---":
            i += 1; continue
        if s.startswith("## "):
            # new work; the next non-empty line is the chinese title (italic or blockquote)
            title_it = s[3:].strip()
            title_zh = ""
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n and looks_like_title_line(lines[j]):
                title_zh = strip_heading_marks(lines[j])
                i = j
            cur = {"title_it": title_it, "title_zh": title_zh, "paras": []}
            works.append(cur)
            sec = 1
            i += 1
            continue
        if s.startswith("### "):
            # subsection heading inside current work -> new sec, becomes a para
            head_it = s[4:].strip()
            head_zh = ""
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n and looks_like_title_line(lines[j]):
                head_zh = strip_heading_marks(lines[j])
                i = j
            sec += 1
            cur["paras"].append({"sec": sec, "it": head_it, "zh": head_zh, "head": True})
            i += 1
            continue
        if s.startswith(">"):
            seg, j = collect_blockquote_block(lines, i)
            it = seg[0] if seg else ""
            zh = "\n".join(seg[1:]) if len(seg) > 1 else ""
            cur["paras"].append({"sec": sec, "it": it, "zh": zh, "head": False, "byline": True})
            i = j
            continue
        # normal paragraph: italian line, optional following '>' chinese block
        it = s
        zh = ""
        j = i + 1
        while j < n and lines[j].strip() == "":
            j += 1
        if j < n and lines[j].strip().startswith(">"):
            seg, k = collect_blockquote_block(lines, j)
            zh = "\n".join(seg)
            i = k
        else:
            i = j
        cur["paras"].append({"sec": sec, "it": it, "zh": zh, "head": False})
    return works


def main():
    works = parse()
    assert works, "no works parsed"

    # assign NN ids (zero-padded, 1-based)
    stories_meta = []
    para_count = 0
    manifest = []
    for idx, w in enumerate(works, 1):
        NN = "%02d" % idx
        paras = []
        for k, p in enumerate(w["paras"], 1):
            pid = "s%03d" % k
            paras.append({
                "pid": pid,
                "sec": p["sec"],
                "it": p["it"],
                "zh": p["zh"],
                "head": bool(p.get("head", False)),
                "audio": "audio/%s/%s.mp3" % (NN, pid),
                "audio_zh": "audio/%s/z%s.mp3" % (NN, pid),
            })
        para_count += len(paras)
        obj = {
            "id": NN,
            "title_it": w["title_it"],
            "title_zh": w["title_zh"],
            "paras": paras,
        }
        out_js = os.path.join(ROOT, "data", "st", NN + ".js")
        with io.open(out_js, "w", encoding="utf-8") as f:
            f.write("window.__GRIMM_STORIES__['%s'] = %s;\n"
                    % (NN, json.dumps(obj, ensure_ascii=False)))
        # audio dir
        os.makedirs(os.path.join(ROOT, "audio", NN), exist_ok=True)
        # manifest
        manifest.append({
            "id": NN,
            "paras": [{"pid": p["pid"], "text": p["it"]} for p in paras],
        })
        stories_meta.append({"id": NN, "title_it": w["title_it"], "title_zh": w["title_zh"]})

    meta = {
        "title_it": "Opere di Mao Tse-tung",
        "title_zh": "毛泽东著作",
        "subtitle_it": "Opere di Mao Tse-tung — Volume 1 (1917–1926)",
        "subtitle_zh": "毛泽东选集 第一卷 · 意中双语",
        "author": AUTHOR,
        "lang": "it-IT",
        "voice": VOICE_IT,
        "voice_zh": VOICE_ZH,
        "story_count": len(stories_meta),
        "para_count": para_count,
        "align": "意中逐句对照 (italiano + 中文)",
    }
    with io.open(os.path.join(ROOT, "data", "stories.js"), "w", encoding="utf-8") as f:
        f.write("window.__GRIMM__ = %s;\n" % json.dumps({"meta": meta, "stories": stories_meta}, ensure_ascii=False))
    with io.open(os.path.join(ROOT, "data", "index.js"), "w", encoding="utf-8") as f:
        f.write("window.__GRIMM_INDEX__ = %s;\n" % json.dumps({"meta": meta, "stories": stories_meta}, ensure_ascii=False))
    with io.open(os.path.join(ROOT, "build", "audio_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    print("WORKS=%d  PARA_COUNT=%d" % (len(works), para_count))
    for m in stories_meta:
        print("  %s  %s  ·  %s" % (m["id"], m["title_it"], m["title_zh"]))


if __name__ == "__main__":
    main()
