# -*- coding: utf-8 -*-
"""Split Mao works into sentence-level bilingual units (一句意 + 一句中).

Usage:
  python split_sentences.py                 # analyze all works (dry run)
  python split_sentences.py --write         # split & write back data files
  python split_sentences.py --works 02,03   # only these works (default: all)
  python split_sentences.py --audio-ids 01  # which works keep audio refs (default: 01)

Design notes:
- Each output unit = one Italian sentence + one corresponding Chinese sentence.
- pid is renumbered s001..sNNN per work; head=True only on a paragraph's first unit.
- Audio refs are written ONLY for works listed in --audio-ids (they already have
  per-sentence audio on disk). Other works get "" and the reader marks them noaudio.
- --write also syncs meta.para_count (total units across all works) and the voice
  fields in data/stories.js + data/index.js.
"""
import re, json, io, os, sys, glob

ABBR = r'(?:n|n°|no|ecc|es|prof|dott|sig|fig|art|vol|p|cfr|sec|app|cap|pag|tel)\.'
WRITE = "--write" in sys.argv

PUNCT_ONLY = re.compile(r'^[\s"\'""\'\'「」『』（）()…—，、：；！？。%0-9¹²³⁴⁵⁶⁷⁸⁹]+$')


def arg_val(flag, default):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def all_work_ids():
    ids = []
    for fn in sorted(glob.glob(os.path.join("data", "st", "*.js"))):
        sid = os.path.basename(fn)[:-3]
        if re.match(r'^\d\d$', sid):
            ids.append(sid)
    return ids


def load_story(sid, path=None):
    if path is None:
        path = os.path.join("data", "st", sid + ".js")
    t = io.open(path, encoding="utf-8").read()
    m = re.search(r"window\.__GRIMM_STORIES__\['" + sid + r"'\] = (\{.*\});\s*$", t, re.S)
    return json.loads(m.group(1))


def split_it(text):
    text = (text or "").strip()
    if not text:
        return []

    def prot(m):
        return m.group(0)[:-1] + "\x00"   # hide the period (abbrev or list marker)
    # protect abbreviations like "n. 2"
    t2 = re.sub(r'\b' + ABBR + r'(?=\s)', prot, text, flags=re.I)
    # protect numbered list markers "1. " "12. " (1-2 digits); 4-digit years like 1915. stay splittable
    t2 = re.sub(r'(?<![\d.])\b\d{1,2}\.\s', prot, t2)
    parts = re.split(r'(?<=[.!?])\s+', t2)
    out = [p.replace("\x00", ".").strip() for p in parts]
    return clean_parts(out)


def clean_parts(parts):
    """Merge fragments that are only quotes/punctuation into the previous sentence."""
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and PUNCT_ONLY.match(p):
            out[-1] = out[-1] + p
        else:
            out.append(p)
    return out


def split_zh(text):
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r'(?<=[。！？])', text)
    return clean_parts(parts)


def distribute(total, n):
    """Split `total` items into `n` groups as evenly as possible."""
    base, rem = divmod(total, n)
    return [base + (1 if i < rem else 0) for i in range(n)]


def align(its, zhs):
    if not zhs:
        return [(it_s, "") for it_s in its]
    if not its:
        return [("", zh_s) for zh_s in zhs]
    if len(its) == len(zhs):
        return list(zip(its, zhs))
    # counts differ: group the longer side into len(shorter) chunks, evenly
    if len(its) > len(zhs):
        sizes = distribute(len(its), len(zhs))
        units, i = [], 0
        for k, z in enumerate(zhs):
            units.append((" ".join(its[i:i + sizes[k]]), z))
            i += sizes[k]
        return units
    else:
        sizes = distribute(len(zhs), len(its))
        units, i = [], 0
        for k, it_s in enumerate(its):
            units.append((it_s, "".join(zhs[i:i + sizes[k]])))
            i += sizes[k]
        return units


def split_story(sid, with_audio):
    story = load_story(sid)
    paras = story["paras"]
    new_paras = []
    idx = 1
    mism = []
    empty_zh_paras = []
    stray = []
    for p in paras:
        its = split_it(p.get("it"))
        zhs = split_zh(p.get("zh"))
        if not zhs and not (p.get("zh") or "").strip():
            empty_zh_paras.append(p.get("pid"))
        pairs = align(its, zhs)
        if len(its) != len(zhs):
            mism.append((p.get("pid"), len(its), len(zhs)))
        head = p.get("head", False)
        sec = p.get("sec", 1)
        for j, (it_s, zh_s) in enumerate(pairs):
            # sanity: no pure-punctuation unit may survive
            if PUNCT_ONLY.match(it_s) or PUNCT_ONLY.match(zh_s):
                stray.append((idx, it_s[:20], zh_s[:20]))
            audio = ("audio/%s/s%03d.mp3" % (sid, idx)) if with_audio else ""
            audio_zh = ("audio/%s/zs%03d.mp3" % (sid, idx)) if (with_audio and zh_s) else ""
            new_paras.append({
                "pid": "s%03d" % idx,
                "sec": sec,
                "it": it_s,
                "zh": zh_s,
                "head": bool(head and j == 0),
                "audio": audio,
                "audio_zh": audio_zh,
            })
            idx += 1
    return story, new_paras, mism, empty_zh_paras, stray


def sync_meta():
    """Recount total units across all works and update stories.js / index.js meta."""
    total = 0
    for sid in all_work_ids():
        try:
            obj = load_story(sid)
            total += len(obj["paras"])
        except Exception:
            pass
    for fn in ("data/stories.js", "data/index.js"):
        t = io.open(fn, encoding="utf-8").read()
        t2 = re.sub(r'("para_count":\s*)\d+', r'\g<1>%d' % total, t)
        t2 = re.sub(r'("voice":\s*")[^"]*(")', r'\g<1>it-IT-GiuseppeMultilingualNeural\g<2>', t2)
        if t2 != t:
            with io.open(fn, "w", encoding="utf-8") as f:
                f.write(t2)
    return total


def main():
    works_arg = arg_val("--works", None)
    audio_arg = arg_val("--audio-ids", "01")
    all_ids = all_work_ids()
    works = [w.strip() for w in works_arg.split(",")] if works_arg else all_ids
    works = [w for w in works if w in all_ids]
    audio_ids = set(w.strip() for w in audio_arg.split(","))

    print("=== 处理篇目: %s | 写音频引用的篇: %s ===" % (",".join(works), ",".join(sorted(audio_ids)) or "(无)"))
    summary = []
    for sid in works:
        story, new_paras, mism, empty_zh, stray = split_story(sid, sid in audio_ids)
        print("\n[work %s] %s" % (sid, story.get("title_zh", "")))
        print("  原段落 %d -> 句级单元 %d | 句数一致 %d | 不一致 %d | 空中文段 %d"
              % (len(story["paras"]), len(new_paras), len(story["paras"]) - len(mism), len(mism), len(empty_zh)))
        if mism:
            detail = ", ".join("%s(IT%d/ZH%d)" % m for m in mism[:12])
            print("  不一致: %s%s" % (detail, " ..." if len(mism) > 12 else ""))
        if stray:
            print("  ⚠️ 纯标点单元: %s" % stray[:5])
        if empty_zh:
            print("  空中文段(仅意语): %s" % empty_zh[:10])
        # preview first two units
        for u in new_paras[:2]:
            print("  预览 [%s]%s IT: %s" % (u["pid"], " H" if u["head"] else "", u["it"][:55]))
            print("            ZH: %s" % (u["zh"][:55] or "(空)"))
        summary.append((sid, len(story["paras"]), len(new_paras), len(mism)))
        if WRITE:
            story["paras"] = new_paras
            out = "window.__GRIMM_STORIES__['%s'] = %s;\n" % (sid, json.dumps(story, ensure_ascii=False, indent=2))
            with io.open(os.path.join("data", "st", sid + ".js"), "w", encoding="utf-8") as f:
                f.write(out)
            print("  [WRITE] data/st/%s.js (%d 单元)" % (sid, len(new_paras)))

    print("\n=== 汇总 ===")
    print("篇目 | 原段数 | 单元数 | 不一致段")
    for sid, a, b, c in summary:
        print("  %s | %5d | %5d | %d" % (sid, a, b, c))
    if WRITE:
        total = sync_meta()
        print("\n[WRITE] meta.para_count 已同步为 %d（全部篇目单元总数），voice 字段已更新" % total)


if __name__ == "__main__":
    main()
