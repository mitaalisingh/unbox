"""Search: plain word matching plus a small local model so "cup" finds "travel mug".

The model (fastembed, runs on CPU, no API key) is loaded lazily. If it isn't
installed, search quietly falls back to word and typo matching only.
"""
import difflib
import re

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
SEMANTIC_MIN = 0.67      # absolute cosine floor
SEMANTIC_BAND = 0.08     # stay within this of the best semantic hit

_model = None
_model_failed = False


def _get_model():
    global _model, _model_failed
    if _model is None and not _model_failed:
        try:
            from fastembed import TextEmbedding
            _model = TextEmbedding(MODEL_NAME)
        except Exception:
            _model_failed = True
    return _model


def embed_one(text):
    model = _get_model()
    if model is None or not text.strip():
        return None
    vec = np.asarray(next(iter(model.embed([text]))), dtype=np.float32)
    vec /= (np.linalg.norm(vec) or 1.0)
    return vec.tobytes()


def _tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def _stem(w):
    for suf in ("ies", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def _lexical(query, text):
    q_tokens = [_stem(t) for t in _tokens(query)]
    t_tokens = [_stem(t) for t in _tokens(text)]
    if not q_tokens or not t_tokens:
        return 0.0
    total = 0.0
    for q in q_tokens:
        if q in t_tokens:
            total += 1.0
        elif any(t.startswith(q) for t in t_tokens) and len(q) >= 3:
            total += 0.8
        else:
            best = max((difflib.SequenceMatcher(None, q, t).ratio() for t in t_tokens), default=0)
            if best >= 0.8 and len(q) >= 4:
                total += 0.6 * best
    return total / len(q_tokens)


def rank(query, rows, text_of):
    """Return [(row, score)] best first. rows need an 'emb' column."""
    scored = {}
    for r in rows:
        lex = _lexical(query, text_of(r))
        if lex > 0:
            scored[r["id"]] = [r, 1.0 + lex]

    model = _get_model()
    if model is not None:
        q_vec = np.asarray(next(iter(model.query_embed([query]))), dtype=np.float32)
        q_vec /= (np.linalg.norm(q_vec) or 1.0)
        sims = []
        for r in rows:
            if r["emb"]:
                sims.append((r, float(np.dot(q_vec, np.frombuffer(r["emb"], dtype=np.float32)))))
        if sims:
            best = max(s for _, s in sims)
            floor = max(SEMANTIC_MIN, best - SEMANTIC_BAND)
            for r, s in sims:
                if s >= floor:
                    if r["id"] in scored:
                        scored[r["id"]][1] += s
                    else:
                        scored[r["id"]] = [r, s]
    return sorted(((r, s) for r, s in scored.values()), key=lambda x: -x[1])


def _as_list(vec):
    v = np.asarray(vec, dtype=np.float32)
    v = v / (np.linalg.norm(v) or 1.0)
    return [round(float(x), 6) for x in v]


def embed_doc(text):
    """The stored form of an item: a list of 384 numbers, or None if the model isn't available."""
    model = _get_model()
    if model is None or not text.strip():
        return None
    return _as_list(next(iter(model.embed([text]))))


def embed_query(text):
    """The form used for what someone types in the search box."""
    model = _get_model()
    if model is None or not text.strip():
        return None
    return _as_list(next(iter(model.query_embed([text]))))


lexical_score = _lexical                     # word, plural and typo matching, 0..1
