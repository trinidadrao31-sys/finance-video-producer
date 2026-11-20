"""Replace one aligned audio window while copying the original video stream."""
import argparse
from array import array
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run(command, audio=None):
    result = subprocess.run(command, input=audio, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", str(path)]))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def video_hash(path):
    output = run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
                  "-c", "copy", "-f", "streamhash", "-hash", "sha256", "-"])
    return output.decode().strip().split("SHA256=", 1)[1]


def streams(media, kind):
    return [stream for stream in media["streams"] if stream["codec_type"] == kind]


def duration(media):
    return round(float(media["format"]["duration"]) * 1000)


def sample_at(ms, rate):
    if type(ms) is not int or ms < 0:
        raise ValueError("音频范围须为非负整数毫秒")
    return round(ms * rate / 1000)


def pcm(path, rate, channels, start=None, end=None):
    filters = f"aresample={rate}"
    if start is not None:
        filters += f",atrim=start_sample={start}:end_sample={end}"
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
               "-af", filters, "-ac", str(channels), "-f", "f32le", "-"])
    return array("f", raw)


def fit(patch, target, rate, channels):
    if len(patch) == target:
        return patch
    tempo = len(patch) / target
    if not 0.5 <= tempo <= 2:
        raise ValueError("补丁与原窗口时长差异过大，请重新定位或补录")
    fmt = ["-f", "f32le", "-ar", str(rate), "-ac", str(channels)]
    raw = run(["ffmpeg", "-v", "error", *fmt, "-i", "-", "-af", f"atempo={tempo:.9f}",
               *fmt, "-"], patch.tobytes())
    fitted = array("f", raw)
    if len(fitted) < target * 0.9:
        raise ValueError("变速后补丁尾部不足，请选取更完整的短语")
    fitted = fitted[:target]
    fitted.extend([0.0] * (target - len(fitted)))
    return fitted


def splice(req, source, replacement, output):
    if req.get("audio_component") != "mixed":
        raise ValueError("此脚本处理单条混合音轨。独立人声请使用已核实的分轨回拼工具")
    if output.suffix.lower() not in {".mp4", ".mov"} or output.exists():
        raise ValueError("用新的 MP4 或 MOV 产物路径，避免覆盖原片或既有候选")
    source_digest, patch_digest = file_hash(source), file_hash(replacement)
    patch_spec = req["replacement"]
    if source_digest != req["source"]["sha256"] or patch_digest != patch_spec["sha256"]:
        raise ValueError("实际输入与领取请求的文件摘要不一致")
    media, patch_media = probe(source), probe(replacement)
    picture, original_audio = streams(media, "video"), streams(media, "audio")
    candidate_picture, candidate_audio = streams(patch_media, "video"), streams(patch_media, "audio")
    if len(picture) != 1 or len(original_audio) != 1 or len(candidate_audio) != 1:
        raise ValueError("需要单一画面流、单一原声音轨和单一补丁音轨")
    if bool(candidate_picture) != (patch_spec["media_type"] == "video"):
        raise ValueError("实际替换素材类型与登记不一致")
    timed_streams = [*picture, *original_audio, *(candidate_picture + candidate_audio if candidate_picture else [])]
    if any(abs(float(stream.get("start_time", 0))) > 0.002 for stream in timed_streams):
        raise ValueError("音画起点存在偏移，请先明确时间轴并用支持该偏移的编辑器回拼")
    rate, channels = int(original_audio[0]["sample_rate"]), original_audio[0]["channels"]
    if channels not in (1, 2):
        raise ValueError("原声需为单声道或双声道；多声道请用独立剪辑工具")

    start, end = (sample_at(req[k], rate) for k in ("from_ms", "to_ms"))
    patch_start, patch_end = (sample_at(patch_spec[k], rate) for k in ("from_ms", "to_ms"))
    source_ms = duration(media)
    audio_duration = original_audio[0].get("duration", media["format"]["duration"])
    audible_samples = round(min(float(audio_duration), float(media["format"]["duration"])) * rate)
    original = pcm(source, rate, channels)[:audible_samples * channels]
    if not 0 <= start < end <= len(original) // channels or patch_end <= patch_start:
        raise ValueError("回拼窗口或补丁范围超出实际音轨")
    patch = pcm(replacement, rate, channels, patch_start, patch_end)
    if not patch or len(patch) != (patch_end - patch_start) * channels:
        raise ValueError("补丁未覆盖登记的实际取样范围")
    patch = fit(patch, (end - start) * channels, rate, channels)

    fade = min(round(rate * 0.010), (end - start) // 2)
    if fade:
        for offset in range(fade):
            weight = (offset + 1) / (fade + 1)
            for i in (offset, end - start - offset - 1):
                for channel in range(channels):
                    local = i * channels + channel
                    patch[local] = patch[local] * weight + original[start * channels + local] * (1 - weight)
    original[start * channels:end * channels] = patch
    source_video = video_hash(source)
    with tempfile.TemporaryDirectory(prefix=".splice-", dir=output.parent) as directory:
        candidate = Path(directory) / output.name
        run(["ffmpeg", "-v", "error", "-i", str(source), "-f", "f32le", "-ar", str(rate),
             "-ac", str(channels), "-i", "-", "-map", "0", "-map", "-0:a:0", "-map", "1:a:0",
             "-c", "copy", "-c:a:0", "aac", "-b:a:0", "192k", str(candidate)], original.tobytes())
        output_video = video_hash(candidate)
        output_ms = duration(probe(candidate))
        if output_video != source_video or abs(output_ms - source_ms) > 50:
            raise ValueError("画面流或原片时长校验未通过")
        output_digest = file_hash(candidate)
        os.link(candidate, output)
    return dict(source_sha256=source_digest, replacement_sha256=patch_digest, output_sha256=output_digest,
                source_video_sha256=source_video, output_video_sha256=output_video,
                source_duration_ms=source_ms, duration_ms=output_ms,
                from_ms=req["from_ms"], to_ms=req["to_ms"],
                replacement_from_ms=patch_spec["from_ms"], replacement_to_ms=patch_spec["to_ms"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claim", type=Path, help="claim-repair 返回的 JSON 文件")
    parser.add_argument("source_file", type=Path, help="确认单中的原视频本地文件")
    parser.add_argument("replacement_file", type=Path, help="确认单或候选回执中的补丁本地文件")
    parser.add_argument("output", type=Path, help="新候选 MP4 或 MOV 文件")
    args = parser.parse_args()
    req = json.loads(args.claim.read_text(encoding="utf-8"))["request"]
    if req.get("pronunciation_route") not in {"seedance", "import_audio"}:
        raise ValueError("仅处理已领取的发音返修")
    proof = splice(req, args.source_file, args.replacement_file, args.output)
    print(json.dumps(proof, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit("回拼未完成：" + str(exc))
