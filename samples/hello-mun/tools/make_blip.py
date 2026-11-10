#!/usr/bin/env python3
"""Write the sample's sound: a short 880 Hz tone with a soft start and end,
PCM 16-bit mono at 48 kHz. Deterministic: the same bytes every time."""
import math
import struct
import sys

RATE, SECONDS, FREQUENCY, PEAK = 48000, 0.07, 880.0, 0.5
frames = int(RATE * SECONDS)
samples = bytearray()
for n in range(frames):
    envelope = min(1.0, n / 240.0, (frames - n) / 960.0)
    value = int(32767 * PEAK * envelope * math.sin(2 * math.pi * FREQUENCY * n / RATE))
    samples += struct.pack("<h", value)
header = b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVE" + b"fmt " + \
    struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16) + b"data" + struct.pack("<I", len(samples))
with open(sys.argv[1], "wb") as out:
    out.write(header + samples)
