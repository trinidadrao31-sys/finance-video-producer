"""Persistent lifecycle and rule-version layer; never calls a remote media service."""
from __future__ import annotations

import copy
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
import uuid

from engine import (SHOOTING_PROMPT, check_qa, digest, index, media_request, media_tags,
                    ref_request, require, required, voice_row)

VERSION = "2.4.2"
ROOT = Path(__file__).resolve().parents[3]
NODE_NAMES = {
    "N01": "需求整理", "N02": "素材与能力核对", "N03": "故事与文案",
    "N04": "分镜与分段", "N05": "生成提示词", "N06": "生成确认",
    "N07": "片段生成与选片", "N08": "合成与交付",
}
RULE_FILES = {
    "N01": ["references/video-intake", "references/video-workflow/guide.md", "references/四步交付与台词时长.md", "references/video-workflow/scripts/speech_budget.py"],
    "N02": ["references/video-references"],
    "N03": ["references/video-story", "references/video-copy-review", "references/video-scene",
            "references/video-presenter", "references/video-explainer", "references/video-campaign", "references/video-showcase",
            "references/creative-review.md"],
    "N04": ["references/video-board", "references/creative-review.md"], "N05": ["references/video-prompt"],
    "N06": ["references/video-workflow/references/events.md", "references/video-workflow/references/host.md"],
    "N07": ["references/video-finish", "references/video-workflow/scripts/repairs.py"], "N08": ["references/video-finish"],
}
OPEN_JOBS = {"submitting", "submitted", "running", "submission_unknown", "cancel_requested"}
TRANSITIONS = {
    "submitting": {"submitted", "running", "succeeded", "failed", "submission_unknown", "cancel_requested"},
    "submitted": {"running", "succeeded", "failed", "submission_unknown", "cancel_requested", "cancelled"},
    "running": {"succeeded", "failed", "submission_unknown", "cancel_requested", "cancelled"},
    "submission_unknown": {"submitted", "running", "succeeded", "failed", "cancel_requested", "cancelled"},
    "cancel_requested": {"running", "succeeded", "failed", "submission_unknown", "cancelled"},
    "succeeded": set(), "failed": set(), "cancelled": set(),
}
RUNTIME_OPS = {"pause", "resume", "job_submit", "job_update", "retry_job", "node_start", "node_failed",
               "register_artifact", "adopt_skills", "rerun_node", "review_clip", "delivered", "legacy_reconciled", "present_segment", "approve_segment"}
PASSIVE_OPS = {"pause", "resume", "job_update", "clip", "ref_candidate", "review_clip", "node_failed",
               "register_artifact", "finish", "delivered"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(root=None):
    root = Path(ROOT if root is None else root)
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in {".md", ".py", ".json"} and "__pycache__" not in p.parts:
            files[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    nodes = {}
    for node, prefixes in RULE_FILES.items():
        nodes[node] = digest({k: v for k, v in files.items() if any(k == x or k.startswith(x + "/") for x in prefixes)})
    return {"version": VERSION, "hash": digest(files), "nodes": nodes, "files": files}


def ensure(s):
    if "runtime" not in s:
        s["runtime"] = {"project_id": str(uuid.uuid4()), "name": "视频项目", "paused": False,
                        "jobs": {}, "activity": {}, "artifacts": {}, "outputs": {}, "stale": [],
                        "adopted_rules": fingerprint(), "artifact_versions": {}, "created_at": now(),
                        "legacy_unverified": s.get("batch_mode") == "render"}
    r = s["runtime"]
    for k, value in {"jobs": {}, "activity": {}, "artifacts": {}, "outputs": {}, "stale": [],
                     "artifact_versions": {}}.items():
        r.setdefault(k, value)
    s.setdefault("unreviewed_clips", {})
    return r


def assert_active(s):
    require(not ensure(s).get("paused"), "项目已暂停；先恢复。暂停期间只查询原任务和接收真实回执，不新提交。")


def open_jobs(s):
    return [j for j in ensure(s)["jobs"].values() if j["status"] in OPEN_JOBS]


def assert_no_open(s, segment_ids=None):
    jobs = open_jobs(s)
    if segment_ids is not None:
        jobs = [j for j in jobs if j["kind"] == "compose" or j["id"] in segment_ids]
    require(not jobs, "存在在途或提交结果不明的任务；先查询/收回结果，再改动其输入或重跑。")


def require_current_rules(s):
    r = ensure(s)
    require(r["adopted_rules"]["hash"] == fingerprint()["hash"],
            "Skill 文件已改变；先查看版本差异，再明确采用新规则，不静默沿用旧确认。")


def assert_fresh(s, before=None):
    require_current_rules(s)
    stale = ensure(s)["stale"]
    blocked = stale if before is None else [n for n in stale if n < before]
    require(not blocked, "上游节点待重跑：" + "、".join(blocked))


def invalidate_from(s, node):
    require(not any(p["status"] in {"planned", "review"} for p in s.get("repairs", {}).values()), "先处理或取消待执行局部返修，再重跑节点")
    from engine import drop_after, invalidate_clip
    r = ensure(s)
    s.pop("assembly_ok", None)
    r["stale"] = sorted(set(r["stale"]) | {n for n in NODE_NAMES if n >= node})
    if node <= "N05":
        if s.get("story"):
            # Preserve accepted independent takes; the original compiler decides reuse by exact inputs.
            drop_after(s, "story")
        s.pop("plan_ok", None)
        s.pop("batch_mode", None)
        s.pop("active_card_hash", None)
        if node <= "N03":
            s.pop("planned_board", None)
        if node == "N01":
            s.pop("brief", None)
        if node == "N02":
            for field in ("ref_picks", "ref_candidates", "draft_choices"):
                s.pop(field, None)
            r["image_attempts"] = {rid: r.get("image_attempts", {}).get(rid, 1) + 1 for rid in s.get("ref_plan", {})}
    elif node == "N06":
        s.pop("batch_mode", None)
        s["authorized"] = None
    elif node == "N07":
        for part in s.get("prompts", []):
            s.setdefault("attempts", {})[part["id"]] = s.get("attempts", {}).get(part["id"], 0) + 1
            for field in ("clips", "failed_clips", "unreviewed_clips"):
                s.get(field, {}).pop(part["id"], None)
            s.get("take_cache", {}).pop(digest(part), None)
        s.update(final=None, delivered=False)
    else:
        s.update(final=None, delivered=False)
        r["compose_attempt"] = r.get("compose_attempt", 1) + 1


def execution(s, pid=None):
    board = s.get("board", s.get("planned_board", {}))
    spec = index(board["segments"])[pid].get("execution", board.get("execution", {})) if pid else board.get("execution", {})
    required(spec, "model", "tool")
    require(spec.get("verified") is True, "先按真实工具能力核对模型、工具和时长范围")
    lo, hi = spec.get("min_seconds"), spec.get("max_seconds")
    require(type(lo) is int and type(hi) is int and 1 <= lo <= hi <= 30, "工具时长范围未明确或无效")
    return spec


def video_input(s, pid):
    req = media_request(s, pid)
    spec = execution(s, pid)
    require(spec["min_seconds"] <= req["duration_s"] <= spec["max_seconds"], "该段时长不符合已核对工具范围")
    parent = index(s["prompts"])[pid]["previous_frame_from"]
    if "audio_route" in req:
        req["output_ratio"] = s["board"]["ratio"]
    if parent and "audio_route" in req:
        req["parent_task"] = s["clips"][parent]["task_id"]
    return dict(req, model=spec["model"], tool=spec["tool"], execution_hash=digest(spec),
                attempt=s.get("attempts", {}).get(pid, 1))


def base_request(s, kind, identifier):
    if kind in {"repair", "repair-source"}:
        import repairs
        return repairs.source_request(s, identifier) if kind == "repair-source" else repairs.edit_request(s, identifier)
    if kind == "video":
        require(s.get("batch_mode") == "render", "当前未授权生成视频")
        return video_input(s, identifier)
    if kind in {"image", "audio"}:
        require(s.get("plan_ok"), "先确认创作方案与参考资产清单")
        req = ref_request(s, identifier)
        is_audio = s["ref_plan"][identifier]["use"] == "audio"
        require(is_audio == (kind == "audio"), "请按参考资产的实际类型选择领取入口")
        require(req["method"] != "provided", "客户已提供原图；直接登记核验后的候选，不创建生成任务")
        return dict(req, capability="generate_voice_sample" if is_audio else "generate_image", attempt=ensure(s).get("image_attempts", {}).get(identifier, 1))
    require(not any(p["status"] in {"planned", "review"} for p in s.get("repairs", {}).values()), "先处理待确认的局部返修")
    require(kind == "compose" and identifier == "final", "任务类型或合成对象无效")
    parts = s.get("prompts", [])
    require(parts and all(s.get("clips", {}).get(p["id"], {}).get("accepted") for p in parts), "仍有片段未采用，不能合成")
    from workflow import assembly_card
    card = assembly_card(s)
    require(s.get("assembly_ok", {}).get("hash") == card["hash"], "请先取得当前片段的合成授权")
    assembly = card["clips"]
    tracks = {p["id"]: p["post_track"] for p in assembly if "post_track" in p}
    post_titles = {p["id"]: p["post_titles"] for p in assembly if p.get("post_titles")}
    seams = []
    if s["board"].get("prompt_format") == SHOOTING_PROMPT:
        for before, after in zip(assembly, assembly[1:]):
            seams.append(dict(previous=before["id"], next=after["id"], kind=after["seam"], join=after["join"],
                              last_frame=s["clips"][before["id"]]["last_frame"],
                              first_frame=s["clips"][after["id"]]["first_frame"]))
    from engine import verify_voice_file
    for t in tracks.values():
        verify_voice_file(t["uri"], t["sha256"], t["duration_s"])
    return {"capability": "assemble", **({"post_tracks": tracks} if tracks else {}),
            **({"post_titles": post_titles} if post_titles else {}), "attempt": ensure(s).get("compose_attempt", 1),
            **({"seams": seams} if seams else {}),
            "ratio": s["board"]["ratio"], "duration_ms": s["story"]["duration_ms"],
            "clips": [{"id": p["id"], "uri": s["clips"][p["id"]]["uri"], "keep_ms": p["keep_ms"],
                       "task_id": s["clips"][p["id"]]["task_id"]} for p in parts]}


def task_key(s, kind, identifier, req):
    return digest({"project": ensure(s)["project_id"], "kind": kind, "id": identifier, "request": req})


def current_job(s, kind, identifier):
    try:
        req = base_request(s, kind, identifier)
    except (ValueError, KeyError, TypeError):
        return None
    return ensure(s)["jobs"].get(task_key(s, kind, identifier, req))


def segment_card(s, pid):
    key, req = can_submit(s, "video", pid, preview=True)
    part = index(s["prompts"])[pid]
    bindings = [{"id": a["id"], "label": a["label"], "version": a.get("selected_candidate", a.get("version") or a.get("sha256") or a["uri"]),
                 **{k: a[k] for k in ("speaker", "provider", "voice", "duration_s") if k in a}} for a in part["assets"]]
    if s["board"].get("prompt_format") == SHOOTING_PROMPT:
        for meta, tag in zip(bindings, media_tags(part["assets"])):
            meta["prompt_tag"] = tag
    if part["previous_frame_from"]:
        parent = part["previous_frame_from"]
        bindings.append(dict(id=parent, label="已采用上段有效末帧", version=s["clips"][parent]["task_id"]))
    card = dict(title="素材与提示词确认单", id=pid, attempt=req["attempt"], hash=key,
                assets=[dict(meta, **media) for meta, media in zip(bindings, req["media"])],
                output_ratio=s["board"]["ratio"], execution=execution(s, pid), request=req,
                post_titles=part.get("post_titles", []), repair=s.get("repair_notes", {}).get(pid))
    if part.get("post_voices"):
        card["post_voices"] = part["post_voices"]
    if s["board"].get("prompt_format") == SHOOTING_PROMPT:
        segment = index(s["board"]["segments"])[pid]
        if segment["seam"] != "opening":
            card["join"] = dict(kind=segment["seam"], description=segment["join"])
    return card


def segment_text(s, pid):
    card = segment_card(s, pid)
    req = card["request"]
    rows = [f'# 素材与提示词确认单 · {pid} · 第 {req["attempt"]} 次提交', "",
            f'画幅 {card["output_ratio"]} · 请求 {req["duration_s"]} 秒 · 保留 {req["keep_ms"]/1000:g} 秒',
            f'入口 {req["tool"]} · 模型 {req["model"]} · 声音 {req.get("audio_route", "待核旧版声音路线")}', "",
            "本次实际输入", ""]
    for i, a in enumerate(card["assets"]):
        tag = f'{a["prompt_tag"]} · ' if "prompt_tag" in a else ""
        rows.append(f'{i+1}. {tag}{a["label"]} · {a["role"]} · {a["uri"]} · 版本 {a.get("version")}')
    if not card["assets"]:
        rows.append("纯文字输入")
    roles = {a["role"] for a in card["assets"]}
    if "reference_video" not in roles:
        rows.append("本次不使用参考视频；仅提交上述实际素材，不要求三模态齐全。")
    rows.extend(["", "消耗与执行确认", "本单是内容与素材确认；视频生成为高消耗生成，实际开拍仍须按宿主取得本批明确生成确认。具体消耗预估仅作可选信息，没有数值或查询失败不额外挂起；不编造数值或写成 0。实际工具与权限限制照常遵守。"])
    if card.get("repair"):
        rows.extend(["", "本次修改", card["repair"]["reason"]])
    if card.get("join"):
        rows.extend(["", "与上一段的接法", card["join"]["description"]])
    rows.extend(["", "本次投模全文", ""])
    rows.append(req["prompt"])
    if card.get("post_voices"):
        rows.extend(["", "后期旁白轨（段内时间）", "", *(voice_row(cue) for cue in card["post_voices"])])
    if card.get("post_titles"):
        import json
        rows.extend(["", "后期字轨", json.dumps(card["post_titles"], ensure_ascii=False, indent=2)])
    rows.extend(["", f'确认对象 {card["hash"]}', f'请确认是否按以上完整输入生成 {pid} 本次版本。'])
    return "\n".join(rows)


def record_presentation(r, kind, identifier, card_hash, content, data):
    required(data, "message_id", "content")
    require(data["content"] == content, "交付正文须与当前完整确认单一致")
    item = dict(hash=card_hash, message_id=data["message_id"], content=content,
                content_hash=digest(content), at=now())
    r.setdefault("presentations", {})[kind + ":" + identifier] = item


def require_presentation(r, kind, identifier, card_hash, data):
    required(data, "presentation_id", "reply_id")
    shown = r.get("presentations", {}).get(kind + ":" + identifier, {})
    require(shown.get("hash") == card_hash and shown.get("message_id") == data["presentation_id"],
            "先在用户对话实际交付本次完整确认单，再绑定回复")
    require(data["reply_id"] != shown["message_id"], "需用户随后回复，不能用交付消息代替确认")
    return shown


def can_submit(s, kind, identifier, preview=False):
    assert_active(s)
    require(not ensure(s).get("legacy_unverified"), "旧项目缺少提交台账；先核对旧任务再放行，不假定此前未提交。")
    assert_fresh(s, before={"video": "N07", "image": "N02", "audio": "N02", "repair": "N07",
                            "repair-source": "N07", "compose": "N08"}.get(kind))
    req = base_request(s, kind, identifier)
    if not preview and kind in {"video", "repair-source"}:
        failed = ensure(s)["activity"].get("N06", {})
        require(failed.get("status") != "failed",
                "N06 生成确认已挂起：" + failed.get("reason", "") + "；先核对实际卡点、取得本批执行确认，再登记 node_start N06；具体预估不是本包的必填条件。")
    key = task_key(s, kind, identifier, req)
    require(key not in ensure(s)["jobs"], "此输入已登记任务；查询原任务，或明确返修后再生成，不重复提交。")
    if kind in {"repair", "repair-source"}:
        import repairs
        assert_no_open(s, descendants(s, {identifier}))
        approval = s["repairs"][identifier].get("approval", {})
        plan_hash = repairs.card(s, identifier)["hash"]
        require(approval.get("hash") == plan_hash, "先交付并确认本次返修定位及完整编辑指令")
        require_presentation(ensure(s), "repair", identifier, plan_hash, approval)
    if kind == "video":
        pending = {pid for pid, p in s.get("repairs", {}).items() if p["status"] in {"planned", "review"}}
        require(identifier not in descendants(s, pending), "本段或其源帧正在返修，先采用或取消返修")
        require(not any(j["kind"] == kind and j["id"] == identifier for j in open_jobs(s)),
                "该段仍有在途或结果不明任务，先查询原回执")
        part = index(s["prompts"])[identifier]
        if "audio_route" not in part:
            shot = index(s["board"]["segments"])[identifier]
            require(not (shot["cues"] or s["brief"].get("audio_policy") == "mute"),
                    "旧版对白或静音段需先按新版核对声音路线和样音，再确认本次输入")
        for asset in part["assets"]:
            if asset["use"] == "audio" and asset.get("speaker"):
                required(asset, "voice", "provider")
                duration = asset.get("duration_s")
                require(type(duration) in (int, float) and math.isfinite(duration) and duration > 0, "角色样音缺实际时长")
            require(asset.get("sha256") or asset["uri"].startswith("asset://"),
                    "实际输入须有文件摘要或经宿主核实的不可变资产版本：" + asset["label"])
        spec = execution(s, identifier)
        roles = {m["role"] for m in req["media"]}
        require(roles <= set(spec.get("media_roles", [])), "当前入口未核实这些输入用途：" + "、".join(sorted(roles - set(spec.get("media_roles", [])))))
        cap = spec.get("max_reference_audio_s")
        if cap is not None and "reference_audio" in roles:
            require(type(cap) in (int, float) and math.isfinite(cap) and cap > 0, "核实入口声明的参考音频时长限制")
            durations = [a.get("duration_s") for a in index(s["prompts"])[identifier]["assets"] if a["use"] == "audio"]
            require(all(type(n) in (int, float) and n > 0 for n in durations) and sum(durations) <= cap,
                    "实际样音时长未知或超出当前入口限制")
        if not preview:
            approval = ensure(s).get("segment_approvals", {}).get(identifier, {})
            require(approval.get("hash") == key, "先展示并确认本段本次完整投模词及真实输入，再提交")
            require_presentation(ensure(s), "video", identifier, key, approval)
        require(identifier not in s.get("clips", {}) and identifier not in s.get("failed_clips", {})
                and identifier not in s.get("unreviewed_clips", {}), "该段已有结果；查看结果或明确指定返修。")
    if kind == "compose":
        require(not s.get("final"), "已有成片，不重复合成")
    return key, req


def change(s, e):
    r, op, data = ensure(s), e["op"], e.get("data", {})
    if op not in PASSIVE_OPS:
        assert_active(s)
    if op in {"pause", "resume"}:
        required(e, "user_message")
        require(r["paused"] == (op == "resume"), "项目暂停状态没有发生变化")
        r.update(paused=(op == "pause"), pause_reason=data.get("reason", e["user_message"]))
    elif op == "present_segment":
        card = segment_card(s, data["id"])
        require(e.get("hash") == card["hash"], "交付对象已经变化")
        record_presentation(r, "video", data["id"], card["hash"], segment_text(s, data["id"]), data)
        r.get("segment_approvals", {}).pop(data["id"], None)
    elif op == "approve_segment":
        required(e, "user_message", "hash")
        card = segment_card(s, data["id"])
        require(e["hash"] == card["hash"], "段号、输入或尝试版已变化，请重发当前段全文")
        require_presentation(r, "video", data["id"], card["hash"], data)
        approval = dict(hash=card["hash"], attempt=card["attempt"], user_message=e["user_message"], at=now(),
                        presentation_id=data["presentation_id"], reply_id=data["reply_id"])
        r.setdefault("segment_approvals", {})[data["id"]] = approval
    elif op == "legacy_reconciled":
        required(e, "user_message")
        required(data, "evidence")
        require(r.get("legacy_unverified") and data.get("no_open_jobs") is True,
                "旧任务须按原回执核对，确认不存在在途或提交不明任务后才能继续")
        r["legacy_unverified"] = False
        r["legacy_evidence"] = data["evidence"]
    elif op == "node_start":
        node = data["node"]
        require(node in NODE_NAMES, "未知节点")
        assert_fresh(s, before=node)
        r["activity"][node] = {"status": "running", "started_at": now(), "reason": data.get("reason", "")}
    elif op == "node_failed":
        require(data["node"] in NODE_NAMES, "未知节点")
        required(data, "reason")
        r["activity"][data["node"]] = {"status": "failed", "reason": data["reason"], "updated_at": now()}
    elif op == "register_artifact":
        node = data["node"]
        require(node in NODE_NAMES, "未知节点")
        required(data, "path", "label")
        path = Path(data["path"]).resolve()
        require(path.is_file(), "产物文件不存在；不能登记占位链接")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        require(not data.get("sha256") or data["sha256"] == sha, "产物内容摘要不匹配")
        output = r["outputs"].get(node, {})
        item = {"path": str(path), "label": data["label"], "sha256": sha,
                "env_id": data.get("env_id"), "created_at": now(), "rule_hash": r["adopted_rules"]["hash"],
                "output_revision": output.get("revision"), "output_hash": output.get("hash")}
        require(data.get("env_id"), "本地产物必须记录真实来源环境")
        r["artifacts"].setdefault(node, []).append(item)
    elif op == "job_submit":
        key, req = can_submit(s, data["kind"], data["id"])
        require(data.get("request_hash") == digest(req), "提交登记与当前请求内容不一致")
        r["jobs"][key] = {"key": key, "kind": data["kind"], "id": data["id"], "attempt": req["attempt"],
                          "request_hash": digest(req), "request": req, "status": "submitting", "task_id": None,
                          "created_at": now(), "updated_at": now(), "progress": None,
                          "rule_hash": r["adopted_rules"]["hash"]}
    elif op == "job_update":
        required(data, "key", "evidence")
        require(data["key"] in r["jobs"], "未知任务；不能凭空补任务回执")
        job = r["jobs"][data["key"]]
        status = data["status"]
        require(status == job["status"] or status in TRANSITIONS[job["status"]], "任务状态不可倒退或重开")
        if job["status"] == "submission_unknown" and status != "submission_unknown":
            require(data.get("queried") is True, "提交结果不明须查询原任务/去重键后才能解除")
        task_id = data.get("task_id", job.get("task_id"))
        if task_id:
            require(not job.get("task_id") or job["task_id"] == task_id, "回执任务号与原任务不符")
        if status in {"submitted", "running", "cancel_requested", "succeeded", "cancelled"}:
            require(isinstance(task_id, str) and task_id.strip(), "此状态需要真实任务句柄")
        if status == "succeeded":
            required(data, "uri")
        if status == "failed":
            required(data, "error")
        progress = data.get("progress", job.get("progress"))
        require(progress is None or (type(progress) in (int, float) and math.isfinite(progress) and 0 <= progress <= 100),
                "进度必须来自工具返回的 0–100 数值，或留空")
        allowed = {"status", "task_id", "evidence", "queried", "uri", "error", "progress", "receipt", "actual_model", "actual_tool"}
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            require(all(job.get(k) == v for k, v in data.items() if k in allowed), "终态回执不可改写；重新生成需新任务")
            return
        job.update({k: v for k, v in data.items() if k in allowed})
        job.update(status=status, task_id=task_id, progress=progress, updated_at=now())
    elif op == "retry_job":
        required(e, "user_message")
        job = r["jobs"][data["key"]]
        require(job["status"] in {"failed", "cancelled"}, "未确认失败或取消，不能重新提交")
        require(current_job(s, job["kind"], job["id"]) == job, "返修对象已经过期")
        require(job["kind"] not in {"repair", "repair-source"},
                "局部返修失败后先取消该轮、针对失败证据修订 repair_plan，再交付确认")
        if job["kind"] == "video":
            from workflow import revise_input
            affected = descendants(s, {job["id"]})
            require(not any(pid in affected and p["status"] in {"planned", "review"} for pid, p in s.get("repairs", {}).items()), "先处理受影响段的局部返修")
            assert_no_open(s, affected)
            for pid in affected:
                s["attempts"][pid] = s.get("attempts", {}).get(pid, 1) + 1
                for field in ("clips", "failed_clips", "unreviewed_clips"):
                    s.get(field, {}).pop(pid, None)
            revise_input(s, job["id"], data, e["user_message"])
            s.update(final=None, delivered=False)
        elif job["kind"] in {"image", "audio"}:
            r.setdefault("image_attempts", {})[job["id"]] = job["attempt"] + 1
        else:
            r["compose_attempt"] = job["attempt"] + 1
    elif op == "adopt_skills":
        required(e, "user_message")
        require(data["scope"] in {"future", "rerun"}, "选择仅影响后续或重跑指定节点")
        current = fingerprint()
        require(data.get("hash") == current["hash"], "Skill 又有变化，先重新查看差异")
        if data["scope"] == "rerun":
            nodes = data.get("nodes", [])
            require(nodes and all(n in NODE_NAMES for n in nodes), "请明确重跑节点")
            assert_no_open(s)
            first = min(nodes)
            invalidate_from(s, first)
        r["adopted_rules"] = current
        r.setdefault("adoption_history", []).append({"hash": current["hash"], "scope": data["scope"],
                                                    "nodes": data.get("nodes", []), "at": now()})
    elif op == "rerun_node":
        required(e, "user_message")
        require(data["node"] in NODE_NAMES, "请明确重跑节点")
        require_current_rules(s)
        assert_no_open(s)
        invalidate_from(s, data["node"])
    elif op == "review_clip":
        require(data["id"] in s["unreviewed_clips"], "该段没有待复核回执")
        clip = s["unreviewed_clips"][data["id"]]
        require(clip["task_id"] == data["task_id"], "复核对象不是当前片段")
        required(data["qa"], "evidence")
        from engine import change as core_change
        changed = dict(clip, qa=data["qa"])
        if any(data["qa"].get(k) == "unknown" for k in ("visual", "speech", "sync", "text", "seam", "duration")):
            s["unreviewed_clips"][data["id"]] = changed
            return
        if any(data["qa"].get(k) == "fail" for k in ("visual", "speech", "sync", "text", "seam", "duration")):
            s.setdefault("failed_clips", {})[data["id"]] = changed
        else:
            core_change(s, {"op": "clip", "data": changed})
        s["unreviewed_clips"].pop(data["id"])
    elif op == "delivered":
        require(s.get("final"), "没有成片，不能标记已交付")
        required(data, "uri", "evidence")
        require(data["uri"] == s["final"]["uri"], "交付的不是最新成片")
        s["delivered"] = True
    else:
        raise ValueError("未知状态事件 " + op)


def descendants(s, seeds):
    affected = set(seeds)
    for p in s.get("prompts", []):
        if p["previous_frame_from"] in affected:
            affected.add(p["id"])
    return affected


def before_business(s, e):
    op = e["op"]
    if op not in PASSIVE_OPS:
        assert_active(s)
    if op in {"brief", "plan", "revise_ref", "start_batch", "approve_plan", "retry_clip"}:
        pending = {pid for pid, p in s.get("repairs", {}).items() if p["status"] in {"planned", "review"}}
        require(not pending or (op == "retry_clip" and not (descendants(s, {e["data"]["id"]}) & pending)
                               and e["data"]["id"] not in descendants(s, pending)), "先处理或取消当前局部返修")
    if op in {"brief", "plan", "revise_ref", "start_batch", "approve_plan"}:
        assert_no_open(s)
    if op == "brief":
        assert_fresh(s, before="N01")
    if op == "plan":
        # Plan carries the updated reference plan as well as story/board; N02 may be recomputed in this transaction.
        assert_fresh(s, before="N02")
    if op in {"approve_plan", "start_batch", "retry_clip"}:
        # start_batch itself rebuilds N05 with selected assets and edited prompt.
        assert_fresh(s, before={"approve_plan": "N06", "start_batch": "N05", "retry_clip": "N07"}[op])
    if op in {"approve_plan", "start_batch"} and e.get("mode") == "render":
        for shot in s["planned_board"]["segments"]:
            execution(dict(s, board=s["planned_board"]), shot["id"])
    if op == "retry_clip":
        affected = descendants(s, {e["data"]["id"]})
        assert_no_open(s, affected)
        for pid in affected:
            s.get("unreviewed_clips", {}).pop(pid, None)
    if op == "ref_candidate":
        data = e["data"]
        if ref_request(s, data["ref_id"])["method"] != "provided":
            kind = "audio" if s["ref_plan"][data["ref_id"]]["use"] == "audio" else "image"
            job = current_job(s, kind, data["ref_id"])
            require(job and job["status"] == "succeeded" and job["uri"] == data["uri"],
                    "先登记该参考资产的真实生成成功回执")
    if op == "clip":
        data = e["data"]
        job = current_job(s, "video", data["id"])
        require(job and job["status"] == "succeeded" and job["task_id"] == data["task_id"]
                and job["uri"] == data["uri"], "先登记该片段真实任务成功回执，再登记视频结果")
    if op == "finish":
        job = current_job(s, "compose", "final")
        require(job and job["status"] == "succeeded" and job["uri"] == e["data"]["uri"], "先登记真实合成任务成功回执")


def record_outputs(s, e, rev):
    r = ensure(s)
    op = e["op"]
    mapping = {}
    if op == "brief":
        mapping = {"N01": s["brief"], "N02": {"assets": s["brief"]["assets"], "missing": s["brief"]["missing"]}}
        r["stale"] = sorted(set(r["stale"]) | {n for n in r["outputs"] if n >= "N03"})
    elif op == "plan":
        r["stale"] = sorted(set(r["stale"]) | {n for n in r["outputs"] if n >= "N06"})
        mapping = {"N02": {"references": s.get("ref_plan", {}), "assets": s["brief"]["assets"]},
                   "N03": {"story": s["story"], "review": s["review"]}, "N04": s["planned_board"]}
        import engine
        mapping["N05"] = engine.compile_prompts(dict(s, board=s["planned_board"]), planned=bool(s.get("ref_plan")))
    elif op in {"approve_plan", "start_batch"}:
        mapping = {"N06": {"plan_ok": s.get("plan_ok"), "mode": s.get("batch_mode"), "authorized": s.get("authorized")}}
        if s.get("prompts"):
            mapping["N05"] = s["prompts"]
    elif op == "revise_ref":
        r["stale"] = sorted(set(r["stale"]) | {n for n in r["outputs"] if n >= "N05"})
    elif op == "ref_candidate":
        mapping = {"N02": {"ref_candidates": s.get("ref_candidates", {})}}
    elif op in {"clip", "review_clip", "accept_batch", "retry_clip"}:
        mapping = {"N07": {"clips": s.get("clips", {}), "unreviewed": s.get("unreviewed_clips", {}),
                           "failed": s.get("failed_clips", {})}}
    elif op == "finish":
        mapping = {"N08": s["final"]}
    for node, value in mapping.items():
        r["outputs"][node] = {"revision": rev, "value": copy.deepcopy(value), "hash": digest(value),
                              "rule_hash": r["adopted_rules"]["hash"], "recorded_at": now()}
        r["artifact_versions"][node] = r["adopted_rules"]["hash"]
        r["activity"].pop(node, None)
        if node in r["stale"]:
            r["stale"].remove(node)
    if op in {"approve_plan", "start_batch"}:
        r["stale"] = [n for n in r["stale"] if n not in {"N06"}]
        if s.get("prompts") and all(s.get("clips", {}).get(p["id"], {}).get("accepted") for p in s["prompts"]):
            r["stale"] = [n for n in r["stale"] if n != "N07"]
    if op in {"repair_result", "accept_repair"}:
        r["outputs"]["N07"] = dict(revision=rev, value=copy.deepcopy(s.get("repairs", {})),
                                    hash=digest(s.get("repairs", {})), rule_hash=r["adopted_rules"]["hash"], recorded_at=now())
    r["updated_at"] = now()


def dispatch(s, legacy):
    r = ensure(s)
    out = dict(legacy)
    if r.get("legacy_unverified"):
        return dict(out, action="旧项目缺少任务台账；先核对旧任务与历史产物，再恢复", legacy_unverified=True, ready=[])
    if r["paused"]:
        return dict(out, action="项目已暂停；可查看产物或查询原任务", paused=True, ready=[])
    if r["stale"] and min(r["stale"]) <= "N06":
        first = min(r["stale"])
        # Inputs have been rebuilt; N06/N05 can be re-confirmed without regenerating earlier content.
        if first == "N06" and s.get("plan_ok"):
            return dict(out, action="重新展示当前输入并确认生成范围", stale=r["stale"], ready=[])
        return dict(out, action="重跑 " + first + " " + NODE_NAMES[first] + "；保留未受影响产物", stale=r["stale"], ready=[])
    if r["stale"]:
        out["stale"] = r["stale"]
    if r["adopted_rules"]["hash"] != fingerprint()["hash"]:
        return dict(out, action="Skill 已改变；先查看差异并选择采用范围", rule_change=True, ready=[])
    running = [j["id"] for j in open_jobs(s)]
    out["in_flight"] = running
    if "ready" in out:
        out["ready"] = [pid for pid in out["ready"] if not current_job(s, "video", pid)]
        failures = [j["id"] for j in r["jobs"].values() if j["kind"] == "video" and j["status"] in {"failed", "cancelled"}
                    and current_job(s, "video", j["id"]) == j]
        out["needs_repair"] = sorted(set(out.get("needs_repair", [])) | set(failures))
        out["awaiting_review"] = sorted(s.get("unreviewed_clips", {}))
        receipt = [p["id"] for p in s.get("prompts", []) if (j := current_job(s, "video", p["id"]))
                   and j["status"] == "succeeded" and p["id"] not in s.get("clips", {})
                   and p["id"] not in s.get("failed_clips", {}) and p["id"] not in s.get("unreviewed_clips", {})]
        out["awaiting_receipt"] = receipt
        awaiting, ready, issues = [], [], {}
        for pid in out["ready"]:
            try:
                card = segment_card(s, pid)
            except (ValueError, KeyError, OSError) as exc:
                issues[pid] = str(exc)
                continue
            approval = r.get("segment_approvals", {}).get(pid, {})
            shown = r.get("presentations", {}).get("video:" + pid, {})
            approved = (approval.get("hash") == card["hash"] and shown.get("hash") == card["hash"]
                        and approval.get("presentation_id") == shown.get("message_id") and bool(approval.get("reply_id")))
            (ready if approved else awaiting).append(pid)
        out.update(ready=ready, awaiting_segment_approval=awaiting, input_issues=issues)
        if awaiting:
            out.update(stage=3, action="交付本次完整提示词确认单并等待回复：" + "、".join(awaiting))
        elif issues:
            out["action"] = "核对实际输入：" + "；".join(k + " " + v for k, v in issues.items())
        if running:
            out["action"] = "生成任务在途；查询原任务，不重复提交"
        elif receipt or out["awaiting_review"]:
            out["action"] = "展示真实片段并登记复核结果"
        elif out["needs_repair"]:
            out["action"] = "查看失败原因；仅返修指定片段"
    pending = [pid for pid, p in s.get("repairs", {}).items() if p["status"] in {"planned", "review"}]
    if pending:
        out.update(stage=3, pending_repairs=pending, action="局部返修待交付、执行或核对结果：" + "、".join(pending))
        from repairs import pronunciation_status
        progress = {pid: pronunciation_status(s, pid) for pid in pending
                    if s["repairs"][pid]["request"].get("pronunciation_route")}
        if progress:
            out["pronunciation_repairs"] = progress
            out["action"] += "。发音返修 " + "；".join(f"{pid} {item['next']}" for pid, item in progress.items())
    failed_confirmation = r["activity"].get("N06", {})
    if failed_confirmation.get("status") == "failed" and not running:
        out.update(ready=[], stage=3, confirmation_blocked=True,
                   action="生成确认已挂起：" + failed_confirmation["reason"] + "；保留当前提示词和素材，按宿主规则恢复后再确认。")
    return out
