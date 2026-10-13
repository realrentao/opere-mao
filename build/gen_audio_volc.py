#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
火山方舟「音频生成」(seed-audio-1.0) 批量生成脚本 —— opere-mao 双语音频
替代已失效的 edge-tts。

接口：POST https://openspeech.bytedance.com/api/v3/tts/create
鉴权：header  X-Api-Key: <API Key>            （新版控制台单头鉴权，API Key 即 volc_config.json 的 api_key）
返回：JSON {"audio":"<base64 mp3>", ...}        （成功响应只有 audio 字段、无 code）

两种音色模式：
  A) 描述模式（默认）：用 text_prompt 前导描述控制音色（如「沉稳男声」）。
     缺点：seed-audio-1.0 每次合成音色随机，逐句不一致。
  B) 克隆模式（--clone）：把一段「范本音频」作为参考音频(audio_data/base64)，
     在 text_prompt 里用 @音频1 引用，锁定同一音色，逐句完全一致。
     —— 本阅读器统一用此模式，范本即 work 01 的 s004(意)/zs004(中)。

用法：
  python gen_audio_volc.py --dry            # 只枚举任务数，不合成
  python gen_audio_volc.py --lang it        # 只生成意大利语
  python gen_audio_volc.py --lang zh        # 只生成中文
  python gen_audio_volc.py                  # 生成 it + zh（描述模式）
  python gen_audio_volc.py --test           # 各合成 1 句验证
  python gen_audio_volc.py --clone          # 克隆模式（读 volc_config.json 的 clone_it/clone_zh）
  python gen_audio_volc.py --clone --force  # 克隆模式 + 强制覆盖已存在文件（用于统一换音色）
  python gen_audio_volc.py --clone --work 01 --lang it   # 指定篇/语种

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
RETRIES = 4
MIN_BYTES = 200
TIMEOUT = 30                # 单段请求超时；超时即重试，避免 120s 长挂
CHUNK_CHARS = 2500          # 描述模式 text_prompt 上限 3000
CLONE_CHUNK_CHARS = 380     # 克隆模式建议每句 <=400 字符
_aborted = [False]          # 命中「服务未授权」时整体中止，避免空耗

# 默认「沉稳男声」前导描述（描述模式用；克隆模式不使用）
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

def load_reference_b64(path):
    """读取范本音频并 base64 编码（克隆模式用）"""
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    if not os.path.exists(path):
        print(f"[参考音频缺失] {path}")
        sys.exit(1)
    return base64.b64encode(open(path, "rb").read()).decode("ascii")

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

async def synth_one(session, api_key, text, ref_b64=None, style_prefix=""):
    """合成一段（自动切分拼接），返回 mp3 bytes 或抛异常。
    ref_b64 非空 → 克隆模式（参考音频锁定音色）；否则描述模式。"""
    limit = CLONE_CHUNK_CHARS if ref_b64 else CHUNK_CHARS
    pieces = split_text(text, limit)
    parts = []
    for seg in pieces:
        if ref_b64:
            body = {
                "model": MODEL,
                "text_prompt": "@音频1 " + seg,
                "references": [{"audio_data": ref_b64}],
                "audio_config": {"format": "mp3", "sample_rate": 48000},
                "watermark": {},
            }
        else:
            body = {
                "model": MODEL,
                "text_prompt": (style_prefix + seg) if style_prefix else seg,
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

async def run(lang, prefix, work=None, ref_b64=None, force=False):
    api_key, _ = load_config()
    _aborted[0] = False
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
            if _aborted[0]:
                skipped += 1
                return
            sub = "zs" if lang == "zh" else "s"
            out = os.path.join(AUDIO, wid, f"{sub}{pid[1:]}.mp3")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if not force and os.path.exists(out) and os.path.getsize(out) >= MIN_BYTES:
                skipped += 1
                return
            async with sem:
                try:
                    data = await synth_one(session, api_key, text, ref_b64=ref_b64, style_prefix=prefix)
                except RuntimeError as e:
                    if "SERVICE_NOT_GRANTED" in str(e):
                        _aborted[0] = True
                        failed.append((wid, pid, "服务未授权→整体中止"))
                        return
                    failed.append((wid, pid, repr(e)[:160]))
                    return
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

    mode = "克隆" if ref_b64 else "描述"
    print(f"[{lang}][{mode}] 生成 {done}  跳过已存在 {skipped}  失败 {len(failed)}")
    for f in failed[:20]:
        print("   失败:", f)
    return done, len(failed)

def main():
    args = sys.argv[1:]
    dry = "--dry" in args
    test = "--test" in args
    force = "--force" in args
    clone = "--clone" in args
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

    _, cfg = load_config()

    # 克隆模式：加载范本音频（程序启动即读入内存，后续覆盖原文件也不影响）
    clone_refs = {}
    if clone:
        clone_it = load_reference_b64(cfg.get("clone_it"))
        clone_zh = load_reference_b64(cfg.get("clone_zh"))
        if not clone_it or not clone_zh:
            print("[克隆模式] 请在 volc_config.json 设置 clone_it / clone_zh 范本音频路径")
            sys.exit(1)
        clone_refs = {"it": clone_it, "zh": clone_zh}

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
        api_key, _ = load_config()
        prefixes = {**DEFAULT_PREFIX, **(cfg.get("prefix") or {})}
        async def t():
            async with aiohttp.ClientSession() as s:
                for L, txt, ref in [("it", "Studio dell'educazione fisica.", clone_refs.get("it")),
                                    ("zh", "体育之研究。", clone_refs.get("zh"))]:
                    try:
                        d = await synth_one(s, api_key, txt, ref_b64=ref, style_prefix=prefixes.get(L, ""))
                        print(f"[{L}][{'克隆' if ref else '描述'}] OK {len(d)} bytes")
                    except Exception as e:
                        print(f"[{L}] FAIL {repr(e)[:160]}")
        asyncio.run(t())
        return

    prefixes = {**DEFAULT_PREFIX, **(cfg.get("prefix") or {})}
    if lang in ("it", "zh"):
        ref = clone_refs.get(lang) if clone else None
        asyncio.run(run(lang, prefixes.get(lang, ""), work, ref_b64=ref, force=force))
    else:
        for L in ("it", "zh"):
            ref = clone_refs.get(L) if clone else None
            asyncio.run(run(L, prefixes.get(L, ""), work, ref_b64=ref, force=force))

if __name__ == "__main__":
    main()
