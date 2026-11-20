"""Scoped edits of existing media, with independent requests and reviewed replacement versions."""
import copy
import hashlib
import json
import math
from pathlib import Path

from engine import require, required, digest, index, check_qa

OPS = {"repair_plan", "present_repair", "approve_repair", "repair_patch",
       "repair_result", "accept_repair", "cancel_repair"}
PRONUNCIATION_ROUTES = {"seedance": "Seedance 生成后回拼", "import_audio": "导入音频后回拼"}


def original(s, pid):
    for name in ("clips", "failed_clips", "unreviewed_clips"):
        if pid in s.get(name, {}):
            return s[name][pid]
    raise ValueError("先取得需要修改的实际样片")


def verify_asset(asset):
    required(asset, "uri", "sha256")
    require(len(asset["sha256"]) == 64 and all(c in "0123456789abcdef" for c in asset["sha256"]), "登记实际资产摘要")
    if "://" not in asset["uri"]:
        path = Path(asset["uri"])
        require(path.is_file(), "找不到返修输入文件")
        with path.open("rb") as stream:
            sha = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha.update(chunk)
        require(sha.hexdigest() == asset["sha256"], "返修输入已改变，请重新交付确认单")


def request(s, pid):
    plan = s.get("repairs", {}).get(pid)
    require(plan is not None and plan["status"] in {"planned", "review"}, "当前没有待执行返修")
    req = plan["request"]
    require(original(s, pid)["task_id"] == req["source"]["task_id"], "返修原片版本已变化")
    verify_asset(req["source"])
    for asset in req["assets"]:
        verify_asset(asset)
    if req.get("pronunciation_route") == "seedance":
        verify_asset(req["seedance_request"]["media"][0])
    elif req.get("pronunciation_route") == "import_audio":
        verify_asset(req["replacement"])
    return copy.deepcopy(req)


def source_request(s, pid):
    req = request(s, pid)
    require(req.get("pronunciation_route") == "seedance", "只有已选 Seedance 的发音返修才能领取生成任务")
    return copy.deepcopy(req["seedance_request"])


def edit_request(s, pid):
    req = request(s, pid)
    if req.get("pronunciation_route") == "seedance":
        replacement = s["repairs"][pid].get("replacement")
        require(replacement, "先取得 Seedance 候选、确认读音并登记实际音频范围")
        verify_asset(replacement)
        req["replacement"] = copy.deepcopy(replacement)
    return req


def card(s, pid):
    import runtime_state as rt
    rt.assert_active(s)
    rt.assert_fresh(s, before="N07")
    req = request(s, pid)
    return dict(title="素材与提示词确认单", id=pid, request=req,
                hash=rt.task_key(s, "repair", pid, req))


def text(s, pid):
    c = card(s, pid)
    req = c["request"]
    route = req.get("pronunciation_route")
    rows = [f'# 素材与提示词确认单 · {pid} · 局部返修 {req["attempt"]}', ""]
    if route:
        rows.extend([PRONUNCIATION_ROUTES[route], ""])
    if route == "seedance":
        rows.extend(["本次 Seedance 投模词全文", "", req["seedance_request"]["prompt"], ""])
    rows.extend([
            req["prompt"], "", "实际编辑输入", json.dumps({k: v for k, v in req.items() if k != "prompt"},
                                                      ensure_ascii=False, indent=2),
            "", "确认对象 " + c["hash"], "请确认按上述定位、范围和输入执行本次局部返修。"])
    return "\n".join(rows)


def check_replacement(asset):
    verify_asset(asset)
    start, end = asset["from_ms"], asset["to_ms"]
    require(type(start) is int and type(end) is int and 0 <= start < end, "登记补丁音频的有效取样区间")


def pronunciation_inputs(req, data):
    route = data.get("pronunciation_route")
    require(route in PRONUNCIATION_ROUTES and data["method"] == "audio_edit",
            "先由用户选择 Seedance 生成后回拼或导入音频后回拼")
    pron = data["pronunciation"]
    required(pron, "cue_id", "term", "observed", "expected")
    require(all(isinstance(pron[k], str) and pron[k].strip() for k in ("cue_id", "term", "observed", "expected")),
            "登记要修的台词、原片读音及正确读音")
    cue = next((c for c in req["original_lines"] if c["id"] == pron["cue_id"]), None)
    require(cue and pron["term"] in cue["text"], "错读词须落在目标时间窗且匹配已确认原句")
    require(req.get("mouth_plan") != "lip_sync", "这两条发音路线只回拼声音；口型单独按画面返修处理")
    require("audio_splice" in req["execution"]["controls"], "先核实原片局部音频回拼能力")
    req.update(pronunciation_route=route, pronunciation=copy.deepcopy(pron))
    if route == "import_audio":
        require("seedance" not in data, "导入音频路线不包含模型投递")
        replacement = data["replacement"]
        require(replacement.get("media_type") == "audio", "请导入修好的实际音频")
        check_replacement(replacement)
        require(replacement["uri"] != req["source"]["uri"], "请提供不同于原片的替换音频")
        req["replacement"] = copy.deepcopy(replacement)
        return

    require("replacement" not in data, "Seedance 替换音频必须来自这轮生成回执")
    seedance = data["seedance"]
    required(seedance, "tool", "model", "schema_evidence")
    require(isinstance(seedance["model"], str) and "seedance" in seedance["model"].lower()
            and seedance.get("verified") is True
            and {"video_edit", "reference_video", "generate_audio"} <= set(seedance.get("controls", [])),
            "先核实当前 Seedance 入口能够基于原视频编辑并返回人声")
    source = seedance["source"]
    verify_asset(source)
    first, last = source["from_ms"], source["to_ms"]
    require(type(first) is int and type(last) is int
            and 0 <= first <= req["from_ms"] < req["to_ms"] <= last <= req["keep_ms"],
            "Seedance 的实际源片范围须覆盖错读和回拼窗口")
    require(source["sha256"] != req["source"]["sha256"] or first == 0,
            "登记非零起点时须提供实际截取的参考片")
    limits = seedance["min_ms"], seedance["max_ms"]
    require(all(type(value) is int for value in limits) and 0 < limits[0] <= last-first <= limits[1],
            "Seedance 源片必须符合已核实的入口时长")
    media = dict(uri=source["uri"], sha256=source["sha256"], role="reference_video")
    relative = (req["from_ms"] - first, req["to_ms"] - first)
    prompt = "\n".join([
        "编辑视频：修正参考视频此处听到的人声发音，保留该说话人的音色、语速和上下句停顿。",
        f"参考视频内 [{relative[0]}, {relative[1]}) 毫秒，{cue['speaker']}，正式原句为「{cue['text']}」。",
        f"读准「{pron['term']}」（{pron['expected']}）。实听问题为 {pron['observed']}。",
        "原句按给定文字读全，保留其余对白、背景声和原节奏。生成带人声的候选视频，供抽取局部声音。"])
    req["seedance_request"] = dict(capability="generate_video", purpose="pronunciation_repair",
                                   segment_id=req["segment_id"], attempt=req["attempt"], prompt=prompt,
                                   media=[media], generate_audio=True, edit_mode=True, context_from_ms=first,
                                   context_to_ms=last, tool=seedance["tool"], model=seedance["model"],
                                   execution=copy.deepcopy(seedance))


def plan_edit(s, data):
    import runtime_state as rt
    pid = data["id"]
    source = original(s, pid)
    affected = rt.descendants(s, {pid})
    rt.assert_no_open(s, affected)
    pending = {key for key, value in s.get("repairs", {}).items() if value["status"] in {"planned", "review"}}
    require(not (affected & pending) and pid not in rt.descendants(s, pending), "存在相互依赖的返修，请先完成或取消前一轮")
    require(s.get("repairs", {}).get(pid, {}).get("status") not in {"planned", "review"},
            "当前返修尚待处理；改方案前取消这次待执行返修")
    required(data, "task_id", "problem", "desired", "method", "source_sha256")
    require(data["task_id"] == source["task_id"], "请指定当前样片版本")
    require(not source.get("sha256") or source["sha256"] == data["source_sha256"], "原片摘要有变")
    method = data["method"]
    require(method in {"audio_edit", "video_edit"}, "局部编辑请选择声音或画面路线；重生成另用 retry_clip")
    start, end = data["from_ms"], data["to_ms"]
    part = index(s["prompts"])[pid]
    require(type(start) is int and type(end) is int and 0 <= start < end <= part["keep_ms"], "返修须定位到段内有效毫秒区间")
    tool = data["execution"]
    required(tool, "tool", "model", "schema_evidence")
    controls = {"source_video", "time_range", method}
    region = data.get("region")
    if method == "video_edit":
        require(region is not None, "画面编辑须给归一化矩形 region，整幅用 [0,0,1,1]")
        require(isinstance(region, list) and len(region) == 4 and
                all(type(v) in (int, float) and math.isfinite(v) for v in region), "region 为 x,y,width,height")
        x, y, width, height = region
        require(0 <= x < 1 and 0 <= y < 1 and width > 0 and height > 0 and x + width <= 1 and y + height <= 1,
                "画面区域超界")
        if region != [0, 0, 1, 1]:
            controls.add("region")
        required(data, "frame_evidence")
    require(tool.get("verified") is True and controls <= set(tool.get("controls", [])),
            "实际编辑入口需支持原视频、时段和所选修改方式；先核实字段或选择可行重生成范围")
    assets = copy.deepcopy(data.get("assets", []))
    for asset in assets:
        verify_asset(asset)
        required(asset, "role", "purpose")
    if method == "video_edit":
        frames = [a for a in assets if a["role"] == "source_frame"]
        require(frames, "上传指定秒点的实际原帧以定位缺陷")
        require(all(type(a.get("at_ms")) is int and start <= a["at_ms"] < end for a in frames),
                "定位原帧须记录落在本次修改区间内的段内 at_ms")
    else:
        required(data, "audio_component", "audition_evidence")
        require(data["audio_component"] in {"speech", "music", "ambience", "mixed"}, "指定要改的声音层")
        if data["audio_component"] != "mixed":
            require((data.get("separated_track") is True and
                     any(a["role"] == data["audio_component"] + "_track" for a in assets))
                    or "audio_separation" in tool.get("controls", []),
                    "当前要改独立声音层，先取得实际分轨或核实分离能力")
    shot = index(s["board"]["segments"])[pid]
    lines = index(s["story"]["lines"])
    relevant = [dict(c, **{k: lines[c["id"]][k] for k in ("speaker", "text", "lang", "mode")})
                for c in shot["cues"] if c["from_ms"] < end and c["to_ms"] > start]
    if method == "audio_edit" and any(c["mode"] in {"dialogue", "address", "chorus"} for c in relevant):
        require(data.get("mouth_plan") in {"existing_timing", "lip_sync"}, "可见人物对白需说明原口型配时或实际口型修复方案")
        if data["mouth_plan"] == "lip_sync":
            require("lip_sync" in tool.get("controls", []), "尚未核实口型修复入口")
    preserve = data.get("preserve")
    require(isinstance(preserve, list) and preserve and all(isinstance(x, str) and x.strip() for x in preserve),
            "明确应保留的画面、声音层和时序")
    req = dict(capability=method, segment_id=pid, attempt=s.get("repairs", {}).get(pid, {}).get("request", {}).get("attempt", 0) + 1,
               source=dict(uri=source["uri"], task_id=source["task_id"], sha256=data["source_sha256"]),
               from_ms=start, to_ms=end, region=region, assets=assets, execution=copy.deepcopy(tool),
               tool=tool["tool"], model=tool["model"], keep_ms=part["keep_ms"], ratio=s["board"]["ratio"],
               original_lines=relevant, preserve=preserve, problem=data["problem"], desired=data["desired"])
    for key in ("audio_component", "mouth_plan", "frame_evidence", "audition_evidence", "separated_track"):
        if key in data:
            req[key] = data[key]
    if data.get("pronunciation") is not None or "pronunciation_route" in data:
        require(part.get("audio_route") != "post_voiceover", "声音来自后期旁白轨，请对该轨回拼后登记 post_track")
        pronunciation_inputs(req, data)
    req["prompt"] = "\n".join([
        "在实际传入的原视频上执行本次编辑。",
        f'段号 {pid}，原片版本 {source["task_id"]}，段内 [{start}, {end}) 毫秒。',
        "指定区域 " + json.dumps(region, ensure_ascii=False) if region is not None else "声音层 " + data["audio_component"],
        "观察到的问题 " + data["problem"], "完成标准 " + data["desired"],
        "保留要求 " + "；".join(preserve), "原句与声源 " + json.dumps(relevant, ensure_ascii=False),
        "输出相同片长的完整候选视频，交付修改处及两端过渡的前后对比。"])
    if "pronunciation_route" in req:
        req["prompt"] += "\n保留原视频流。只把已对齐的补丁声音回拼到指定时段，并交付原片与修复候选。"
    verify_asset(req["source"])
    s.setdefault("repairs", {})[pid] = dict(status="planned", request=req)


def change(s, event):
    import runtime_state as rt
    data, op = event.get("data", {}), event["op"]
    pid = data["id"]
    r = rt.ensure(s)
    if op != "repair_result":
        rt.assert_active(s)
        rt.assert_fresh(s, before="N07")
    if op == "repair_plan":
        required(event, "user_message")
        plan_edit(s, data)
        return
    entry = s.get("repairs", {}).get(pid)
    require(entry is not None, "缺少该段返修记录")
    if op in {"present_repair", "approve_repair"}:
        c = card(s, pid)
        require(event.get("hash") == c["hash"], "返修卡版本已经变化")
        require(not (rt.current_job(s, "repair", pid) or rt.current_job(s, "repair-source", pid)),
                "该返修已提交，先查询或处理其结果")
        if op == "present_repair":
            rt.record_presentation(r, "repair", pid, c["hash"], text(s, pid), data)
            entry.pop("approval", None)
        else:
            required(event, "user_message")
            rt.require_presentation(r, "repair", pid, c["hash"], data)
            entry["approval"] = dict(hash=c["hash"], presentation_id=data["presentation_id"],
                                     reply_id=data["reply_id"], user_message=event["user_message"])
    elif op == "repair_patch":
        job = rt.current_job(s, "repair-source", pid)
        require(job and job["status"] == "succeeded" and job["task_id"] == data["task_id"],
                "先取得本次 Seedance 候选的真实成功回执")
        require(not entry.get("replacement"), "已登记补丁；如需更换，先完成或取消本次返修")
        replacement = data["replacement"]
        required(replacement, "evidence")
        require(replacement.get("media_type") == "video" and replacement["uri"] == job["uri"],
                "取声对象须为本次 Seedance 返回的视频")
        check_replacement(replacement)
        entry["replacement"] = copy.deepcopy(replacement)
    elif op == "cancel_repair":
        required(event, "user_message")
        rt.assert_no_open(s, {pid})
        require(entry["status"] in {"planned", "review"}, "该返修已经结束")
        entry["status"] = "cancelled"
    elif op == "repair_result":
        job = rt.current_job(s, "repair", pid)
        require(job and job["status"] == "succeeded" and job["task_id"] == data["task_id"]
                and job["uri"] == data["uri"], "先登记本次实际编辑成功回执")
        for key in ("first_frame", "last_frame", "sha256"):
            required(data, key)
        verify_asset(data)
        require(data.get("media_type") == "video", "返修结果需为实际视频；静帧只能作中间资产")
        if entry["request"].get("pronunciation_route"):
            check_splice(job["request"], data)
        else:
            require(type(data.get("duration_ms")) is int and abs(data["duration_ms"] - entry["request"]["keep_ms"]) <= 50,
                    "局部返修须保持约定有效时长")
        required(data["qa"], "evidence")
        required(data["checks"]["target"], "observed")
        for key in ("target", "preservation", "boundaries"):
            check = data["checks"][key]
            require(check.get("status") in {"pass", "fail", "unknown"}, "返修检查状态无效")
            required(check, "evidence")
        require(type(data.get("ending_changed")) is bool, "复核有效末帧是否改变")
        entry.update(status="review", result=copy.deepcopy(data))
    elif op == "accept_repair":
        required(event, "user_message")
        rt.assert_no_open(s, rt.descendants(s, {pid}))
        result = entry.get("result", {})
        require(entry["status"] == "review" and result.get("task_id") == data["task_id"]
                and event.get("hash") == digest(result), "请采用已经展示的本次返修结果")
        source = original(s, pid)
        require(source["task_id"] == entry["request"]["source"]["task_id"], "原片已变，重新定位返修")
        check_qa(result["qa"])
        from engine import needs_text_check
        part = index(s["prompts"])[pid]
        if needs_text_check(part):
            require(result["qa"]["text"] == "pass", "实际画面字需要复核")
        require(result["qa"]["speech"] == "pass", "回听正式原句或无词声音")
        if index(s["board"]["segments"])[pid]["cues"]:
            require(result["qa"]["sync"] == "pass", "复核角色口型与正式台词")
        require(all(c["status"] == "pass" for c in result["checks"].values()), "返修目标、保留内容或接缝仍待处理")
        verify_asset(result)
        affected = rt.descendants(s, {pid}) - {pid} if result["ending_changed"] else set()
        if not result["ending_changed"]:
            # Reuse the original verified end-frame asset so downstream clip provenance stays valid.
            source_digest = source.get("last_frame_sha256")
            require((source_digest and result.get("last_frame_sha256") == source_digest)
                    or (not source_digest and entry["request"].get("pronunciation_route")),
                    "保留末帧需提供可核对的同版帧摘要")
        revised = dict(source, **{k: result[k] for k in ("uri", "task_id", "sha256", "first_frame", "last_frame", "qa")},
                       accepted=event["user_message"], repair_source=entry["request"]["source"],
                       repair_request=rt.current_job(s, "repair", pid)["request"], repair_checks=result["checks"])
        revised["last_frame_sha256"] = result.get("last_frame_sha256")
        if not result["ending_changed"]:
            revised["last_frame"] = source["last_frame"]
        for field in ("clips", "failed_clips", "unreviewed_clips"):
            for aid in affected | {pid}:
                s.get(field, {}).pop(aid, None)
        for aid in affected:
            s["attempts"][aid] = s.get("attempts", {}).get(aid, 1) + 1
        for part in s["prompts"]:
            if part["id"] in affected | {pid}:
                s.get("take_cache", {}).pop(digest(part), None)
        s["clips"][pid] = revised
        s.pop("assembly_ok", None)
        s.get("post_tracks", {}).pop(pid, None)
        s.update(final=None, delivered=False)
        entry["status"] = "accepted"


def check_splice(req, result):
    proof = result["audio_splice"]
    required(proof, "source_video_sha256", "output_video_sha256")
    require(proof["source_video_sha256"] == proof["output_video_sha256"]
            and len(proof["source_video_sha256"]) == 64
            and all(c in "0123456789abcdef" for c in proof["source_video_sha256"])
            and result.get("ending_changed") is False, "发音返修须验证原视频流未变")
    source, patch = req["source"], req["replacement"]
    require(result["uri"] not in {source["uri"], patch["uri"]} and proof.get("output_sha256") == result["sha256"]
            and proof.get("source_sha256") == source["sha256"] and proof.get("replacement_sha256") == patch["sha256"],
            "候选须是按已确认原片和补丁回拼后的新视频")
    require(proof.get("from_ms") == req["from_ms"] and proof.get("to_ms") == req["to_ms"]
            and proof.get("replacement_from_ms") == patch["from_ms"] and proof.get("replacement_to_ms") == patch["to_ms"],
            "回拼范围和补丁取样范围须与本次请求一致")
    duration, original = result.get("duration_ms"), proof.get("source_duration_ms")
    require(type(duration) is int and type(original) is int and proof.get("duration_ms") == duration
            and abs(duration - original) <= 50 and duration >= req["keep_ms"] - 50, "回拼须保持原片及有效时长")


def pronunciation_status(s, pid):
    import runtime_state as rt
    entry = s["repairs"][pid]
    route = entry["request"]["pronunciation_route"]
    result = dict(route=route, next="交付并确认返修")
    if entry["status"] == "review":
        result["next"] = "试听并采用回拼后的候选"
        return result
    if not entry.get("approval"):
        return result
    edit = rt.current_job(s, "repair", pid)
    if edit:
        messages = {"succeeded": "核对并登记回拼结果", "failed": "核对回拼失败原因",
                    "cancelled": "回拼已取消"}
        result["next"] = messages.get(edit["status"], "查询原回拼任务")
        return result
    if route == "import_audio" or entry.get("replacement"):
        result["next"] = "领取原片局部回拼"
        return result
    source = rt.current_job(s, "repair-source", pid)
    if source:
        messages = {"succeeded": "回听 Seedance 候选，登记补丁范围", "failed": "核对 Seedance 失败原因",
                    "cancelled": "Seedance 已取消"}
        result["next"] = messages.get(source["status"], "查询原 Seedance 任务")
    else:
        result["next"] = "领取 Seedance 生成"
    return result
