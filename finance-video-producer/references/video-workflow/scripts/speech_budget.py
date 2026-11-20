"""Estimate Mandarin speaking time inside a given window, including ordinary pauses."""
import argparse
import math
import unicodedata

NORMAL = 250
FAST = 270
SLOW = 240


def spoken_count(text):
    count = 0
    for char in text:
        if unicodedata.name(char, "").startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")) or char == "〇":
            count += 1
        elif not (char.isspace() or char in "，。！？；：、…—·“”‘’「」『』,.!?;:《》"):
            return None
    return count


def timing_text(n, window, measured=None):
    seconds = measured if measured is not None else n * 60 / NORMAL
    basis = "实读" if measured is not None else "估读"
    if seconds <= window:
        result = "合适"
    elif measured is None and n * 60 / FAST <= window:
        result = "偏紧，建议试读"
    else:
        result = "需调整"
    count_text = f"{n} 个口播字" if n is not None else "字数待核"
    return f"{count_text}，发声窗口 {window:g} 秒，{basis} {seconds:.1f} 秒，{result}"


def is_mandarin(lang):
    value = lang.strip().lower().replace("_", "-")
    return value in {"zh", "cmn", "中文", "汉语", "普通话", "mandarin", "国语"} or value.startswith(
        ("zh-cn", "zh-hans", "zh-tw", "zh-hant", "cmn-"))


def review(story, board):
    lines = {line["id"]: line for line in story["lines"]}
    if not lines:
        return ["全片 0 个口播字，时长按动作、产品展示和片尾安排。"]
    notes, duration, characters, missing = [], 0, 0, 0
    for part in board["segments"]:
        for cue in part["cues"]:
            line = lines[cue["id"]]
            window = (cue["to_ms"] - cue["from_ms"]) / 1000
            duration += window
            mandarin = is_mandarin(line["lang"])
            n = spoken_count(line.get("spoken_zh", line["text"])) if mandarin else None
            read_ms = line.get("read_ms")
            measured = read_ms / 1000 if type(read_ms) is int and read_ms > 0 else None
            if n is None:
                missing += 1
            else:
                characters += n
            detail = (timing_text(n, window, measured) if n is not None or measured is not None
                      else f"发声窗口 {window:g} 秒，待补口读稿或实读时长")
            notes.append(f'- {part["id"]} / {line["id"]} / {line["speaker"]}（段内 {cue["from_ms"]/1000:g}–{cue["to_ms"]/1000:g} 秒）　{detail}')
    scope = f"已数 {characters} 字，另有 {missing} 句字数待核" if missing else f"完整口播稿 {characters} 字"
    return [f'成片 {story["duration_ms"]/1000:g} 秒，合计发声窗口 {duration:g} 秒；{scope}。', *notes]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seconds", type=float, help="整片中实际可用于说话的秒数")
    parser.add_argument("spoken_zh", nargs="?", help="已去掉舞台说明并展开数字、缩写的口读稿")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error("可说时长须为非负的有限秒数")
    print(f"可说 {args.seconds:g} 秒，建议约 {round(args.seconds * NORMAL / 60)} 字，参考区间约 {round(args.seconds * SLOW / 60)}–{round(args.seconds * FAST / 60)} 字。")
    if args.spoken_zh is not None:
        n = spoken_count(args.spoken_zh)
        if n is None:
            parser.error("请把数字、符号或缩写展开为实际口读汉字，移出角色名和表演说明。")
        print(timing_text(n, args.seconds))
