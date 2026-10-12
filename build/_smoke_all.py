# -*- coding: utf-8 -*-
"""Smoke test ALL 19 works in all 4 modes via lettura.html (opere-mao repo).
Validates DOM rendering only (no audio playback needed)."""
import asyncio, json, re, io
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8178/lettura.html?id=%s"
MODES = ["pair", "it", "zh", "col"]
ROOT = r"D:/意大利语材料/opere-mao"
SIDS = ["%02d" % i for i in range(1, 20)]


def expected(sid):
    _t = io.open(os.path.join(ROOT, "data", "st", sid + ".js"), encoding="utf-8").read()
    m = re.search(r"window\.__GRIMM_STORIES__\['" + sid + r"'\] = (\{.*\});\s*$", _t, re.S)
    paras = json.loads(m.group(1))["paras"]
    total = len(paras)
    zh_count = sum(1 for p in paras if p.get("zh"))
    return total, zh_count


async def run():
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", args=["--no-sandbox"])
        for sid in SIDS:
            total, zh_count = expected(sid)
            for mode in MODES:
                ctx = await browser.new_context()
                await ctx.add_init_script(
                    "localStorage.setItem('mao_prefs_v1', JSON.stringify(%s));"
                    % json.dumps({"mode": mode, "fs": False, "cont": False,
                                   "follow": False, "loop": False, "rate": 1, "lang": "it"})
                )
                page = await ctx.new_page()
                cerr = []
                bad404 = []
                page.on("console", lambda m: cerr.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: cerr.append(str(e)))
                page.on("response", lambda r: bad404.append(r.url) if r.status == 404 else None)
                await page.goto(BASE % sid, wait_until="networkidle")
                try:
                    await page.wait_for_selector(".para", timeout=20000)
                except Exception as ex:
                    errors.append("%s/%s: no .para (%s)" % (sid, mode, ex))
                    await ctx.close()
                    continue
                paras = await page.eval_on_selector_all(".para", "els=>els.length")
                its = await page.eval_on_selector_all(".it-line", "els=>els.length")
                zhs = await page.eval_on_selector_all(".zh-line", "els=>els.length")
                cls = await page.eval_on_selector("body", "b=>b.className")
                # Pending-audio 404s (audio/<id>/s*.mp3) are expected before TTS generation.
                real_cerr = [e for e in cerr if "404" not in e]
                audio_404_only = (not bad404) or all(
                    ("/audio/" in u) or u.rstrip("/").endswith("favicon.ico") for u in bad404)
                ok = (paras == total and its == total and zhs == zh_count
                      and cls == "mode-" + mode and not real_cerr and audio_404_only)
                note = " (audio pending)" if (bad404 and audio_404_only) else ""
                if not ok:
                    errors.append("%s/%s: paras=%s(it=%s) zh=%s(exp %s/%s) cls=%s cerr=%s bad=%s"
                                  % (sid, mode, paras, its, zhs, total, zh_count, cls, real_cerr[:2], bad404[:2]))
                print("%s %-5s paras=%d it=%d zh=%d(exp %d/%d) cls=%s %s%s"
                      % (sid, mode, paras, its, zhs, total, zh_count, cls,
                         "OK" if ok else "FAIL", note))
                await ctx.close()
        await browser.close()
    if errors:
        print("\nSMOKE FAIL (%d issues):" % len(errors))
        for e in errors:
            print("  -", e)
        raise SystemExit(1)
    print("\nSMOKE ALL OK — 19 works x 4 modes (76 renders)")


import os
asyncio.run(run())
