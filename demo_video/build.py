"""Encode scenes, build the soundtrack from the app's sound log, and join everything."""
import json
import os
import subprocess
import wave

import numpy as np

FPS = 30
SR = 48000
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS)]
FADE = 0.4

TL = json.load(open("timeline.json"))
SCENES = [
    ("title", "still", 4.0),
    ("problem", "still", 5.5),
    ("how", "still", 7.0),
    ("A", "ride", len(TL["rides"]["A"]["frames"]) / FPS),
    ("B", "ride", len(TL["rides"]["B"]["frames"]) / FPS),
    ("features", "still", 10.0),
    ("end", "still", 4.5),
]


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def encode(name, kind, dur):
    out = f"scene_{name}.mp4"
    tmp = f"scene_{name}.tmp.mp4"
    fade = f"fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE:.3f}:d={FADE}"
    if kind == "still":
        cmd = ["ffmpeg", "-y", "-framerate", str(FPS), "-i", f"slide_{name}.png",
               "-vf", f"format=yuv420p,loop=-1:1,trim=duration={dur},{fade}", *ENC, tmp]
    else:
        cmd = ["ffmpeg", "-y", "-framerate", str(FPS), "-i", f"frames_{name}/%05d.jpg", "-vf", f"format=yuv420p,{fade}", *ENC, tmp]
    run(cmd)
    os.replace(tmp, out)
    return out


# ---------------- audio ----------------
def tone(freq, ms, amp=0.32):
    n = int(SR * ms / 1000)
    t = np.arange(n) / SR
    sq = np.sign(np.sin(2 * np.pi * freq * t)) * 0.7 + 0.3 * np.sin(2 * np.pi * freq * t)
    env = np.ones(n)
    a = int(SR * 0.005)
    env[:a] = np.linspace(0, 1, a)
    env[-a:] = np.linspace(1, 0, a)
    return amp * sq * env


PATTERNS = {  # identical to web/js/beeper.js
    "caution": ([(1200, 120, 100), (1200, 120, 0)], 0),
    "urgent": ([(2400, 80, 50), (2400, 80, 50), (2400, 80, 0)], 450),
    "hotspot": ([(700, 450, 0)], 0),
}


def add(buf, start_s, clip):
    i = int(start_s * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(clip))
    buf[i:j] += clip[: j - i]


def play(buf, start_s, name):
    t = start_s
    total = 0
    for f, ms, gap in PATTERNS[name][0]:
        add(buf, t, tone(f, ms))
        t += (ms + gap) / 1000
        total += ms + gap
    return total / 1000


def voice(text):
    run(["espeak-ng", "-v", "en-us+m3", "-s", "160", "-a", "180", "-w", "v.wav", text])
    with wave.open("v.wav") as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    x = np.interp(np.arange(0, len(x), rate / SR), np.arange(len(x)), x)
    return 0.85 * x / (np.abs(x).max() + 1e-9)


def lowpass(x, k):
    return np.convolve(x, np.ones(k) / k, mode="same")


def ride_audio(key, dur):
    r = TL["rides"][key]
    n = int(dur * SR)
    buf = np.zeros(n)
    frames = r["frames"]
    # engine: pitch follows speed, quieter when braking (throttle off)
    sp = np.interp(np.arange(n) / SR, [f["t"] for f in frames], [f["speed"] for f in frames])
    br = np.interp(np.arange(n) / SR, [f["t"] for f in frames], [1.0 if f["braking"] else 0.0 for f in frames])
    f0 = 26 + sp * 1.15
    ph = 2 * np.pi * np.cumsum(f0) / SR
    eng = 0.5 * np.sin(ph) + 0.3 * np.sin(2 * ph + 0.4) + 0.2 * np.sin(3 * ph + 1.1)
    rng = np.random.default_rng(1)
    eng += 0.25 * lowpass(rng.standard_normal(n), 40)
    buf += eng * (0.085 - 0.035 * br)
    if r["wet"]:
        buf += 0.05 * (rng.standard_normal(n) - lowpass(rng.standard_normal(n), 6))  # hiss of rain
    ev = json.load(open(f"events_{key}.json"))
    loop_start = None
    for e in ev:
        if e["type"] == "beep":
            play(buf, e["t"], e["name"])
        elif e["type"] == "loop":
            loop_start = e["t"]
        elif e["type"] == "stop" and loop_start is not None:
            t = loop_start
            while t < e["t"]:
                t += play(buf, t, e["name"]) + PATTERNS[e["name"]][1] / 1000
            loop_start = None
        elif e["type"] == "voice":
            add(buf, e["t"], voice(e["text"]))
    # fades to match the picture
    f = int(FADE * SR)
    buf[:f] *= np.linspace(0, 1, f)
    buf[-f:] *= np.linspace(1, 0, f)
    return buf


def slide_audio(dur, base):
    n = int(dur * SR)
    t = np.arange(n) / SR
    pad = sum(np.sin(2 * np.pi * f * t) for f in (base, base * 1.5, base * 2)) / 3
    env = np.minimum(1, np.minimum(t / 0.8, (dur - t) / 0.8))
    return 0.035 * pad * env


def main():
    files = [encode(n, k, d) for n, k, d in SCENES]
    # real durations from the encoded files keep sound and picture aligned
    durs = []
    for f in files:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], capture_output=True, text=True)
        durs.append(float(out.stdout))
    parts = []
    bases = {"title": 110, "problem": 98, "how": 110, "features": 123, "end": 110}
    for (name, kind, _), d in zip(SCENES, durs):
        parts.append(ride_audio(name, d) if kind == "ride" else slide_audio(d, bases[name]))
    audio = np.concatenate(parts)
    audio = audio / max(1.0, np.abs(audio).max() / 0.95)
    with wave.open("soundtrack.wav", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    with open("list.txt", "w") as fh:
        fh.writelines(f"file '{f}'\n" for f in files)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "video_only.mp4"])
    run(["ffmpeg", "-y", "-i", "video_only.mp4", "-i", "soundtrack.wav", "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", "Moto_AI_demo.tmp.mp4"])
    os.replace("Moto_AI_demo.tmp.mp4", "Moto_AI_demo.mp4")
    print("durations:", [round(d, 2) for d in durs], "total", round(sum(durs), 2))


main()
