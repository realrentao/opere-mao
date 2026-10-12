#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
火山方舟「音频生成」(seed-audio-1.0) 批量生成脚本 —— opere-mao 双语音频
替代已失效的 edge-tts。

接口：POST https://openspeech.bytedance.com/api/v3/tts/create
鉴权：header  X-Api-Key: <API Key>            （新版控制台单头鉴权，API Key 即 volc_config.json 的 api_key）
返回：JSON {code:10000, audio:"<base64 mp3>", ...}

模型 seed-audio-1.0 原生支持意大利语（及中文），无需旧版语音合成资源。
音色用 text_prompt 前导描述控制（默认「沉稳男声」），可在 volc_config.json 的
prefix_it / prefix_zh 调整。

用法：
  python gen_audio_volc.py --dry            # 只枚举任务数，不合成
  python gen_audio_volc.py --lang it        # 只生成意大利语
  python gen_audio_volc.py --lang zh        # 只生成中文
  python gen_audio_volc.py                  # 生成 it + zh
  python gen_audio_volc.py --test           # 各合成 1 句验证音色

数据来源：data/st/NN.js（单一事实源，含 it / zh 双语文本）
输出：     audio/NN/sNNN.mp3（意）  audio/NN/zsNNN.mp3（中）
"""
import os, re, sys, json, asyncio, aiohttp, base64, uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ST = os.path.join(ROOT, "data", "st")
AUDIO = os.path.join(ROOT, "audio")
CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "volc_config.json")
URL = "https://openspeech.bytedance.com/api/v3/tts/create"
MODEL = "seed-audio-1.0"

CONCURRENCY = 4
RETRIES = 6
MIN_BYTES = 200
TIMEOUT = 120
CHUNK_CHARS = 2500          # text_prompt 上限 3000，超长按标点切分

# 默认「沉稳男声」前导描述（可在 volc_config.json 用 prefix_it / prefix_zh 覆盖；置空则不加）
DEFAULT_PREFIX = {
    "it": "(voce maschile, calma e profonda) ",
    "zh": "(沉稳的男声) ",
}

CJK = re.compile(r"[一-鿿]")
def has_cjk(s): return bool(CJK.search(s or ""))

def load_config():
    api_key = os.environ.get("ARK_API_KEY")
    cfg = {}
    if os.path.exists(CFG_PATH):
        try:
            cfg = json.load(open(CFG_PATH, encoding="utf-8"))
        except Exception:
            cfg = {}
    if not api_key:
        api_key = cfg.get("api_key")
    if not api_key:
        print("[配置缺失] 请在 build/volc_config.json 填 api_key，或设置环境变量 ARK_API_KEY。")
        print('  示例：{"api_key": "你的方舟API Key (UUID 格式)"}')
        sys.exit(1)
    return api_key, cfg

def build_tasks(work=None):
    """返回 {'it':[(wid,pid,text)...], 'zh':[...]}；work 指定时只含该篇"""
    tasks = {"it": [], "zh": []}
    if not os.path.isdir(DATA_ST):
        return tasks
    for fn in sorted(os.listdir(DATA_ST)):
        if not fn.endswith(".js"):
            continue
        s = open(os.path.join(DATA_ST, fn), encoding="utf-8").read()
        m = re.search(r"window\.__GRIMM_STORIES__\['(\d+)'\]\s*=\s*(\{.*\})\s*;\s*$", s, re.S)
        if not m:
            continue
        wid = m.group(1)
        if work and wid != work:
            continue
        try:
            obj = json.loads(m.group(2))
        except Exception:
            continue
        for p in obj.get("paras", []):
            pid = p.get("pid")
            if not pid:
                continue
            it = (p.get("it") or "").strip()
            zh = (p.get("zh") or "").strip()
            if it:
                tasks["it"].append((wid, pid, it))
            if zh:
                tasks["zh"].append((wid, pid, zh))
    return tasks

def split_text(text, limit=CHUNK_CHARS):
    """按标点切分，避免超过 text_prompt 上限"""
    pieces, buf = [], ""
    for ch in text:
        buf += ch
        if len(buf) >= limit and ch in " ,.;:!?，。；：！？\n":
            pieces.append(buf.strip()); buf = ""
    if buf.strip():
        pieces.append(buf.strip())
    return pieces or [text]

async def synth_one(session, api_key, prompt):
    """合成一段（自动切分拼接），返回 mp3 bytes 或抛异常"""
    pieces = split_text(prompt)
    parts = []
    for seg in pieces:
        body = {
            "model": MODEL,
            "text_prompt": seg,
            "audio_config": {"format": "mp3", "sample_rate": 48000},
            "watermark": {},
        }
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
            "X-Api-Request-Id": uuid.uuid4().hex,
        }
        last = Exception("no response")
        for attempt in range(RETRIES):
            try:
                async with session.post(URL, json=body, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                    j = await r.json()
                # 成功响应只有 audio 字段、无 code；错误响应才有 code+message
                if j.get("audio"):
                    parts.append(base64.b64decode(j["audio"]))
                    break
                last = Exception(f"code={j.get('code')} msg={j.get('message')}")
                if "not granted" in str(j.get("message", "")) or str(j.get("code")) == "45000030":
                    # 服务未授权属于账号侧问题，不必重试
                    raise RuntimeError(f"SERVICE_NOT_GRANTED: {j.get('message')}")
            except Exception as e:
                last = e
                if isinstance(e, RuntimeError):
                    raise
            await asyncio.sleep(1.5 * (attempt + 1))
        else:
            raise last
    return b"".join(parts)

async def run(lang, prefix, work=None):
    api_key, _ = load_config()
    tasks = build_tasks(work)[lang]
    if not tasks:
        print(f"[{lang}] 无任务")
        return 0, 0
    done = skipped = 0
    failed = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async with aiohttp.ClientSession() as session:
        async def worker(wid, pid, text):
            nonlocal done, skipped
            sub = "zs" if lang == "zh" else "s"
            out = os.path.join(AUDIO, wid, f"{sub}{pid[1:]}.mp3")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if os.path.exists(out) and os.path.getsize(out) >= MIN_BYTES:
                skipped += 1
                return
            prompt = (prefix + text) if prefix else text
            async with sem:
                try:
                    data = await synth_one(session, api_key, prompt)
                except Exception as e:
                    failed.append((wid, pid, repr(e)[:160]))
                    return
            if not data or len(data) < MIN_BYTES:
                failed.append((wid, pid, "empty audio"))
                return
            tmp = out + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, out)
            done += 1

        await asyncio.gather(*[worker(w, p, t) for (w, p, t) in tasks])

    print(f"[{lang}] 生成 {done}  跳过已存在 {skipped}  失败 {len(failed)}")
    for f in failed[:20]:
        print("   失败:", f)
    return done, len(failed)

def main():
    args = sys.argv[1:]
    dry = "--dry" in args
    test = "--test" in args
    lang = "both"
    if "--lang" in args:
        i = args.index("--lang")
        if i + 1 < len(args):
            lang = args[i + 1]
    work = None
    if "--work" in args:
        i = args.index("--work")
        if i + 1 < len(args):
            work = args[i + 1]

    if dry:
        tasks = build_tasks(work)
        for L in ("it", "zh"):
            print(f"  {L}: {len(tasks[L])} 段")
        cnt = 0
        for L in ("it", "zh"):
            sub = "zs" if L == "zh" else "s"
            for (wid, pid, _) in tasks[L]:
                if os.path.exists(os.path.join(AUDIO, wid, f"{sub}{pid[1:]}.mp3")):
                    cnt += 1
        print(f"  磁盘已有: {cnt} 个")
        return

    if test:
        api_key, cfg = load_config()
        prefixes = {**DEFAULT_PREFIX, **(cfg.get("prefix") or {})}
        async def t():
            async with aiohttp.ClientSession() as s:
                # 复用 synth_one：临时给 session 用
                import types
                async def synth(prompt):
                    return await synth_one(s, api_key, prompt)
                for L, txt in [("it", "Studio dell'educazione fisica."),
                               ("zh", "体育之研究。")]:
                    try:
                        d = await synth((prefixes.get(L, "") ) + txt)
                        print(f"[{L}] OK {len(d)} bytes")
                    except Exception as e:
                        print(f"[{L}] FAIL {repr(e)[:160]}")
        asyncio.run(t())
        return

    _, cfg = load_config()
    prefixes = {**DEFAULT_PREFIX, **(cfg.get("prefix") or {})}
    if lang in ("it", "zh"):
        asyncio.run(run(lang, prefixes.get(lang, ""), work))
    else:
        asyncio.run(run("it", prefixes.get("it", ""), work))
        asyncio.run(run("zh", prefixes.get("zh", ""), work))

if __name__ == "__main__":
    main()
