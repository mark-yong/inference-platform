#!/usr/bin/env python3
"""RAG query embedding sidecar (CPU, torch).

OpenAI-compatible /v1/embeddings on :8017. Query-latency-oriented: short
strings, one lane, always on. Weights mounted read-only at /model.

Companion lanes (two-lane embeddings design; see design-decisions/001-two-lane-embeddings.md):
  - GPU ingest lane: vLLM /v1/embeddings on :8016 (batch ingestion only)
"""
import os, json, time
import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

MODEL_DIR = os.environ.get("MODEL_DIR", "/model")
# weights are stored as an HF hub cache dir (snapshots/<hash>/...); resolve the snapshot
_snap = os.path.join(MODEL_DIR, "snapshots")
if os.path.isdir(_snap):
    MODEL_DIR = os.path.join(_snap, os.listdir(_snap)[0])
PORT = int(os.environ.get("PORT", "8017"))
THREADS = int(os.environ.get("TORCH_THREADS", "4"))
torch.set_num_threads(THREADS)

_t0 = time.time()
# The vllm image lacks sentencepiece/tiktoken (no fast-tokenizer conversion path), but the
# HF weights bundle tokenizer.json; load it directly via the tokenizers lib (bundled with
# transformers as a hard dep).
from tokenizers import Tokenizer as _Tok
tok = _Tok.from_file(os.path.join(MODEL_DIR, "tokenizer.json"))
model = AutoModel.from_pretrained(MODEL_DIR, dtype=torch.float32).eval()
print(f"loaded in {time.time()-_t0:.1f}s", flush=True)

_last_lat = 0.0

def embed(texts, is_query=True):
    """vLLM-equivalent: RAW text, LEFT-pad, LAST-token pooling (position -1).
    Verified: cosine 0.99994 vs vLLM GPU for identical input. No instruct prefix:
    vLLM's /v1/embeddings does not add one."""
    global _last_lat
    t = time.time()
    with torch.no_grad():
        encs = [tok.encode(t_) for t_ in texts]
        maxlen = max(len(e.ids) for e in encs)
        pad = tok.padding["pad_id"] if tok.padding else tok.token_to_id("<|endoftext|>")
        ids = np.full((len(encs), maxlen), pad, dtype=np.int64)
        mask = np.zeros((len(encs), maxlen), dtype=np.int64)
        # LEFT-pad so the last real token is always at position -1 (last-token pooling)
        for i, e in enumerate(encs):
            n = len(e.ids)
            ids[i, maxlen-n:] = e.ids
            mask[i, maxlen-n:] = 1
        out = model(input_ids=torch.from_numpy(ids),
                    attention_mask=torch.from_numpy(mask)).last_hidden_state
        emb = out[:, -1, :]                     # last position = EOS of each sequence
        emb = torch.nn.functional.normalize(emb, dim=-1)
    _last_lat = (time.time() - t) * 1000
    return emb.numpy().astype(np.float32)

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/v1/models":
            self._json(200, {"object":"list","data":[{"id":"qwen3-emb-0.6b-cpu","object":"model"}]})
        elif self.path == "/health":
            self._json(200, {"status":"ok","load_seconds":round(time.time()-_t0,1),"last_embed_ms":round(_last_lat,1)})
        else:
            self._json(404, {"error":"not found"})
    def do_POST(self):
        if self.path != "/v1/embeddings":
            return self._json(404, {"error":"not found"})
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        texts = req.get("input") or []
        if isinstance(texts, str): texts = [texts]
        vecs = embed(texts)
        self._json(200, {"object":"list",
            "data":[{"object":"embedding","index":i,"embedding":v.tolist()} for i,v in enumerate(vecs)],
            "model":"qwen3-emb-0.6b-cpu",
            "usage":{"prompt_tokens":0,"latency_ms":round(_last_lat,1)}})

ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
