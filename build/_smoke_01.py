# -*- coding: utf-8 -*-
"""Smoke test work 01 in all 4 modes via lettura.html (opere-mao repo)."""
import asyncio, json, re, io
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8178/lettura.html?id=01"
MODES = ["pair", "it", "zh", "col"]
SID = "01"

# compute expected para count from data file
_t = io.open(r"D:/意大利语材料/opere-mao/data/st/01.js", encoding="utf-8").read()
_m = re.search(r"window\.__GRIMM_STORIES__\['" + SID + r"'\] = (\{.*\});\s*$", _t, re.S)
EXPECT = len(json.loads(_m.group(1))["paras"])
print("work 01 expected paras =", EXPECT)


async def run():
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", args=["--no-sandbox"])
        for mode in MODES:
            ctx = await browser.new_context()
            await ctx.add_init_script(
                "localStorage.setItem('mao_prefs_v1', JSON.stringify(%s));"
                % json.dumps({"mode": mode, "fs": False, "cont": False,
                               "follow": False, "loop": False, "rate": 1, "lang": "it"})
            )
            page = await ctx.new_page()
            cerr = []
            page.on("console", lambda m: cerr.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: cerr.append(str(e)))
            await page.goto(BASE, wait_until="networkidle")
            try:
                await page.wait_for_selector(".para", timeout=15000)
            except Exception as ex:
                errors.append("%s: no .para (%s)" % (mode, ex))
                await ctx.close()
                continue
            paras = await page.eval_on_selector_all(".para", "els=>els.length")
            its = await page.eval_on_selector_all(".it-line", "els=>els.length")
            zhs = await page.eval_on_selector_all(".zh-line", "els=>els.length")
            cls = await page.eval_on_selector("body", "b=>b.className")
            ok = (paras == EXPECT and its == EXPECT and zhs == EXPECT
                  and cls == "mode-" + mode and not cerr)
            if not ok:
                errors.append("%s: paras=%s it=%s zh=%s cls=%s console=%s"
                              % (mode, paras, its, zhs, cls, cerr[:3]))
            print("%-5s paras=%d it=%d zh=%d cls=%s console=%d %s"
                  % (mode, paras, its, zhs, cls, len(cerr), "OK" if ok else "FAIL"))
            await ctx.close()
        await browser.close()
    if errors:
        print("SMOKE FAIL:", errors)
        raise SystemExit(1)
    print("SMOKE ALL OK — work 01 x 4 modes")


asyncio.run(run())
