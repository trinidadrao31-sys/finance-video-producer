#!/usr/bin/env python3
"""Single-project, transactional demo for an agent. Uses only Python's standard library."""
import hashlib
import json
import math
import re
from pathlib import Path

DIRECTORS = {
    "scene": "video-scene", "presenter": "video-presenter", "explainer": "video-explainer",
    "campaign": "video-campaign", "showcase": "video-showcase",
}
TYPES = set(DIRECTORS)
MODES = {"dialogue": "现场对白", "address": "对镜口播", "vo": "画外旁白",
         "device": "设备播报", "chorus": "合声"}
STEPS = ("brief", "story", "review", "board", "prompts")
SEAMS = {"opening", "cut", "match", "first_frame", "dissolve_inside"}
SHOOTING_PROMPT = "shooting"
REFERENCE_PURPOSES = {
    "role": "保持人物的外貌和服装", "product": "保持产品的外形与可见细节",
    "ui": "保持界面布局和可见细节", "logo": "保持标识的形状和颜色",
    "scene": "保持场景布局和陈设", "audio": "参考声音特征", "video": "参考画面和动作",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def required(obj, *keys):
    for key in keys:
        require(isinstance(obj.get(key), str) and obj[key].strip(), "缺少 " + key)


def number(x):
    return type(x) is int


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def index(rows):
    require(isinstance(rows, list), "清单必须为数组")
    out = {}
    for row in rows:
        required(row, "id")
        require(row["id"] not in out, "重复 ID " + row["id"])
        out[row["id"]] = row
    return out


def drop_after(s, step):
    # Keep reviewed independent takes for an identical rebuilt request after a copy edit.
    if s.get("prompts"):
        bases = index(compile_prompts(s))
        s["manual_prompts"] = {p["id"]: {"base": bases[p["id"]], "edited": p["prompt"]}
                               for p in s["prompts"] if p["prompt"] != bases[p["id"]]["prompt"]}
        cache = s.setdefault("take_cache", {})
        for part in s["prompts"]:
            clip = s.get("clips", {}).get(part["id"])
            if clip and clip.get("accepted"):
                cache[digest(part)] = clip
    for k in STEPS[STEPS.index(step) + 1:]:
        s.pop(k, None)
    s["approved"] = {k: v for k, v in s.get("approved", {}).items()
                     if k in STEPS[:STEPS.index(step)]}
    s.update(authorized=None, clips={}, unreviewed_clips={}, final=None, delivered=False)


def validate_brief(b):
    require(b["type"] in TYPES and type(b["people"]) is bool and type(b["speech"]) is bool, "片型或开关无效")
    require(b["product_mode"] in {"none", "illustrative", "exact"}, "产品模式无效")
    require(isinstance(b["missing"], list), "missing 必须为数组")
    copy_locks = b.get("exact_copy", [])
    require(isinstance(copy_locks, list), "exact_copy 须是客户逐字要求列表")
    targets = set()
    for lock in copy_locks:
        required(lock, "text")
        require("line_id" in lock or "caption_id" in lock, "锁定原句须绑定人声或屏显 ID")
        for channel in ("line_id", "caption_id"):
            if channel in lock:
                required(lock, channel)
                key = (channel, lock[channel])
                require(key not in targets, "同一文案 ID 重复锁定")
                targets.add(key)
        require("line_id" not in lock or b["speech"], "已锁定人声，brief.speech 应为 true")
    if "audio_policy" in b:
        require(b["audio_policy"] == "mute" and not b["speech"], "全片静音不能包含人声")
    ref_needs = b.get("ref_needs", [])
    require(isinstance(ref_needs, list) and all(isinstance(k, str) and k.strip() for k in ref_needs),
            "ref_needs 应为待制作/选定的图 ID 列表")
    for a in index(b["assets"]).values():
        required(a, "use", "uri", "label")
        require(a["use"] in {"product", "logo", "ui", "role", "scene", "audio", "video", "analysis"}, "素材用途无效")


def validate_story(s, story, for_approval):
    required(story, "synopsis")
    needs = story.get("ref_needs", [])
    require(isinstance(needs, list) and all(isinstance(k, str) and k.strip() for k in needs),
            "故事的参考图需求应使用图 ID 列表")
    require(number(story["duration_ms"]) and story["duration_ms"] > 0, "片长须为正整数毫秒")
    if "target_duration_ms" in s["brief"]:
        require(story["duration_ms"] == s["brief"]["target_duration_ms"], "分段之和需符合已确认总时长")
    lines = index(story["lines"])
    if not s["brief"]["speech"]:
        require(not lines, "无词片不能含台词")
    for line in lines.values():
        required(line, "speaker", "text", "lang")
        require(line["mode"] in MODES and "\n" not in line["text"], "声源或逐句格式无效")
    captions = index(story["captions"])
    for cap in captions.values():
        if "line_id" in cap:
            require(cap["line_id"] in lines and "text" not in cap, "共用文案只能引用现存 line_id")
        else:
            required(cap, "text")
            require("\n" not in cap["text"], "多行落版用多个 ID")
    for lock in s["brief"].get("exact_copy", []):
        if "line_id" in lock:
            line_id = lock["line_id"]
            require(lines.get(line_id, {}).get("text") == lock["text"], "人声与客户锁定原句不符：" + line_id)
        if "caption_id" in lock:
            caption_id = lock["caption_id"]
            cap = captions.get(caption_id, {})
            text = lines[cap["line_id"]]["text"] if "line_id" in cap else cap.get("text")
            require(text == lock["text"], "屏显与客户锁定原句不符：" + caption_id)
            if "line_id" in lock:
                require(cap.get("line_id") == lock["line_id"], "相同的锁定音画文案应引用同一 line_id")
    if for_approval:
        b = s["brief"]
        require(not b["missing"], "请补齐：" + "、".join(b["missing"]))
        require(bool(lines) == b["speech"], "有词片须先补全逐字原稿")
        if b["speech"]:
            require(story.get("speech_complete") is True, "当前只有部分台词，需确认所有台词已齐")
        if b["product_mode"] == "exact":
            require(any(a["use"] in {"product", "ui"} for a in b["assets"]), "要精确复现，先补产品图/界面原图")
        require(not story.get("unresolved"), "故事含未定项")
        for row in [*lines.values(), *story["captions"]]:
            require(not re.search(r"\[待核[^\]]*\]|【待核[^】]*】", row.get("text", "")), "文案含待核项")


def ref_needs(s):
    return set(s["brief"].get("ref_needs", [])) | set(s.get("story", {}).get("ref_needs", []))


def ref_sources(s, item):
    originals = index(s["brief"]["assets"])
    sources = [originals[k] for k in item.get("source_ids", [])]
    if item.get("base_candidate"):
        old = s["ref_candidates"][item["base_candidate"]]
        require(old["ref_id"] == item["id"], "编辑底图不属于这个参考项")
        sources.append(dict(uri=old.get("snapshot_uri", old["uri"]), sha256=old["sha256"]))
    return sources


def ref_request(s, rid):
    require(approved(s, "story") and s.get("review", {}).get("verdict") == "pass", "先确认故事并通过审查")
    require(rid in s.get("ref_plan", {}), "请先制定该参考图的计划")
    item = s["ref_plan"][rid]
    words = index(s["story"]["lines"])
    titles = index(s["story"]["captions"])
    wording = []
    for field, key in item.get("copy_refs", []):
        row = (words if field == "lines" else titles)[key]
        wording.append(words[row["line_id"]]["text"] if "line_id" in row else row["text"])
    package = dict(item=item, sources=ref_sources(s, item), wording=wording,
                   story_visuals=[s["story"]["synopsis"], s["story"].get("beats", [])])
    prompt = item.get("prompt", "")
    if wording:
        prompt += "\n只在约定位置原样呈现以下原文：" + " / ".join(wording)
    if item["use"] == "audio":
        return dict(ref_id=rid, method=item["method"], speaker=item["speaker"], voice=item["voice"],
                    sample_text=item.get("sample_text", ""), provider=item["provider"],
                    duration_s=item.get("duration_s", 5), sources=package["sources"],
                    spec_hash=digest({"sample": {k: v for k, v in item.items() if k not in {"segments", "purpose", "label"}},
                                      "sources": package["sources"]}))
    return dict(ref_id=rid, method=item["method"], ratio=item["ratio"], prompt=prompt,
                sources=package["sources"], spec_hash=digest(package))


def pending_refs(s):
    wanted = ref_needs(s) | set(s.get("ref_plan", {}))
    missing = []
    for rid in sorted(wanted):
        chosen = s.get("ref_picks", {}).get(rid)
        if not chosen or rid not in s.get("ref_plan", {}):
            missing.append(rid)
        elif s["ref_candidates"][chosen]["spec_hash"] != ref_request(s, rid)["spec_hash"]:
            missing.append(rid)
    return missing


def project_assets(s):
    assets = index(s["brief"]["assets"])
    for rid, cid in s.get("ref_picks", {}).items():
        item = s["ref_plan"][rid]
        candidate = s["ref_candidates"][cid]
        assets[rid] = dict(id=rid, use=item["use"], label=item["label"], uri=candidate.get("snapshot_uri", candidate["uri"]),
                           sha256=candidate["sha256"], selected_candidate=cid, reference_purpose=item["purpose"])
        if item["use"] == "audio":
            assets[rid].update(speaker=item["speaker"], provider=item["provider"], voice=item["voice"],
                               duration_s=candidate["duration_s"])
    return assets


def clear_board_for_refs(s):
    if "board" in s:
        s["previous_board"] = s["board"]
        drop_after(s, "board")
        s.pop("board", None)


def verify_ref_image(uri, sha):
    require(re.fullmatch(r"[0-9a-f]{64}", sha), "请记录实际图像字节的 SHA-256")
    if uri.startswith("asset://"):
        return  # The host verified the bytes and supplies an immutable asset version.
    require(not uri.startswith("https://"), "普通图片网址请保存实际 PNG/JPEG 缓存并登记 snapshot_uri，或使用版本化资产")
    path = Path(uri)
    require(path.is_file(), "未找到参考图文件")
    raw = path.read_bytes()
    require(raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")), "本地交接请使用 PNG/JPEG")
    require(hashlib.sha256(raw).hexdigest() == sha, "图像版本与实际内容不符")


def verify_candidate(s, candidate):
    uri = candidate.get("snapshot_uri", candidate["uri"])
    sha = candidate["sha256"]
    item = s["ref_plan"][candidate["ref_id"]]
    if item["use"] != "audio":
        return verify_ref_image(uri, sha)
    verify_voice_file(uri, sha, candidate.get("duration_s"))


def verify_voice_file(uri, sha, length):
    require(type(length) in (int, float) and math.isfinite(length) and length > 0, "登记试听文件的实际秒数")
    require(re.fullmatch(r"[0-9a-f]{64}", sha), "样音需要实际文件摘要")
    if uri.startswith("asset://"):
        return
    path = Path(uri)
    require(path.is_file(), "样音需要稳定的本地快照或版本化资产")
    raw = path.read_bytes()
    wav = raw.startswith(b"RIFF") and raw[8:12] == b"WAVE"
    mp3 = raw.startswith(b"ID3") or (len(raw) > 1 and raw[0] == 255 and raw[1] & 224 == 224)
    require(wav or mp3 or raw.startswith((b"fLaC", b"OggS")) or raw[4:8] == b"ftyp", "无法识别该音频容器")
    require(hashlib.sha256(raw).hexdigest() == sha, "样音文件与所选版本不一致")
    if wav and raw[20:22] == bytes((1, 0)):
        import wave
        with wave.open(str(path), "rb") as stream:
            actual = stream.getnframes() / stream.getframerate()
            require(abs(actual - length) <= 0.05, "登记秒数与 WAV 实际时长不符")


def validate_ref_plan(s, rows):
    plan = index(rows)
    require(ref_needs(s) <= set(plan), "计划遗漏了已有参考图需求")
    originals = index(s["brief"]["assets"])
    require(not set(plan).intersection(originals), "参考项与输入素材 ID 冲突")
    for item in plan.values():
        required(item, "label", "purpose")
        if item["use"] == "audio":
            required(item, "speaker", "voice", "provider")
            sources = ref_sources(s, item)
            require(item["method"] in {"generate", "provided"}, "样音请选择生成或复用")
            if item["method"] == "generate":
                required(item, "sample_text")
                require(not sources, "生成新样音请指定声线；复用已有样音请选择 provided")
            else:
                require(len(sources) == 1 and sources[0].get("use") == "audio", "选用已有样音须绑定一份音频")
            length = item.get("duration_s", 5)
            require(type(length) in (int, float) and math.isfinite(length) and length > 0, "样音时长无效")
            continue
        required(item, "ratio")
        require(item["use"] in {"role", "scene", "product", "ui", "logo"}, "参考图用途不支持")
        method = item["method"]
        require(method in {"generate", "edit", "provided"}, "请选择新生、参考编辑或直接使用")
        sources = ref_sources(s, item)
        require(all(a.get("use") not in {"audio", "analysis"} for a in sources), "未授权作图的素材不可作输入")
        if method == "provided":
            require(len(sources) == 1 and not item.get("base_candidate"), "直接使用须指定一张原图")
        else:
            required(item, "prompt")
            require(bool(sources) == (method == "edit"), "有底图应走编辑，无底图走生成")
        if s["brief"]["product_mode"] == "exact" and item["use"] in {"product", "ui"}:
            require(any(a.get("use") in {"product", "ui"} for a in sources),
                    "精确产品须从客户产品/界面原图取样；示意设计可按用户要求另行创建")
        for field, key in item.get("copy_refs", []):
            require(field in {"lines", "captions"} and key in index(s["story"][field]), "图中文字引用无效")
        scope = item.get("segments", [])
        require(isinstance(scope, list) and all(isinstance(k, str) and k for k in scope), "段号范围无效")
    voices = [a["speaker"] for a in plan.values() if a["use"] == "audio"]
    require(len(voices) == len(set(voices)), "每位说话人只建一个样音参考项")
    return plan


def audio_route(s, part):
    if s["brief"].get("audio_policy") == "mute":
        require(part.get("audio_route", "mute") == "mute", "全片已指定静音")
        return "mute"
    return part.get("audio_route", "native_dialogue" if part["cues"] else "ambience")


def validate_board(s, board, planned=False):
    b, story = s["brief"], s["story"]
    if not planned:
        require(not pending_refs(s), "参考图尚未选定：" + "、".join(pending_refs(s)))
    lines, titles, assets = index(story["lines"]), index(story["captions"]), project_assets(s)
    if planned:
        assets.update(s.get("ref_plan", {}))
    parts = index(board["segments"])
    require(parts and board["ratio"] in {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}, "缺片段或画幅不支持")
    cap = board.get("max_seconds", 30)
    require(number(cap) and 1 <= cap <= 30, "单段上限须为 1–30 秒；开拍前还须核对实际工具能力")
    seen, shown, used = [], set(), set()
    total = 0
    preceding_refs = set()
    for i, p in enumerate(parts.values()):
        n = p["keep_ms"]
        require(number(n) and 0 < n <= cap * 1000, "有效段超长或为空")
        total += n
        required(p, "opening", "subjects", "ending")
        if i:
            required(p, "join")
        require(p["seam"] in SEAMS and (p["seam"] == "opening") == (i == 0), "首段/续段接法错误")
        require(len(set(p["asset_ids"])) == len(p["asset_ids"]), "重复绑定同一素材")
        for aid in p["asset_ids"]:
            require(aid in assets and assets[aid]["use"] != "analysis", "未知或仅供分析的素材")
            used.add(aid)
        route = audio_route(s, p)
        require(route in {"native_dialogue", "post_voiceover", "ambience", "mute"}, "声音路线无效")
        require(bool(p["cues"]) == (route in {"native_dialogue", "post_voiceover"}), "发声窗口与声音路线不一致")
        speakers = {lines[c["id"]]["speaker"] for c in p["cues"] if c["id"] in lines}
        on_camera = {lines[c["id"]]["speaker"] for c in p["cues"] if c["id"] in lines
                     and lines[c["id"]]["mode"] in {"dialogue", "address", "chorus"}}
        samples = [assets[k] for k in p["asset_ids"] if assets[k]["use"] == "audio" and assets[k].get("speaker")]
        names = [a["speaker"] for a in samples]
        require(len(names) == len(set(names)) and set(names) <= speakers, "本段每位实际发声者最多绑定一份自己的样音")
        if route == "native_dialogue":
            require(on_camera <= set(names), "缺少本段出声人物样音：" + "、".join(sorted(on_camera - set(names))))
        if route == "post_voiceover":
            require(not on_camera and not samples, "后期旁白仅用于画外声，本段视频不传人物样音")
        if p["seam"] == "first_frame":
            require(all(assets[k]["use"] == "audio" for k in p["asset_ids"]), "首帧模式请以已合成首帧承载参考，或改为独立参考生视频")
        inherited = set(p.get("inherited_ref_ids", []))
        require(not inherited or (p["seam"] == "first_frame" and inherited <= preceding_refs
                                   and inherited <= set(s.get("ref_plan" if planned else "ref_picks", {}))), "继承的参考须已选用且来自相邻上段")
        preceding_refs = set(p["asset_ids"]) | inherited
        last = 0
        for shot in p["shots"]:
            required(shot, "visual")
            t = shot["end_ms"]
            require(number(t) and last < t <= n, "镜头时间顺序错误")
            if "min_duration_ms" in shot:
                minimum = shot["min_duration_ms"]
                require(number(minimum) and 0 < minimum <= t - last, "镜头未留足指定时长：" + p["id"])
            last = t
        require(last == n, "镜头须覆盖有效区间")
        for key, entries in (("cues", lines), ("titles", titles)):
            prev = -1
            for cue in p[key]:
                a, z = cue["from_ms"], cue["to_ms"]
                require(number(a) and number(z) and 0 <= a < z <= n and cue["id"] in entries, "无效文案时间或 ID")
                if "min_duration_ms" in cue:
                    minimum = cue["min_duration_ms"]
                    require(number(minimum) and 0 < minimum <= z - a, "文案窗口短于所需时长：" + cue["id"])
                if key == "cues":
                    require(a >= prev, "串句或重叠对白；合声请用 chorus")
                    prev = z
                    seen.append(cue["id"])
                else:
                    shown.add(cue["id"])
    require(seen == list(lines), "母稿与分镜的台词顺序、覆盖或次数不符")
    require(shown == set(titles), "重要画面字未全部安排")
    require(total == story["duration_ms"], "有效区间之和不等于全片时长")
    for rid, item in s.get("ref_plan", {}).items():
        actual = {p["id"] for p in parts.values() if rid in p["asset_ids"] or rid in p.get("inherited_ref_ids", [])}
        scope = set(item.get("segments", []))
        require(actual and (not scope or actual == scope), "参考图未用于指定片段：" + rid)
    if b["product_mode"] == "exact":
        require(any(assets[a]["use"] in {"product", "ui"} for a in used), "精确产品图未绑定任何实际片段")


def sec(ms):
    return ("%0.3f" % (ms / 1000)).rstrip("0").rstrip(".") or "0"


def media_tags(refs):
    labels, counts = [], {}
    for a in refs:
        kind = {"audio": "音频", "video": "视频"}.get(a["use"], "图片")
        counts[kind] = counts.get(kind, 0) + 1
        labels.append(f"<{kind}{counts[kind]}>")
    return labels


def voice_row(cue):
    return "%s–%s秒｜%s｜%s｜%s｜%s" % (
        sec(cue["from_ms"]), sec(cue["to_ms"]), cue["speaker"], MODES[cue["mode"]], cue["lang"], cue["text"])


def verify_prompt_copy(p, prompt, lines, titles, include_speech):
    cursor = 0
    if include_speech:
        for cue in p["cues"]:
            words = lines[cue["id"]]["text"]
            pos = prompt.find(words, cursor)
            require(pos >= 0, "投模全文缺逐字台词或顺序不符：" + cue["id"])
            cursor = pos + len(words)
    for cue in p["titles"]:
        title = titles[cue["id"]]
        words = lines[title["line_id"]]["text"] if "line_id" in title else title["text"]
        require(title.get("render") == "post" or words in prompt, "投模全文缺画面字：" + cue["id"])


def compile_prompts(s, planned=False):
    board, story = s["board"], s["story"]
    assets, lines, titles = project_assets(s), index(story["lines"]), index(story["captions"])
    if planned:
        for rid, item in s.get("ref_plan", {}).items():
            assets[rid] = dict(item, reference_purpose=item["purpose"])
    shooting = board.get("prompt_format") == SHOOTING_PROMPT
    result = []
    for i, p in enumerate(board["segments"]):
        refs = [assets[k] for k in p["asset_ids"]]
        n = p["keep_ms"]
        minimum = p.get("execution", board.get("execution", {})).get("min_seconds", 4)
        request = max(minimum, math.ceil(n / 1000))
        frame = p["seam"] == "first_frame"
        labels = media_tags(refs)
        purposes = [a.get("reference_purpose", REFERENCE_PURPOSES[a["use"]] if shooting else a["use"]) for a in refs]
        if shooting:
            purposes = [purpose.rstrip("。；") for purpose in purposes]
        separator = "。\n" if shooting else "；"
        sources = separator.join(f"{tag}（{a['label']}）用于{purpose}" for tag, a, purpose in zip(labels, refs, purposes))
        voices = {a["speaker"]: tag for tag, a in zip(labels, refs) if a["use"] == "audio" and a.get("speaker")}
        route = audio_route(s, p)
        legacy_sections = not shooting and bool(p["cues"] or len(refs) > 1 or p.get("timing_required"))
        source = "以提交的上一段有效末帧为本段首帧。" if frame else (
            (sources + "。") if refs else ("" if shooting else "仅按以下文字生成。"))
        if frame and refs:
            source += ("\n" if shooting else "") + sources + "。"
        if planned and refs and not frame and not shooting:
            source = "拟绑定：" + sources + "。"
        inherited = [assets[k] for k in p.get("inherited_ref_ids", [])]
        if inherited:
            source += ("\n" if shooting else "") + "由首帧延续已选定的" + "、".join(a["label"] for a in inherited) + "。"
        t = []
        if voices:
            source += ("\n样音只锁定对应角色音色、咬字与共鸣。只念时间轴中本角色的正式原句。" if shooting
                       else " 样音只锁定对应角色音色、咬字与共鸣；只念时间轴中本角色的正式原句。")
        if legacy_sections:
            t.extend(["一、输入与零秒状态", source, p["opening"], "二、主体与目标", p["subjects"], "三、动作、声音和脚本"])
        for clause in (source, p["opening"], None if shooting else p["subjects"]):
            if clause and clause not in t:
                t.append(clause)
        timed = p.get("timing_required") or (
            not shooting and (len(p["shots"]) > 1 or p["cues"] or p["titles"] or frame))
        start = 0
        for shot in p["shots"]:
            phrase = shot["visual"]
            if timed:
                phrase = "%s–%s秒，%s" % (sec(start), sec(shot["end_ms"]), phrase)
            if phrase not in t:
                t.append(phrase)
            start = shot["end_ms"]
        if shooting and route in {"mute", "post_voiceover"}:
            t.append("全段静音，出镜人物不说话。")
        elif route == "mute":
            t.append("全段静音，交付时移除音轨或验证全程无声。")
        elif route == "post_voiceover":
            t.append("后期旁白时段留给产品或动作展示；视频静音底片，后期按母稿配音。")
        elif p.get("ambience"):
            t.append("声音：" + p["ambience"])
        if not p["cues"] and (not shooting or route != "mute"):
            t.append("本段无人声词句。")
        post_voices = []
        for cue in p["cues"]:
            l = lines[cue["id"]]
            track = dict(cue, speaker=l["speaker"], mode=l["mode"], lang=l["lang"], text=l["text"])
            if shooting and route == "post_voiceover":
                post_voices.append(track)
                continue
            if l["speaker"] in voices:
                t.append(f'{l["speaker"]} 声音参考 {voices[l["speaker"]]}。')
            t.append(("后期人声 " if route == "post_voiceover" else "人声 ") + voice_row(track))
        post_titles = []
        for cue in p["titles"]:
            v = titles[cue["id"]]
            words = lines[v["line_id"]]["text"] if "line_id" in v else v["text"]
            if v.get("render") == "post":
                post_titles.append(dict(cue, text=words, placement=v.get("placement", "")))
                if shooting and not timed:
                    placement = v.get("placement", "")
                    if placement:
                        t.append(f"画面{placement}留白，保持该区域无字。")
                else:
                    t.append("段内%s–%s秒留出后期字区%s，画面生成时保持该区域无字。" %
                             (sec(cue["from_ms"]), sec(cue["to_ms"]), v.get("placement", "")))
                continue
            t.append("画面字 %s–%s秒｜%s" % (sec(cue["from_ms"]), sec(cue["to_ms"]), words))
        if legacy_sections:
            t.append("四、转场与结束状态")
        for clause in (None if shooting else p.get("join"), p["ending"]):
            if clause and clause not in t:
                t.append(clause)
        if n < request * 1000:
            suffix = "。" if shooting else "以供裁切。"
            t.append("有效内容到%s秒。此后只保持末态至%d秒%s" % (sec(n), request, suffix))
        if legacy_sections:
            t.append("五、一致性与禁区")
            t.append("保持参考资产及出场主体一致。" + ("未发声的人物闭嘴倾听，不追加台词。" if voices else ""))
        elif shooting and voices:
            t.append("只有当前发声者做说话口型，其他人保持自然闭口。")
        if p.get("locks"):
            t.append(p["locks"])
        prompt = "\n".join(t)
        if "user_prompt" in p:
            required(p, "user_prompt")
            prompt = p["user_prompt"]
            verify_prompt_copy(p, prompt, lines, titles, not shooting or route != "post_voiceover")
        result.append(dict(id=p["id"], prompt=prompt, duration=request, keep_ms=n, audio_route=route,
                           ratio="adaptive" if frame else board["ratio"], assets=refs,
                           previous_frame_from=board["segments"][i-1]["id"] if frame else None))
        if post_titles:
            result[-1]["post_titles"] = post_titles
        if post_voices:
            result[-1]["post_voices"] = post_voices
        if "execution" in p:
            result[-1]["execution_hash"] = digest(p["execution"])
        if inherited:
            result[-1]["inherited_refs"] = inherited
    return result


def approved(s, stage):
    return stage in s and s["approved"].get(stage, {}).get("hash") == digest(s[stage])


def locked_rows(prompt):
    return [line for line in prompt.splitlines() if line.startswith(("人声 ", "后期人声 ", "画面字 ", "本段无人声词句"))]


def edit_shape(text):
    """Protect section order, time rows and dedicated copy lanes while leaving shot prose editable."""
    rows = []
    for line in text.splitlines():
        if re.match(r"^[一二三四五]、", line) or locked_rows(line):
            rows.append(line)
        elif re.match(r"^\d+(?:\.\d+)?–\d+(?:\.\d+)?秒，", line):
            rows.append(line.split("，", 1)[0])
        elif line.startswith(("声音：", "有效内容到")):
            rows.append(line.split("：", 1)[0])
        else:
            rows.append("描述")
    return rows


def check_edit(old, new):
    require(locked_rows(old) == locked_rows(new), "文案有变，请更新台词/落版母稿并复审")
    require(edit_shape(old) == edit_shape(new), "请在原时序的对应描述内修改；新增镜头或时段请回方案")
    for a, b in zip(old.splitlines(), new.splitlines()):
        if a != b and not locked_rows(b):
            require(not re.search(r"(再说|说出|说[道着]?|念出|念[道着]?|喊[出道]?|唱[出道]?|读出|播报|补一句|来一句|旁白|台词|对白|口播).{0,12}[“「『\"‘]", b),
                    "发现描述区疑似新增发声，请放入逐字母稿并复核")


def invalidate_clip(s, pid):
    affected = {pid}
    for p in s["prompts"]:
        if p["previous_frame_from"] in affected:
            affected.add(p["id"])
    for key in affected:
        s["clips"].pop(key, None)
    s.update(final=None, delivered=False)


def check_qa(q):
    for k in ("visual", "speech", "sync", "text", "seam", "duration"):
        require(q.get(k) in ("pass", "na"), "缺少通过的检查 " + k)
    require(q["visual"] == "pass" and q["duration"] == "pass", "必须检查画面与时长")
    required(q, "evidence")


def needs_text_check(part):
    return (any(x.startswith("画面字 ") for x in part["prompt"].splitlines())
            or any(a["use"] in {"ui", "logo"} for a in part["assets"]))


def change(s, e):
    op, p = e["op"], e.get("data", {})
    if op == "brief":
        validate_brief(p)
        drop_after(s, "brief")
        s["brief"] = p
        for key in ("ref_plan", "ref_picks", "ref_candidates"):
            s.pop(key, None)
    elif op == "story":
        require("brief" in s, "先判型")
        validate_story(s, p, False)
        drop_after(s, "story")
        s["story"] = p
        s.pop("ref_plan", None)
        s.pop("ref_picks", None)
    elif op == "edit_copy":
        require(approved(s, "story"), "先确定被改动的母稿版本")
        table = p["table"]
        require(table in ("lines", "captions"), "选择 lines 或 captions")
        new_story = json.loads(json.dumps(s["story"]))
        row = index(new_story[table])[p["id"]]
        required(p, "text")
        require(row.get("text") == p["old"], "旧文不匹配；共用音画文案请修改其来源行")
        row["text"] = p["text"]
        validate_story(s, new_story, False)
        if s.get("board"):
            s["previous_board"] = s["board"]
        drop_after(s, "story")
        s["story"] = new_story
    elif op == "approve":
        k = e["stage"]
        require(k in ("story", "board", "prompts") and k in s, "当前没有可确认的产物")
        required(e, "user_message")
        require(e["hash"] == digest(s[k]), "确认针对旧版本")
        if k == "story":
            validate_story(s, s[k], True)
        if k == "board":
            require(s.get("review", {}).get("verdict") == "pass", "文案审查未通过")
        if k == "prompts":
            require(approved(s, "board"), "分镜未确认")
        s["approved"][k] = dict(hash=e["hash"], message=e["user_message"])
    elif op == "review":
        require(approved(s, "story"), "先确认故事与逐字稿")
        require(p["verdict"] in ("changes", "unknown", "pass") and isinstance(p["findings"], list), "审查格式错误")
        required(p, "basis")
        require(p["verdict"] != "pass" or not any(f.get("blocking", True) for f in p["findings"]), "仍有阻塞问题")
        drop_after(s, "review")
        s["review"] = p
    elif op == "ref_plan":
        require(approved(s, "story") and s.get("review", {}).get("verdict") == "pass", "故事或文案审查未通过")
        plan = validate_ref_plan(s, p["items"])
        if set(s.get("ref_plan", {})) - set(plan):
            required(e, "user_message")
        if plan != s.get("ref_plan"):
            clear_board_for_refs(s)
            s["ref_plan"] = plan
            s["ref_picks"] = {rid: cid for rid, cid in s.get("ref_picks", {}).items()
                              if rid in plan and s["ref_candidates"][cid]["spec_hash"] == ref_request(s, rid)["spec_hash"]}
    elif op == "ref_candidate":
        required(p, "ref_id", "candidate_id", "uri", "sha256", "receipt", "spec_hash", "inspection")
        request = ref_request(s, p["ref_id"])
        require(p["spec_hash"] == request["spec_hash"], "候选图来自旧提示词或旧底图")
        verify_candidate(s, p)
        if request["method"] == "provided":
            a = request["sources"][0]
            require(p["uri"] == a["uri"] and (not a.get("sha256") or a["sha256"] == p["sha256"]),
                    "所示图与用户输入不符")
        ledger = s.setdefault("ref_candidates", {})
        require(p["candidate_id"] not in ledger or ledger[p["candidate_id"]] == p, "候选号已指向另一版，请使用新编号")
        ledger[p["candidate_id"]] = p
    elif op in {"ref_pick", "ref_reject"}:
        required(e, "user_message")
        required(p, "ref_id", "candidate_id", "sha256")
        request = ref_request(s, p["ref_id"])
        c = s["ref_candidates"][p["candidate_id"]]
        require(c["ref_id"] == p["ref_id"] and c["sha256"] == p["sha256"] and c["spec_hash"] == request["spec_hash"],
                "用户所选图片不是本项展示的当前版本")
        picks = s.setdefault("ref_picks", {})
        if op == "ref_pick":
            verify_candidate(s, c)
            if picks.get(c["ref_id"]) != c["candidate_id"]:
                clear_board_for_refs(s)
                picks[c["ref_id"]] = c["candidate_id"]
        elif picks.get(c["ref_id"]) == c["candidate_id"]:
            clear_board_for_refs(s)
            del picks[c["ref_id"]]
    elif op == "board":
        require(approved(s, "story") and s.get("review", {}).get("verdict") == "pass", "故事或审查未通过")
        validate_board(s, p)
        drop_after(s, "board")
        s["board"] = p
    elif op == "compile":
        require(approved(s, "board"), "分镜与素材仍待确认")
        drop_after(s, "prompts")
        s["prompts"] = compile_prompts(s)
        for part in s["prompts"]:
            saved = s.get("manual_prompts", {}).get(part["id"])
            if saved:
                old = saved["base"]
                old_rows, new_rows = old["prompt"].splitlines(), part["prompt"].splitlines()
                if (len(old_rows) == len(new_rows) and
                        all(a == b or (locked_rows(a) and locked_rows(b)) for a, b in zip(old_rows, new_rows)) and
                        {k:v for k,v in old.items() if k != "prompt"} == {k:v for k,v in part.items() if k != "prompt"}):
                    edited_rows = saved["edited"].splitlines()
                    part["prompt"] = "\n".join(b if locked_rows(b) else e for b, e in zip(new_rows, edited_rows))
            clip = s.get("take_cache", {}).get(digest(part))
            parent = part["previous_frame_from"]
            if clip and (not parent or clip.get("used_frame") == s["clips"].get(parent, {}).get("last_frame")):
                s["clips"][part["id"]] = dict(clip)
    elif op == "edit_prompt":
        require("prompts" in s, "先编译")
        q = index(s["prompts"])[p["id"]]
        required(p, "prompt")
        check_edit(q["prompt"], p["prompt"])
        q["prompt"] = p["prompt"]
        invalidate_clip(s, p["id"])
        s["approved"].pop("prompts", None)
        s["authorized"] = None
    elif op == "render_authorize":
        required(e, "user_message")
        require(approved(s, "prompts") and e["hash"] == digest(s["prompts"]), "需批准当前完整提示词")
        s["authorized"] = e["hash"]
    elif op == "clip":
        require("prompts" in s and s.get("authorized") == digest(s["prompts"]), "尚未授权此次生成")
        part = index(s["prompts"])[p["id"]]
        required(p, "uri", "task_id", "first_frame", "last_frame")
        require(p["prompt_hash"] == digest(part), "片段并非当前请求的结果")
        parent = part["previous_frame_from"]
        if parent:
            c = s["clips"].get(parent, {})
            require(c.get("accepted") and p.get("used_frame") == c.get("last_frame"), "依赖的已选末帧不匹配")
        check_qa(p["qa"])
        require(p["qa"]["speech"] == "pass", "需检查原句或无意人声")
        if index(s["board"]["segments"])[part["id"]]["cues"] and part.get("audio_route") != "post_voiceover":
            require(p["qa"]["sync"] == "pass", "有声时须核对声音时序")
        if needs_text_check(part) or any(index(s["story"]["captions"])[cue["id"]].get("render") != "post"
                                         for cue in index(s["board"]["segments"])[part["id"]]["titles"]):
            require(p["qa"]["text"] == "pass", "画面字须核对")
        invalidate_clip(s, p["id"])
        s["clips"][p["id"]] = dict(p, accepted=False)
    elif op == "accept_clip":
        required(e, "user_message")
        c = s["clips"][p["id"]]
        require(c["task_id"] == p["task_id"], "确认的是旧片段")
        c["accepted"] = e["user_message"]
    elif op == "finish":
        parts = s["prompts"]
        require(all(s["clips"].get(q["id"], {}).get("accepted") for q in parts), "仍有片段待确认")
        required(p, "uri")
        require(p["tasks"] == [s["clips"][q["id"]]["task_id"] for q in parts], "拼接清单不符")
        check_qa(p["qa"])
        require(p["qa"]["speech"] == "pass", "成片须核对词句/无词")
        if len(parts) > 1:
            require(p["qa"]["seam"] == "pass", "多段成片需核对接缝")
        if s["story"]["lines"]:
            require(p["qa"]["sync"] == "pass", "有声成片需核对音画")
        if any(needs_text_check(part) for part in parts) or s["story"]["captions"]:
            require(p["qa"]["text"] == "pass", "成片须核对画面字")
        s.update(final=p, delivered=False)
    elif op == "accept_final":
        required(e, "user_message")
        require(s.get("final") and e["hash"] == digest(s["final"]), "成片未就绪或已过期")
        s["delivered"] = True
    else:
        raise ValueError("未识别事件 " + op)


def media_request(s, segment_id):
    """Prepare an authorized task without choosing or calling a host tool."""
    require("prompts" in s and approved(s, "prompts") and s.get("authorized") == digest(s["prompts"]), "需当前生成授权")
    part = index(s["prompts"])[segment_id]
    media = [(a["uri"], {"audio": "reference_audio", "video": "reference_video"}.get(a["use"], "reference_image"), a.get("sha256"))
             for a in part["assets"]]
    parent = part["previous_frame_from"]
    if parent:
        clip = s["clips"].get(parent, {})
        require(clip.get("accepted"), "等待上一段确认及有效尾帧")
        media.append((clip["last_frame"], "first_frame", clip.get("last_frame_sha256")))
    items = []
    for uri, role, sha in media:
        if "://" not in uri:
            path = Path(uri)
            require(path.is_file() and sha, "本地输入需提供实际文件与已登记的 sha256")
            with path.open("rb") as stream:
                checksum = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(block)
            require(checksum.hexdigest() == sha, "输入内容已变化，请重新确认该素材")
        items.append(dict(uri=uri, role=role, sha256=sha))
    return dict(capability="generate_video", segment_id=segment_id, prompt=part["prompt"],
                ratio=part["ratio"], duration_s=part["duration"], keep_ms=part["keep_ms"],
                generate_audio=part.get("audio_route") not in {"mute", "post_voiceover"}, prompt_hash=digest(part), media=items,
                **({"audio_route": part["audio_route"]} if "audio_route" in part else {}))


