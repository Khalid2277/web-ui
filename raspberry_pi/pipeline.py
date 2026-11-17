"""Two-stage tomato leaf pipeline for the Pi: YOLO leaf detector -> per-leaf disease classifier.

Runs on OpenCV's cv2.dnn only (no extra packages). A model package directory contains:
  detector.onnx    class-agnostic leaf detector (YOLO, single class)
  classifier.onnx  distilled disease classifier
  model.json       classes, input sizes, normalisation, temperature, thresholds, metrics
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np


class LeafPipeline:
    def __init__(self, model_dir):
        d = Path(model_dir)
        self.meta = json.load(open(d / "model.json"))
        self.classes = self.meta["classes"]
        c = self.meta["classifier"]
        self.cls_res = c["input_size"]
        self.mean = np.array(c["mean"], np.float32)
        self.std = np.array(c["std"], np.float32)
        self.temperature = c.get("temperature", 1.0)
        self.uncertain_below = c.get("uncertain_below", 0.5)
        self.cls_net = cv2.dnn.readNetFromONNX(str(d / "classifier.onnx"))
        det = self.meta.get("detector")
        self.det_net = cv2.dnn.readNetFromONNX(str(d / "detector.onnx")) if det else None
        if det:
            self.det_res = det["input_size"]
            self.det_conf = det.get("conf", 0.35)
            self.det_iou = det.get("iou", 0.5)
            self.max_leaves = det.get("max_leaves", 8)

    # ---------------------------------------------------------------- classifier
    def _prep(self, bgr):
        """Resize shorter side to input size, centre crop (matches training eval transform)."""
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        s = self.cls_res / min(h, w)
        rgb = cv2.resize(rgb, (max(self.cls_res, round(w * s)), max(self.cls_res, round(h * s))),
                         interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        h, w = rgb.shape[:2]
        y0, x0 = (h - self.cls_res) // 2, (w - self.cls_res) // 2
        rgb = rgb[y0:y0 + self.cls_res, x0:x0 + self.cls_res]
        x = (rgb.astype(np.float32) / 255.0 - self.mean) / self.std
        return x.transpose(2, 0, 1)

    def classify(self, crops):
        """Return calibrated probability vectors for a list of BGR crops."""
        if not crops:
            return np.zeros((0, len(self.classes)), np.float32)
        out = []
        for crop in crops:  # one at a time: fixed-batch ONNX graphs are the most compatible
            self.cls_net.setInput(self._prep(crop)[None])
            logits = self.cls_net.forward()[0] / self.temperature
            e = np.exp(logits - logits.max())
            out.append(e / e.sum())
        return np.stack(out)

    # ---------------------------------------------------------------- detector
    def detect(self, bgr):
        """Letterbox to the detector size, run YOLO, return [(x0, y0, x1, y1, score)] in frame pixels."""
        h, w = bgr.shape[:2]
        s = self.det_res / max(h, w)
        nw, nh = round(w * s), round(h * s)
        canvas = np.full((self.det_res, self.det_res, 3), 114, np.uint8)
        px, py = (self.det_res - nw) // 2, (self.det_res - nh) // 2
        canvas[py:py + nh, px:px + nw] = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0, swapRB=True)
        self.det_net.setInput(blob)
        pred = self.det_net.forward()[0]        # (4 + nc, N): cx, cy, w, h, class scores
        scores = pred[4:].max(axis=0)
        keep = scores >= self.det_conf
        if not keep.any():
            return []
        cx, cy, bw, bh = pred[0][keep], pred[1][keep], pred[2][keep], pred[3][keep]
        boxes = np.stack([(cx - bw / 2 - px) / s, (cy - bh / 2 - py) / s, bw / s, bh / s], axis=1)
        idx = cv2.dnn.NMSBoxes(boxes.tolist(), scores[keep].tolist(), self.det_conf, self.det_iou)
        idx = np.array(idx).flatten()[: self.max_leaves]
        out = []
        for i in idx:
            x, y, bw_, bh_ = boxes[i]
            x0, y0 = max(0, int(x)), max(0, int(y))
            x1, y1 = min(w, int(x + bw_)), min(h, int(y + bh_))
            if x1 - x0 >= 24 and y1 - y0 >= 24:
                out.append((x0, y0, x1, y1, float(scores[keep][i])))
        return out

    # ---------------------------------------------------------------- full frame
    def run(self, bgr):
        t0 = time.time()
        h, w = bgr.shape[:2]
        boxes = self.detect(bgr) if self.det_net is not None else []
        t_det = time.time() - t0
        crops = []
        for x0, y0, x1, y1, _ in boxes:
            mx, my = int((x1 - x0) * 0.08), int((y1 - y0) * 0.08)  # same context margin as training crops
            crops.append(bgr[max(0, y0 - my):min(h, y1 + my), max(0, x0 - mx):min(w, x1 + mx)])
        probs = self.classify(crops)
        leaves = []
        for (x0, y0, x1, y1, ds), p in zip(boxes, probs):
            leaves.append(self._describe(p, box=[x0 / w, y0 / h, x1 / w, y1 / h], det_score=round(ds, 3)))
        # Drop detections the classifier says are not tomato leaves.
        leaves = [l for l in leaves if l["label"] != "no_tomato_leaf"]
        frame = None
        if not leaves:  # nothing detected: classify the whole frame (handles 'no tomato leaf in view')
            frame = self._describe(self.classify([bgr])[0], box=None)
        return {"leaves": leaves, "frame": frame, "ms_detect": round(t_det * 1000),
                "ms_total": round((time.time() - t0) * 1000)}

    def _describe(self, p, box, **extra):
        order = np.argsort(p)[::-1]
        best = int(order[0])
        top = [{"label": self.classes[i], "p": round(float(p[i]), 4)} for i in order[:3]]
        label = self.classes[best]
        status = "uncertain" if p[best] < self.uncertain_below and label != "no_tomato_leaf" else "confident"
        return {"label": label, "p": round(float(p[best]), 4), "status": status, "top": top,
                "probs": [round(float(x), 4) for x in p], "box": box, **extra}
