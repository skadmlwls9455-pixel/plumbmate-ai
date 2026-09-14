"""Synthetic 500-turn context benchmark; no API, game data, or pricing claims."""

import json
import statistics
import time

from plumbmate.budget import canonical_json, clamp_request
from plumbmate.cli import make_demo_request
from plumbmate.dialogue import dialogue_context


def main():
    elapsed, before, after = [], [], []
    for i in range(500):
        request = make_demo_request(("함께 산책할까? 🌻 " * (1 + i % 20))[:1000])
        request["memory_summary"] = ""
        start = time.perf_counter()
        packed = clamp_request(request)
        context = dialogue_context(packed)
        before.append(len(canonical_json(packed).encode("utf-8")))
        after.append(len(canonical_json(context).encode("utf-8")))
        elapsed.append((time.perf_counter() - start) * 1000)
    print(json.dumps({"turns": 500, "context_bytes_before_mean": statistics.mean(before),
                      "context_bytes_after_mean": statistics.mean(after), "max_context_bytes": max(before),
                      "pack_and_project_ms_median": statistics.median(elapsed),
                      "pack_and_project_ms_p95": sorted(elapsed)[474],
                      "note": "Synthetic UTF-8 bytes, not tokens or billed savings; no accumulated history"}, indent=2))


if __name__ == "__main__":
    main()
