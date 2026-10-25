"""Suggest a name for a photo of an item, entirely on our own server.

The photo half of a small open-source model (SigLIP2, compressed to 4-bit) turns the picture into 768 numbers.
We compare them with numbers for a fixed list of everyday item names that were worked out ahead of time
(tools/build_labels.py), so the text half of the model is never loaded. Nothing is sent to anyone else.
"""
import io
import json
import os

import numpy as np
from PIL import Image

REPO = "onnx-community/siglip2-base-patch16-224-ONNX"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "naming_data")
SCALE = 100.0           # how sharply the model's scores are turned into percentages
SURE = 0.10             # below this top score we say "Unidentified item" instead of guessing
CHECK = 0.30            # below this the review screen asks the person to double-check the name
UNKNOWN = "Unidentified item"

_state = {}


def _load():
    if not _state:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        o = ort.SessionOptions()
        o.intra_op_num_threads = 1
        o.enable_cpu_mem_arena = False          # keep memory small on a small server
        o.enable_mem_pattern = False
        path = os.environ.get("VISION_PATH") or hf_hub_download(REPO, "onnx/vision_model_bnb4.onnx")
        _state["session"] = ort.InferenceSession(path, o, providers=["CPUExecutionProvider"])
        _state["labels"] = json.load(open(os.path.join(DATA, "labels.json")))
        _state["vectors"] = np.load(os.path.join(DATA, "labels.npy")).astype(np.float32)
    return _state


def suggest(jpeg_bytes, k=3):
    """The k most likely names for a photo, as [(name, score 0-1), ...]. Raises if the picture can't be read."""
    s = _load()
    im = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB").resize((224, 224), Image.BICUBIC)
    a = (np.asarray(im, dtype=np.float32) / 255.0 - 0.5) / 0.5
    v = s["session"].run(None, {"pixel_values": a.transpose(2, 0, 1)[None]})[1][0]
    v = v / np.linalg.norm(v)
    z = SCALE * (s["vectors"] @ v)
    p = np.exp(z - z.max())
    p /= p.sum()
    order = np.argsort(-p)[:k]
    return [(s["labels"][i], float(p[i])) for i in order]


def best_name(suggestions):
    """The name to save for a photo: the top suggestion if the model is sure enough, otherwise 'Unidentified item'."""
    return suggestions[0][0] if suggestions and suggestions[0][1] >= SURE else UNKNOWN
