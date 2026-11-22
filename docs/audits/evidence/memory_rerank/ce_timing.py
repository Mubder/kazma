"""Time both cross-encoders on CPU: 20 (question, memory) pairs of benchmark-like lengths."""
import json, os, statistics, sys, time
import torch
from sentence_transformers import CrossEncoder

MODELS = {
    "mmarco": ("cross-encoder/mmarco-mMiniLMv2-L12-H384-v1", "1427fd652930e4ba29e8149678df786c240d8825"),
    "bge": ("BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"),
}
which = sys.argv[1]
threads = int(sys.argv[2])
maxlen = int(sys.argv[3]) if len(sys.argv) > 3 else 512
torch.set_num_threads(threads)
name, rev = MODELS[which]
t0 = time.perf_counter()
ce = CrossEncoder(name, revision=rev, local_files_only=True, max_length=maxlen, trust_remote_code=False)
load = time.perf_counter() - t0
q = "What did my sister say about the trip to Lisbon last spring?"
short = "User: We went to Lisbon in April with my sister. Assistant: That sounds lovely, how was the weather?"
mid = ("User: " + "We talked about the plans for the summer and the budget for the new kitchen. " * 6 +
       "Assistant: " + "Here is a breakdown of the costs you mentioned and some ideas. " * 6)
long = ("User: " + "My sister told me a long story about her work and the trip. " * 20 +
        "Assistant: " + "That is a lot to think about; here are some suggestions for you. " * 25)
mixes = {"short": [short] * 20, "mixed": [short] * 8 + [mid] * 8 + [long] * 4, "long": [long] * 20}
out = {"model": which, "threads": threads, "max_length": maxlen, "load_s": round(load, 2)}
for label, texts in mixes.items():
    pairs = [(q, t) for t in texts]
    ce.predict(pairs[:4], batch_size=32, show_progress_bar=False)
    times = []
    for _ in range(15):
        s = time.perf_counter()
        ce.predict(pairs, batch_size=32, show_progress_bar=False)
        times.append((time.perf_counter() - s) * 1000)
    times.sort()
    out[label] = {"p50_ms": round(times[len(times)//2], 1), "p95_ms": round(times[-1], 1)}
    # per-pair one at a time (anytime granularity)
    s = time.perf_counter()
    for p in pairs[:8]:
        ce.predict([p], show_progress_bar=False)
    out[label]["one_pair_ms"] = round((time.perf_counter() - s) * 1000 / 8, 1)
print(json.dumps(out))
