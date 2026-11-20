#!/usr/bin/env python3
"""Select legal cuts on the global millisecond timeline; never edit text or speed."""
import argparse
import json
import math
from pathlib import Path

PENALTY = {"scene": 0, "chapter": 0, "sentence": 1, "action": 1, "page": 1, "hold": 3}


def plan(timeline):
    total = timeline["duration_ms"]
    limit = timeline.get("max_seconds", 30)
    if type(total) is not int or total < 1 or type(limit) is not int or not 4 <= limit <= 30:
        raise ValueError("总时长或单段上限不合法")
    end = limit * 1000
    protected = timeline.get("protected", [])
    for r in protected:
        a, b = r["from_ms"], r["to_ms"]
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= total:
            raise ValueError("受保护时间范围错误")
        if b-a > end:
            raise ValueError("自然时长超出单段上限，请处理完整语句或动作：" + r["id"])
    options = {0: {"kind": "scene", "note": "开场"}, total: {"kind": "scene", "note": "片尾落定"}}
    for item in timeline["cuts"]:
        t, k = item["at_ms"], item["kind"]
        if type(t) is not int or not 0 < t <= total or k not in PENALTY or not item["note"].strip():
            raise ValueError("候选剪点须有合法位置、类型及接法")
        if t == total or any(r["from_ms"] < t < r["to_ms"] for r in protected):
            continue
        if t not in options or PENALTY[k] < PENALTY[options[t]["kind"]]:
            options[t] = item
    ordered = sorted(options)
    best = {0: ((0, 0, 0), None)}
    for i, stop in enumerate(ordered[1:], 1):
        for j in range(i-1, -1, -1):
            start = ordered[j]
            span = stop-start
            if span > end:
                break
            if start not in best:
                continue
            count, cut_cost, spread = best[start][0]
            # For equal segment count, prefer semantic cuts, then a balanced allocation.
            score = (count+1, cut_cost+PENALTY[options[stop]["kind"]], spread+span*span)
            if stop not in best or score < best[stop][0]:
                best[stop] = (score, start)
    if total not in best:
        raise ValueError("安全剪点不足。请补一个落音、动作结束或页面停留点，再规划。")
    segments = []
    stop = total
    while stop:
        start = best[stop][1]
        keep = stop-start
        segments.append(dict(global_from_ms=start, global_to_ms=stop, keep_ms=keep,
                             request_seconds=max(4, math.ceil(keep/1000)),
                             enter=options[start]["note"], leave=options[stop]["note"]))
        stop = start
    return list(reversed(segments))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timeline")
    args = parser.parse_args()
    try:
        value = plan(json.loads(Path(args.timeline).read_text()))
        print(json.dumps(value, ensure_ascii=False, indent=2))
    except (KeyError, ValueError, OSError, TypeError) as err:
        parser.exit(2, "无法分段：" + str(err) + "\n")
