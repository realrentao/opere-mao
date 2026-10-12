# -*- coding: utf-8 -*-
"""Generate Italian TTS audio for Mao works via edge-tts.
One mp3 per paragraph: audio/<id>/<pid>.mp3
Resume-safe (skips existing). Concurrent with semaphore.

Adapted from grimm-fiabe/build/gen_audio.py for the opere-mao repo.
This round: only IDS=["01"] (first-work audio validation).
"""
import asyncio, json, os, time
import edge_tts

OUT = r"D:/意大利语材料/opere-mao"
VOICE = "it-IT-ElsaNeural"
MANIFEST = os.path.join(OUT, "build", "audio_manifest.json")
IDS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19"]
CONCURRENCY = 4   # gentle: the upstream TTS endpoint throttles high concurrency (403)
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


async def gen_one(sem, item):
    global done
    sid = item["id"]
    out_dir = os.path.join(OUT, "audio", sid)
    os.makedirs(out_dir, exist_ok=True)
    tasks = []
    for para in item["paras"]:
        pid = para["pid"]
        text = para["text"].strip()
        # poem line-breaks written as " / " must be a PAUSE, not pronounced
        text = text.replace(" / ", ", ")
        path = os.path.join(out_dir, pid + ".mp3")
        if os.path.exists(path) and os.path.getsize(path) > 200:
            async with lock:
                done += 1
            continue
        if not text:
            async with lock:
                done += 1
            continue
        tasks.append((path, text))
    for path, text in tasks:
        async with sem:
            for attempt in range(6):
                try:
                    comm = edge_tts.Communicate(text, VOICE)
                    await asyncio.wait_for(comm.save(path), timeout=45)
                    if os.path.getsize(path) > 200:
                        break
                except Exception as e:
                    await asyncio.sleep(1.5 * (attempt + 1))
            async with lock:
                done += 1
                if done % 25 == 0 or done == total:
                    log(f"progress {done}/{total}  ({done*100//max(total,1)}%)  t={int(time.time()-start)}s")


async def main():
    global total
    man = json.load(open(MANIFEST, encoding="utf-8"))
    man = [s for s in man if s["id"] in IDS]
    total = sum(len(s["paras"]) for s in man)
    existing = 0
    for s in man:
        d = os.path.join(OUT, "audio", s["id"])
        if os.path.isdir(d):
            existing += sum(1 for p in s["paras"] if os.path.exists(os.path.join(d, p["pid"] + ".mp3")))
    log(f"IDS={IDS} | TOTAL paras={total} | already on disk={existing} | to generate={total-existing}")
    sem = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*[gen_one(sem, s) for s in man])
    log(f"ALL DONE {done}/{total}  total_time={int(time.time()-start)}s")


if __name__ == "__main__":
    asyncio.run(main())
