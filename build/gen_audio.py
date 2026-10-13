# -*- coding: utf-8 -*-
"""Generate Italian TTS audio for Mao works via edge-tts (fixed DiegoNeural voice).
One mp3 per paragraph: audio/<id>/<pid>.mp3
Reads text from data/st/<id>.js (field "it").
Resume-safe (skips existing >200 bytes). Voice: it-IT-DiegoNeural.

NOTE: edge-tts was revived 2026-10-13 by applying rany2/edge-tts PR #491
(WSS endpoint -> api.msedgeservices.com, Ocp-Apim-Subscription-Key auth).
Fixed neural voices keep every sentence's timbre identical.
"""
import asyncio, json, os, re, io, time
import edge_tts

OUT = r"D:/意大利语材料/opere-mao"
VOICE = "it-IT-DiegoNeural"   # 意大利语沉稳男声（固定音色，逐句一致）
IDS = ["01"]
CONCURRENCY = 4
LOG = os.path.join(OUT, "build", "audio_gen.log")

done = 0
total = 0
lock = asyncio.Lock()
start = time.time()


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_paras(sid):
    fn = os.path.join(OUT, "data", "st", sid + ".js")
    t = io.open(fn, encoding="utf-8").read()
    m = re.search(r"window\.__GRIMM_STORIES__\['" + sid + r"'\] = (\{.*\});\s*$", t, re.S)
    return json.loads(m.group(1))["paras"]


async def gen_one(sem, sid, para):
    global done
    pid = para.get("pid")
    text = (para.get("it") or "").strip()
    # poem line-breaks written as " / " must be a PAUSE, not pronounced
    text = text.replace(" / ", ", ")
    if not text:
        async with lock:
            done += 1
        return
    path = os.path.join(OUT, "audio", sid, pid + ".mp3")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > 200:
        async with lock:
            done += 1
        return
    async with sem:
        for attempt in range(8):
            try:
                comm = edge_tts.Communicate(text, VOICE)
                await asyncio.wait_for(comm.save(path), timeout=60)
                if os.path.getsize(path) > 200:
                    break
            except Exception as e:
                await asyncio.sleep(2 * (attempt + 1))
    async with lock:
        done += 1
        if done % 25 == 0 or done == total:
            log(f"progress {done}/{total}  ({done*100//max(total,1)}%)  t={int(time.time()-start)}s")


async def main():
    global total
    items = []
    for sid in IDS:
        for p in load_paras(sid):
            items.append((sid, p))
    total = len(items)
    existing = 0
    for sid, p in items:
        path = os.path.join(OUT, "audio", sid, p.get("pid", "") + ".mp3")
        if os.path.exists(path) and os.path.getsize(path) > 200:
            existing += 1
    log(f"IDS={IDS} | TOTAL={total} | on disk={existing} | to generate={total-existing}")
    sem = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*[gen_one(sem, sid, p) for sid, p in items])
    log(f"ALL DONE {done}/{total}  total_time={int(time.time()-start)}s")


if __name__ == "__main__":
    asyncio.run(main())
