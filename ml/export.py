"""Export the trained model to ONNX and check it matches PyTorch.

Usage:  python ml/export.py
"""
import json

import numpy as np
import torch
from torchvision import models

ckpt = torch.load("ml/model.pt", map_location="cpu")
classes = ckpt["classes"]

model = models.mobilenet_v3_small(weights=None)
model.classifier[3] = torch.nn.Linear(model.classifier[3].in_features, len(classes))
model.load_state_dict(ckpt["model"])
model.eval()

dummy = torch.randn(1, 3, 224, 224)
try:
    torch.onnx.export(model, dummy, "ml/model.onnx", input_names=["image"],
                      output_names=["logits"], opset_version=17, dynamo=False)
except TypeError:
    torch.onnx.export(model, dummy, "ml/model.onnx", input_names=["image"],
                      output_names=["logits"], opset_version=17)

with open("ml/classes.json", "w") as f:
    json.dump(classes, f, indent=2)
print("Saved ml/model.onnx and ml/classes.json")

# Check the ONNX output matches PyTorch
import onnxruntime as ort
sess = ort.InferenceSession("ml/model.onnx")
onnx_out = sess.run(None, {"image": dummy.numpy()})[0]
torch_out = model(dummy).detach().numpy()
print("Max difference vs PyTorch:", float(np.abs(onnx_out - torch_out).max()))