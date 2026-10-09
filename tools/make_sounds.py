#!/usr/bin/python3
"""Regenerate sportlock/locks/ui/lock_screen/sounds/*.wav: clock-like ticks (filtered noise click + short resonance)."""
import math
import random
import struct
import wave
from pathlib import Path

RATE = 44100
OUT = Path(__file__).resolve().parent.parent / "sportlock" / "locks" / "ui" / "lock_screen" / "sounds"


def render(name, ms, parts, volume):
    random.seed(7)
    n = int(RATE * ms / 1000)
    out = [0.0] * n
    for kind, freq, decay, amp in parts:
        prev = 0.0
        for i in range(n):
            t = i / RATE
            env = math.exp(-t / decay)
            if kind == "noise":
                x = random.uniform(-1, 1)
                out[i] += amp * env * (x - prev * 0.6)  # one-pole high-pass: crisp click
                prev = x
            else:
                out[i] += amp * env * math.sin(2 * math.pi * freq * t)
    peak = max(abs(v) for v in out) or 1
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(volume * 32767 * v / peak)) for v in out))


OUT.mkdir(parents=True, exist_ok=True)
render("tick.wav", 25, [("noise", 0, 0.0012, 1.0), ("tone", 3200, 0.002, 0.35), ("tone", 5100, 0.0012, 0.2)], 0.55)
render("tock.wav", 45, [("noise", 0, 0.0018, 0.8), ("tone", 1500, 0.006, 0.6), ("tone", 2400, 0.003, 0.3)], 0.6)
render("ding.wav", 600, [("tone", 880, 0.2, 1.0), ("tone", 1760, 0.08, 0.3)], 0.6)
