#!/usr/bin/env python3
"""四步工作流。每项目独立数据库；本程序构造请求，实际媒体由宿主执行。"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

import engine as core
import runtime_state as runtime
import state_card
import speech_budget
import env_check
import repairs
from engine import digest, require, required


def plan_card(s):
    require("planned_board" in s, "先准备创作方案")
    snapshot = dict(s, board=s["planned_board"])
    refs = list(s.get("ref_plan", {}).values())
    previews = core.compile_prompts(snapshot, planned=bool(refs))
    card = dict(idea=s["idea"], brief=s["brief"], story=s["story"], review=s["review"],
                board=s["planned_board"], references=refs,
                segments=[{k: v for k, v in p.items() if k != "assets"} for p in previews])
    card["deliverable"] = "分镜脚本确认单"
    card["speech_review"] = speech_budget.review(s["story"], s["planned_board"])
    card["brief_card"] = brief_card(s)
    return dict(card, hash=digest(card))


def brief_card(s):
    brief = s.get("brief", {})
    story = s.get("story", {})
    board = s.get("planned_board", {})
    total = brief.get("target_duration_ms", story.get("duration_ms"))
    result = dict(title="需求确认单", idea=s["idea"], target_duration_ms=total)
    cursor = 0
    segments = []
    for part in board.get("segments", []):
        end = cursor + part["keep_ms"]
        segments.append(dict(id=part["id"], from_ms=cursor, to_ms=end, content=part["subjects"],
                             reason=part.get("split_reason", part.get("join") or part["ending"])))
        cursor = end
    result["segments"] = segments or brief.get("proposed_segments", [])
    if total is not None:
        require(type(total) is int and total > 0, "目标时长须为正整数毫秒")
        if "silent_ms" in brief:
            silent = brief["silent_ms"]
        elif board:
            windows = sum(c["to_ms"] - c["from_ms"] for p in board["segments"] for c in p["cues"])
            silent = max(0, story["duration_ms"] - windows)
        else:
            silent = total if brief.get("speech") is False else None
        if silent is not None:
            require(type(silent) is int and 0 <= silent <= total, "无词独占时长无效")
            window = (total - silent) / 1000
            result.update(silent_ms=silent, speaking_seconds=window, budget_chars=round(window*250/60),
                          range_chars=[round(window*240/60), round(window*270/60)])
            text = brief.get("spoken_zh")
            if text is None and story.get("lines") is not None and all(speech_budget.is_mandarin(x["lang"]) for x in story["lines"]):
                text = "".join(row.get("spoken_zh", row["text"]) for row in story["lines"])
            n = speech_budget.spoken_count(text) if text is not None else None
            if brief.get("speech") is False:
                n = 0
            measured = brief.get("read_ms")
            lines = story.get("lines", [])
            if measured is None and lines and all(type(x.get("read_ms")) is int and x["read_ms"] > 0 for x in lines):
                measured = sum(x["read_ms"] for x in lines)
            if measured is not None:
                require(type(measured) is int and measured > 0, "实读时长须为正整数毫秒")
            if n is not None or measured is not None:
                needed = measured / 1000 if measured is not None else n * 60 / 250
                result.update(spoken_chars=n, timing=speech_budget.timing_text(n, window, measured / 1000 if measured else None),
                              keep_copy_seconds=round(needed + silent/1000, 1))
                if n is not None:
                    result["trim_chars_at_default"] = max(0, n - round(window*250/60))
    return dict(result, hash=digest(result))


def draft_text(s):
    """Render the actual plan card's text lanes and compiled prompts verbatim for human review."""
    card = plan_card(s)
    story = card["story"]
    rows = ["# 分镜脚本确认单（讨论稿）", "", story["synopsis"], "", *card["speech_review"], "", "## 逐字母稿", ""]
    lines = core.index(story["lines"])
    def literal(text):
        return json.dumps(text, ensure_ascii=False)
    for line in lines.values():
        rows.append(f'人声 {line["id"]}（{line["speaker"]}，{line["lang"]}）　{literal(line["text"])}')
    for caption in story["captions"]:
        text = lines[caption["line_id"]]["text"] if "line_id" in caption else caption["text"]
        rows.append(f'屏显 {caption["id"]}　{literal(text)}')
    if not lines and not story["captions"]:
        rows.append("本稿无人声词句与锁定画面字。")
    if card["board"].get("prompt_format") == core.SHOOTING_PROMPT and len(card["segments"]) > 1:
        rows.extend(["", "## 段界衔接", ""])
        segments = card["board"]["segments"]
        for before, after in zip(segments, segments[1:]):
            rows.extend([f'### {before["id"]} → {after["id"]}', "",
                         f'接法 {after["seam"]} · {after["join"]}', "",
                         f'上段末态　{before["ending"]}', f'下段首态　{after["opening"]}', ""])
    if card["board"].get("prompt_format") == core.SHOOTING_PROMPT and card["references"]:
        rows.extend(["", "草案中的拟制素材按编号预留；定稿时核对选定的真实版本。"])
    for part in card["segments"]:
        rows.extend(["", f'## {part["id"]} · 有效 {core.sec(part["keep_ms"])} 秒', "",
                     *("    " + row for row in part["prompt"].split("\n"))])
        if part.get("post_voices"):
            rows.extend(["", "后期旁白轨（段内时间）", "", *(core.voice_row(cue) for cue in part["post_voices"])])
    rows.extend(["", "方案卡摘要 " + card["hash"], ""])
    return "\n".join(rows)


def set_plan(s, data):
    # Preserve old takes only when the execution configuration also remains unchanged.
    board = dict(data["board"], prompt_format=core.SHOOTING_PROMPT)
    previous_execution = s.get("board", s.get("planned_board", {})).get("execution")
    core.change(s, dict(op="story", data=data["story"]))
    if previous_execution != data["board"].get("execution"):
        s.pop("take_cache", None)
    s.pop("plan_ok", None)
    s.pop("draft_choices", None)
    s.pop("batch_mode", None)
    s.pop("failed_clips", None)
    s["ref_plan"] = core.validate_ref_plan(s, data.get("references", []))
    result = data["review"]
    require(result["verdict"] in {"pass", "changes", "unknown"} and isinstance(result["findings"], list),
            "文案审查需要判定、问题和依据")
    required(result, "basis")
    require(result["verdict"] != "pass" or not any(f.get("blocking", True) for f in result["findings"]),
            "请处理阻塞的文案问题")
    s["review"] = result
    core.validate_board(s, board, planned=True)
    s["planned_board"] = board
    plan_card(s)


def selected_inputs(s, selections):
    plan = s.get("ref_plan", {})
    require(isinstance(selections, dict) and set(selections) == set(plan), "请为每项参考资产选择一份实际候选")
    shadow = dict(s, ref_picks=dict(selections), board=s["planned_board"])
    for rid, cid in selections.items():
        cand = s.get("ref_candidates", {}).get(cid, {})
        require(cand.get("ref_id") == rid and cand["spec_hash"] == core.ref_request(s, rid)["spec_hash"],
                "候选不属于本项或已失效：" + rid)
        core.verify_candidate(s, cand)
    core.validate_board(shadow, shadow["board"])
    return shadow


def production_card(s, selections, edits=None):
    require(s.get("plan_ok") and s["plan_ok"]["hash"] == plan_card(s)["hash"], "需要先确认当前方案")
    shadow = selected_inputs(s, selections)
    parts = core.compile_prompts(shadow)
    by_id = core.index(parts)
    shots = core.index(shadow["board"]["segments"])
    lines, titles = core.index(s["story"]["lines"]), core.index(s["story"]["captions"])
    for pid, text in (edits or {}).items():
        require(pid in by_id and isinstance(text, str) and text.strip(), "修改的段号或全文无效")
        core.check_edit(by_id[pid]["prompt"], text)
        core.verify_prompt_copy(shots[pid], text, lines, titles, not by_id[pid].get("post_voices"))
        by_id[pid]["prompt"] = text
    pics = {rid: {"candidate_id": cid, "sha256": s["ref_candidates"][cid]["sha256"],
                  "uri": s["ref_candidates"][cid].get("snapshot_uri", s["ref_candidates"][cid]["uri"])}
            for rid, cid in selections.items()}
    for rid, item in pics.items():
        meta = core.project_assets(shadow)[rid]
        item.update({k: meta[k] for k in ("label", "use", "speaker", "voice", "provider", "duration_s") if k in meta})
    card = dict(title="素材与提示词确认单", plan_hash=s["plan_ok"]["hash"], references=pics, segments=parts)
    return dict(card, hash=digest(card))


def activate(s, selections, mode, message, edits=None):
    require(mode in {"export", "render"}, "请选择仅导出或按这版生成")
    card = production_card(s, selections, edits)
    for rid, item in card["references"].items():
        core.change(s, dict(op="ref_pick", user_message=message, data=dict(
            ref_id=rid, candidate_id=item["candidate_id"], sha256=item["sha256"])))
    core.change(s, dict(op="board", data=s["planned_board"]))
    core.change(s, dict(op="approve", stage="board", hash=digest(s["board"]), user_message=message))
    # The on-screen card is authoritative, including any visual edits carried by this request.
    s.pop("manual_prompts", None)
    core.change(s, dict(op="compile"))
    for pid, text in (edits or {}).items():
        core.change(s, dict(op="edit_prompt", data=dict(id=pid, prompt=text)))
    # Manual edits participate in the cache key just like compiled shots.
    for part in s["prompts"]:
        cached = s.get("take_cache", {}).get(digest(part))
        parent = part["previous_frame_from"]
        if cached and (not parent or cached.get("used_frame") == s["clips"].get(parent, {}).get("last_frame")):
            s["clips"][part["id"]] = dict(cached)
    require(s["prompts"] == card["segments"], "实际编译与已展示的本批输入不一致")
    core.change(s, dict(op="approve", stage="prompts", hash=digest(s["prompts"]), user_message=message))
    if mode == "render":
        core.change(s, dict(op="render_authorize", hash=digest(s["prompts"]), user_message=message))
    s["batch_mode"] = mode
    s["active_card_hash"] = card["hash"]
    s.pop("failed_clips", None)
    if mode == "render":
        runtime.ensure(s)["segment_approvals"] = {}
        s["attempts"] = {p["id"]: s.get("attempts", {}).get(p["id"], 0) + 1 for p in s["prompts"]}


def assembly_card(s):
    require(s.get("batch_mode") == "render", "需要已生成的分段")
    clips = []
    for segment in s["board"]["segments"]:
        pid = segment["id"]
        clip = s["clips"].get(pid)
        require(bool(clip), "待取得通过审查的片段 " + pid)
        clips.append(dict(id=pid, task_id=clip["task_id"], uri=clip["uri"], attempt=clip["attempt"],
                          keep_ms=segment["keep_ms"], seam=segment["seam"], join=segment.get("join", "")))
        post_titles = core.index(s["prompts"])[pid].get("post_titles")
        if post_titles:
            clips[-1]["post_titles"] = post_titles
        if core.audio_route(s, segment) == "post_voiceover":
            track = s.get("post_tracks", {}).get(pid, {})
            clips[-1]["post_track"] = track if track.get("task_id") == clip["task_id"] else None
    card = dict(batch=s["active_card_hash"], clips=clips)
    return dict(card, hash=digest(card))


def authorize_assembly(s, card_hash, message):
    card = assembly_card(s)
    require(all(s["clips"][p["id"]].get("accepted") for p in card["clips"]), "仍有片段待采用")
    require(all("post_track" not in p or p["post_track"] for p in card["clips"]), "当前段的后期旁白轨尚未登记")
    require(card_hash == card["hash"], "合成清单已变，请展示当前片段再确认")
    s["assembly_ok"] = dict(hash=card_hash, user_message=message)


def legacy_dispatch(s):
    brief = s.get("brief", {})
    director = core.DIRECTORS.get(brief.get("type"))
    def node(stage, action, skills, **extra):
        return dict(stage=stage, action=action, director=director, skills=skills, **extra)
    if not brief or brief["missing"] and not s.get("planned_board"):
        return node(1, "补充影响效果的缺项" if brief else "判断目标与缺口", ["video-intake"])
    if not s.get("plan_ok"):
        return node(2, "审改当前方案" if s.get("planned_board") else "编写分段方案",
                    [director, "video-story", "video-copy-review", "video-board", "video-prompt", "video-references"])
    if not s.get("batch_mode"):
        return node(3, "准备素材并展示本批投模全文", ["video-references", "video-prompt"],
                    reference_ids=list(s.get("ref_plan", {})))
    if s["batch_mode"] == "export":
        return node(3, "提示词已定稿，可导出", ["video-prompt"])
    if s.get("final"):
        return node(4, "交付成片及实际提示词", ["video-finish"])
    ready, waiting, review = [], [], []
    failed = s.get("failed_clips", {})
    for part in s["prompts"]:
        pid, parent = part["id"], part["previous_frame_from"]
        clip = s["clips"].get(pid)
        if clip:
            if not clip.get("accepted"):
                review.append(pid)
        elif pid in failed:
            continue
        elif parent and not s["clips"].get(parent, {}).get("accepted"):
            waiting.append(pid)
        else:
            ready.append(pid)
    action = "生成或选段"
    if not (ready or waiting or review or failed):
        action = "所有段已采用，等待确认合成"
        if s.get("assembly_ok", {}).get("hash") == assembly_card(s)["hash"]:
            action = "已确认合成当前片段"
    return node(4, action,
                ["video-finish"], ready=ready, waiting_for_parent=waiting, awaiting_selection=review, needs_repair=list(failed))


def dispatch(s):
    return runtime.dispatch(s, legacy_dispatch(s))


def revise_input(s, pid, data, message):
    part = core.index(s["prompts"])[pid]
    old = part["prompt"]
    new = data.get("prompt", old)
    if new != old:
        core.check_edit(old, new)
        # Custom client prompts can carry locked lines inline instead of in dedicated rows.
        shot = core.index(s["board"]["segments"])[pid]
        lines, titles = core.index(s["story"]["lines"]), core.index(s["story"]["captions"])
        cursor = 0
        for cue in shot["cues"]:
            line = lines[cue["id"]]["text"]
            pos = new.find(line, cursor)
            require(pos >= 0, "正式台词有改动，请回到分镜脚本确认单")
            cursor = pos + len(line)
        for cue in shot["titles"]:
            row = titles[cue["id"]]
            require(row.get("render") == "post" or (lines[row["line_id"]]["text"] if "line_id" in row else row["text"]) in new, "锁定画面字有变")
        part["prompt"] = new
        signature = digest(s["prompts"])
        s["authorized"] = signature
        s["approved"]["prompts"] = dict(hash=signature, message=message)
    runtime.ensure(s).get("segment_approvals", {}).pop(pid, None)
    s.setdefault("repair_notes", {})[pid] = dict(reason=message, categories=data.get("categories", []),
                                                timecode=data.get("timecode"), previous_prompt=old, changed=new != old)


def change(s, e):
    op, data = e["op"], e.get("data", {})
    if op in repairs.OPS:
        repairs.change(s, e)
        return
    if op in runtime.RUNTIME_OPS:
        runtime.change(s, e)
        return
    runtime.before_business(s, e)
    if op in {"brief", "plan", "revise_ref", "start_batch", "retry_clip"}:
        s.pop("assembly_ok", None)
    if op == "brief":
        core.change(s, e)
        for key in ("planned_board", "brief_ok", "plan_ok", "batch_mode"):
            s.pop(key, None)
    elif op == "confirm_brief":
        required(e, "hash", "user_message")
        require(e["hash"] == brief_card(s)["hash"], "需求单已变化，请展示当前时长和分段")
        s["brief_ok"] = dict(hash=e["hash"], user_message=e["user_message"])
    elif op == "plan":
        set_plan(s, data)
    elif op == "approve_plan":
        required(e, "user_message")
        require(s.get("plan_ok", {}).get("hash") != e.get("hash") and e.get("hash") == plan_card(s)["hash"], "方案已确认或版本已变，请重新查看")
        require(s["review"]["verdict"] == "pass", "文案尚待处理")
        mode = e.get("mode", "prepare")
        require(mode == "prepare" or (not s["ref_plan"] and mode in {"render", "export"}),
                "新制或待选的参考图需先展示再授权")
        core.change(s, dict(op="approve", stage="story", hash=digest(s["story"]), user_message=e["user_message"]))
        s["plan_ok"] = dict(hash=e["hash"], user_message=e["user_message"])
        s["brief_ok"] = dict(hash=brief_card(s)["hash"], user_message=e["user_message"])
        if mode != "prepare":
            activate(s, {}, mode, e["user_message"])
    elif op == "revise_ref":
        required(e, "user_message")
        require(s.get("plan_ok") and e.get("hash") == plan_card(s)["hash"], "请定位当前方案中的图")
        required(data, "id")
        rid, item = data["id"], dict(s["ref_plan"][data["id"]])
        require(item["method"] != "provided" or data.get("base_candidate") or (item["use"] == "audio" and data.get("sample_text")), "编辑客户原图需绑定真实底图")
        if item["use"] == "audio":
            require(not data.get("base_candidate"), "样音改版请换声线或样音短句")
            updates = {k: data[k] for k in ("voice", "provider", "sample_text", "duration_s") if k in data}
            require(bool(updates), "提供样音改动")
            if item["method"] == "provided":
                item.update(method="generate", source_ids=[])
            item.update(updates)
        else:
            required(data, "prompt")
            item["prompt"] = data["prompt"]
        if data.get("base_candidate"):
            item.update(method="edit", base_candidate=data["base_candidate"])
        plan = dict(s["ref_plan"], **{rid: item})
        core.change(s, dict(op="ref_plan", data=dict(items=list(plan.values()))))
        s["plan_ok"] = dict(hash=plan_card(s)["hash"], user_message=e["user_message"])
        s.pop("batch_mode", None)
        s.pop("failed_clips", None)
        s.setdefault("draft_choices", {}).pop(rid, None)
    elif op == "ref_choice":
        required(e, "user_message")
        require(s.get("plan_ok") and not s.get("batch_mode"), "当前未处在待选参考资产状态")
        rid, cid = data["ref_id"], data["candidate_id"]
        cand = s.get("ref_candidates", {}).get(cid, {})
        require(cand.get("ref_id") == rid and cand["sha256"] == data.get("sha256") and
                cand["spec_hash"] == core.ref_request(s, rid)["spec_hash"], "指定图片版本已变化")
        core.verify_candidate(s, cand)
        s.setdefault("draft_choices", {})[rid] = cid
    elif op == "ref_candidate":
        require(s.get("plan_ok"), "方案通过后才能登记候选")
        core.change(s, e)
    elif op == "start_batch":
        required(e, "user_message")
        choices = data.get("selections", s.get("draft_choices", {}))
        edits = data.get("edits", {})
        card = production_card(s, choices, edits)
        require(e.get("hash") == card["hash"], "选图或提示词版本已变，先展示当前版")
        require(not (s.get("batch_mode") == "render" and s.get("active_card_hash") == card["hash"]),
                "这批输入已定稿，请查看逐段确认、在途任务或指定返修")
        activate(s, choices, e["mode"], e["user_message"], edits)
    elif op == "accept_batch":
        required(e, "user_message")
        clips = data.get("clips")
        require(isinstance(clips, list) and clips and len({p["id"] for p in clips}) == len(clips), "请选择明确的片段版本")
        for p in clips:
            core.change(s, dict(op="accept_clip", data=p, user_message=e["user_message"]))
        if "assembly_hash" in data:
            authorize_assembly(s, data["assembly_hash"], e["user_message"])
    elif op == "post_track":
        pid = data["id"]
        part = core.index(s["prompts"])[pid]
        require(part.get("audio_route") == "post_voiceover", "本段未选后期旁白")
        runtime.assert_no_open(s, {pid})
        for k in ("uri", "sha256", "evidence", "task_id"):
            required(data, k)
        require(s.get("clips", {}).get(pid, {}).get("task_id") == data["task_id"], "后期轨需对应当前样片")
        core.verify_voice_file(data["uri"], data["sha256"], data["duration_s"])
        require(0 < data["duration_s"] <= part["keep_ms"]/1000 + 0.001, "后期旁白轨超出有效段时长")
        s.setdefault("post_tracks", {})[pid] = dict(data)
        s.pop("assembly_ok", None)
        s.update(final=None, delivered=False)
    elif op == "authorize_assembly":
        required(e, "user_message", "hash")
        runtime.assert_fresh(s, before="N08")
        runtime.assert_no_open(s)
        authorize_assembly(s, e["hash"], e["user_message"])
    elif op == "revise_segment":
        required(e, "user_message")
        pid = data["id"]
        runtime.segment_card(s, pid)
        require(isinstance(data.get("prompt"), str) and data["prompt"].strip(), "提供本段编辑后的全文")
        revise_input(s, pid, data, e["user_message"])
    elif op == "retry_clip":
        required(e, "user_message")
        pid = data["id"]
        require(not any(k in data for k in ("from_ms", "to_ms", "region")), "指定时段或画面区域请用 repair_plan，整段重生成另行确认")
        target = s["clips"].get(pid) or s.get("failed_clips", {}).get(pid, {})
        require(s.get("batch_mode") == "render" and target.get("task_id") == data["task_id"],
                "返修目标片已过期")
        parts = core.index(s["prompts"])
        affected = {pid}
        for part in s["prompts"]:
            if part["previous_frame_from"] in affected:
                affected.add(part["id"])
        for aid in affected:
            s["attempts"][aid] += 1
            s.get("failed_clips", {}).pop(aid, None)
            s.get("take_cache", {}).pop(digest(parts[aid]), None)
        core.invalidate_clip(s, pid)
        revise_input(s, pid, data, e["user_message"])
    elif op == "clip":
        require(data.get("attempt") == s.get("attempts", {}).get(data["id"]) and data.get("attempt") is not None,
                "片段回执来自旧生成批次")
        existing = s.get("clips", {}).get(data["id"]) or s.get("failed_clips", {}).get(data["id"])
        if existing:
            prev = {k:v for k,v in existing.items() if k != "accepted"}
            require(prev == data, "该段已有回执；请先发起指定版本返修")
            return
        qa = data.get("qa", {})
        if not qa or any(qa.get(k) == "unknown" for k in ("visual", "speech", "sync", "text", "seam", "duration")):
            required(data, "task_id", "uri", "prompt_hash")
            if qa:
                required(qa, "evidence")
            req = runtime.video_input(s, data["id"])
            require(req["prompt_hash"] == data["prompt_hash"], "待复核回执不是当前输入")
            s.setdefault("unreviewed_clips", {})[data["id"]] = data
        elif any(qa.get(k) == "fail" for k in ("visual","speech","sync","text","seam","duration")):
            pid = data["id"]
            req = runtime.video_input(s, pid)
            required(data, "task_id", "uri")
            required(qa, "evidence")
            require(req["prompt_hash"] == data.get("prompt_hash"), "失败回执不是当前输入")
            s.setdefault("failed_clips", {})[pid] = data
        else:
            core.change(s, e)
    elif op == "finish":
        require(s.get("batch_mode") == "render", "先生成并选定实际片段")
        core.change(s, e)
    else:
        raise ValueError("未知的四步工作流事件 " + op)


def transaction(db, event):
    db.execute("BEGIN IMMEDIATE")
    try:
        rev, state = latest(db)
        require(event.get("expected") == rev, "版本已变化，请刷新当前卡片")
        # Identical brief submissions are no-ops and preserve later approvals and output versions.
        if state is not None and event["op"] == "brief" and "brief" in state and event.get("data") == state["brief"]:
            db.commit()
            return rev, state
        if state is None:
            require(event["op"] == "init", "先创建项目")
            required(event, "idea")
            state = dict(idea=event["idea"], approved={}, clips={}, delivered=False)
            r = runtime.ensure(state)
            r["name"] = event.get("name", "视频项目")
        else:
            runtime.ensure(state)
            change(state, event)
        runtime.record_outputs(state, event, rev + 1)
        db.execute("INSERT INTO four_events VALUES(?,?,?)", (rev + 1, json.dumps(state, ensure_ascii=False),
                    json.dumps(event, ensure_ascii=False)))
        db.commit()
        return rev + 1, state
    except Exception:
        db.rollback()
        raise


def latest(db):
    row = db.execute("SELECT rev,value FROM four_events ORDER BY rev DESC LIMIT 1").fetchone()
    return (row[0], json.loads(row[1])) if row else (0, None)


def video_request(s, pid):
    _, req = runtime.can_submit(s, "video", pid)
    return req


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db")
    parser.add_argument("action", choices=["apply","status","dispatch","brief-card","segment-card","segment-text","repair-card","repair-text","claim-repair-source","claim-repair","audio-request","claim-audio","plan-card","prepare-card","image-request",
                                           "media-request","compose-request","assembly-card","assembly-request","export","draft-export","status-card","summary","artifact",
                                           "skill-diff","claim-video","claim-image","claim-compose","env-check"])
    parser.add_argument("argument", nargs="?")
    parser.add_argument("--expected", type=int)
    parser.add_argument("--output", help="draft-export 的新 Markdown 路径；不覆盖已存在文件")
    parser.add_argument("--install", action="store_true", help="env-check: 经确认安装可选本地依赖")
    parser.add_argument("--yes", action="store_true", help="env-check: 跳过安装逐项确认")
    args = parser.parse_args()
    require(not args.output or args.action == "draft-export", "--output 仅用于 draft-export")
    # env-check 只读环境，不要求项目数据库存在
    if args.action == "env-check":
        sys.exit(env_check.run_checks(install=args.install, yes=args.yes))
    writing = args.action == "apply" or args.action.startswith("claim-")
    if writing:
        db = sqlite3.connect(args.db)
        db.execute("CREATE TABLE IF NOT EXISTS four_events(rev INTEGER PRIMARY KEY,value TEXT,event TEXT)")
    else:
        path = Path(args.db).resolve()
        require(path.is_file(), "项目数据库不存在；只读查询不会自动创建空项目")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    with db:
        if args.action == "apply":
            rev, s = transaction(db, json.loads(Path(args.argument).read_text()))
        elif args.action.startswith("claim-"):
            rev, s = latest(db)
            require(s is not None and args.expected == rev, "先读取当前 revision，再原子登记任务")
            kind = args.action.split("-", 1)[1]
            identifier = "final" if kind == "compose" else args.argument
            key, req = runtime.can_submit(s, kind, identifier)
            rev, s = transaction(db, {"op": "job_submit", "expected": rev,
                                     "data": {"kind": kind, "id": identifier, "request_hash": digest(req)}})
            print(json.dumps({"revision": rev, "key": key, "request": req,
                              "notice": "仅登记已领取；宿主必须用这一请求提交一次并回写真实回执"}, ensure_ascii=False, indent=2))
            return
        else:
            rev, s = latest(db)
        require(s is not None, "项目为空")
        if args.action == "draft-export":
            content = draft_text(s)
            if args.output:
                path = Path(args.output)
                require(path.suffix.lower() == ".md", "草案请使用 Markdown 文件")
                with path.open("x", encoding="utf-8") as dest:
                    dest.write(content)
                print(str(path.resolve()))
            else:
                sys.stdout.write(content)
            return
        if args.action == "brief-card":
            out = brief_card(s)
        elif args.action == "repair-card":
            out = repairs.card(s, args.argument)
        elif args.action == "repair-text":
            print(repairs.text(s, args.argument))
            return
        elif args.action == "segment-text":
            print(runtime.segment_text(s, args.argument))
            return
        elif args.action == "segment-card":
            out = runtime.segment_card(s, args.argument)
        elif args.action == "plan-card":
            out = plan_card(s)
        elif args.action == "prepare-card":
            config = json.loads(Path(args.argument).read_text()) if args.argument else {}
            out = production_card(s, config.get("selections", s.get("draft_choices", {})), config.get("edits", {}))
        elif args.action in {"image-request", "audio-request"}:
            _, out = runtime.can_submit(s, args.action.split("-")[0], args.argument)
        elif args.action == "media-request":
            out = video_request(s, args.argument)
        elif args.action in {"compose-request", "assembly-request"}:
            _, out = runtime.can_submit(s, "compose", "final")
        elif args.action == "assembly-card":
            out = assembly_card(s)
        elif args.action == "export":
            require(s.get("batch_mode") and core.approved(s, "prompts"), "需要先定稿")
            out = s["prompts"]
        elif args.action == "dispatch":
            out = dict(revision=rev, **dispatch(s))
        elif args.action in {"status-card", "summary"}:
            print(state_card.render(s, rev, dispatch(s)))
            return
        elif args.action == "skill-diff":
            out = state_card.skill_diff(s)
        elif args.action == "artifact":
            require(args.argument in runtime.NODE_NAMES, "指定 N01–N08 的节点")
            out = dict(node=args.argument, output=runtime.ensure(s)["outputs"].get(args.argument),
                       files=runtime.ensure(s)["artifacts"].get(args.argument, []))
        else:
            out = dict(revision=rev, next=dispatch(s), state=s)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    db.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, sqlite3.Error, OSError) as error:
        sys.exit("未推进：" + str(error))
