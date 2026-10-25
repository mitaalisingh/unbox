"""Turns the list of item names (naming_data/household_items.txt) into numbers, once, so the app never needs the
text half of the AI model at run time.  Run on your own computer whenever the list changes:

    python tools/build_labels.py <text_model.onnx> <remap.npy>

Writes naming_data/labels.json and naming_data/labels.npy (one 768-number row per name, saved as float16).
Needs: numpy, onnxruntime, sentencepiece, huggingface_hub.
"""
import json
import os
import sys

import numpy as np
import onnxruntime as ort
import sentencepiece as spm
from huggingface_hub import hf_hub_download

REPO = "onnx-community/siglip2-base-patch16-224-ONNX"
HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "naming_data")

model_path, remap_path = sys.argv[1], sys.argv[2]
labels = []
for line in open(os.path.join(HERE, "household_items.txt")):
    line = line.strip()
    if line and not line.startswith("#") and line not in labels:
        labels.append(line)
sp = spm.SentencePieceProcessor(model_file=hf_hub_download(REPO, "tokenizer.model"))
remap = np.load(remap_path)
sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
rows = []
for name in labels:
    ids = (sp.encode(f"this is a photo of a {name}.".lower()) + [1] + [0] * 64)[:64]
    v = sess.run(None, {"input_ids": remap[np.array([ids], dtype=np.int64)]})[1][0]
    rows.append(v / np.linalg.norm(v))
np.save(os.path.join(HERE, "labels.npy"), np.array(rows, np.float16))
json.dump(labels, open(os.path.join(HERE, "labels.json"), "w"), indent=0)
print(f"{len(labels)} names written")
