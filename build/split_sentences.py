# -*- coding: utf-8 -*-
"""Split work 01 paragraphs into sentence-level bilingual units.

Each output unit = one Italian sentence + one corresponding Chinese sentence,
so the existing sentence-level reader renders them one-by-one. Audio paths are
rewritten per sentence (audio/01/sNNN.mp3, audio/01/zsNNN.mp3) so the TTS
gen scripts (which iterate paras and use pid / audio_zh) regenerate per-sentence.

Alignment: split IT on . ! ? ; split ZH on 。 ！ ？. When counts differ, pair
min(N,M) 1:1 and append the longer side's tail into the LAST unit, so every
unit stays a complete bilingual pair (no orphaned untranslated sentence).
"""
import re, json, io, os, sys

SID = "01"
ABBR = r'(?:n|n°|no|ecc|es|prof|dott|sig|fig|art|vol|p|cfr|sec|app|cap|pag|tel)\.'
WRITE = "--write" in sys.argv


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


PUNCT_ONLY = re.compile(r'^[\s"\'""\'\'「」『』（）()…—，、：；！？。%0-9¹²³⁴⁵⁶⁷⁸⁹]+$')


def clean_parts(parts):
    """Drop/merge fragments that are only quotes or punctuation."""
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


def main():
    # 从 git 取回的原始127段版重切（当前 data/st/01.js 已是旧切分结果）
    src = os.path.join("build", "_orig_01.js")
    if not os.path.exists(src):
        src = os.path.join("data", "st", SID + ".js")
    story = load_story(SID, src)
    paras = story["paras"]
    new_paras = []
    idx = 1
    mism = []
    empty_zh_paras = []
    for p in paras:
        its = split_it(p.get("it"))
        zhs = split_zh(p.get("zh"))
        if not zhs and p.get("zh"):
            pass
        if not zhs and not p.get("zh"):
            empty_zh_paras.append(p.get("pid"))
        pairs = align(its, zhs)
        if len(its) != len(zhs):
            mism.append((p.get("pid"), len(its), len(zhs)))
        head = p.get("head", False)
        sec = p.get("sec", 1)
        for j, (it_s, zh_s) in enumerate(pairs):
            audio_zh = ("audio/%s/zs%03d.mp3" % (SID, idx)) if zh_s else ""
            new_paras.append({
                "pid": "s%03d" % idx,
                "sec": sec,
                "it": it_s,
                "zh": zh_s,
                "head": bool(head and j == 0),
                "audio": "audio/%s/s%03d.mp3" % (SID, idx),
                "audio_zh": audio_zh,
            })
            idx += 1

    print("=== 分析报告 (work %s) ===" % SID)
    print("原段落数: %d | 拆出句级单元数: %d" % (len(paras), len(new_paras)))
    print("句数一致段落: %d | 不一致(已把多出的并到最后一句): %d"
          % (len(paras) - len(mism), len(mism)))
    if mism:
        print("\n-- 不一致段落 (pid, 意句数, 中句数) --")
        for pid, ni, nz in mism:
            print("  %s: IT=%d ZH=%d" % (pid, ni, nz))
    if empty_zh_paras:
        print("\n-- 原中文为空的段落(保留意语, 中文留空): %s --" % empty_zh_paras)
    print("\n-- 前 6 个句级单元预览 --")
    for u in new_paras[:6]:
        print("  [%s] IT: %s" % (u["pid"], u["it"][:60]))
        print("       ZH: %s" % (u["zh"][:60] or "(空)"))

    if WRITE:
        story["paras"] = new_paras
        out = "window.__GRIMM_STORIES__['%s'] = %s;\n" % (SID, json.dumps(story, ensure_ascii=False, indent=2))
        with io.open(os.path.join("data", "st", SID + ".js"), "w", encoding="utf-8") as f:
            f.write(out)
        print("\n[WRITE] 已写入 data/st/%s.js (%d 单元)" % (SID, len(new_paras)))


if __name__ == "__main__":
    main()
