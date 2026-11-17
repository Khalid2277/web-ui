#!/usr/bin/env python3
"""Time ONNX models with the Pi's OpenCV (cv2.dnn), the same runtime the console uses."""
import glob
import re
import sys
import time

import cv2
import numpy as np

cv2.setNumThreads(4)
for path in sorted(glob.glob(sys.argv[1] + "/*.onnx")):
    size = int(re.search(r"_(\d+)\.onnx$", path).group(1))
    try:
        net = cv2.dnn.readNetFromONNX(path)
    except cv2.error as e:
        print(f"{path.split('/')[-1]:42s} LOAD FAILED: {str(e).splitlines()[0][:80]}")
        continue
    x = np.random.rand(1, 3, size, size).astype(np.float32)
    for _ in range(3):
        net.setInput(x)
        net.forward()
    n, t = 12, time.time()
    for _ in range(n):
        net.setInput(x)
        out = net.forward()
    print(f"{path.split('/')[-1]:42s} {1000 * (time.time() - t) / n:7.0f} ms   out {tuple(out.shape)}", flush=True)
