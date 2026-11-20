"""Read-only human-readable project summary. The database remains authoritative."""
from __future__ import annotations

from pathlib import Path
import hashlib

import runtime_state as rt

LABELS = {"not_started": "未开始", "draft": "草案已产出", "ready": "可继续", "running": "执行中",
          "waiting": "等待确认/材料", "failed": "失败待处理", "completed": "已完成", "stale": "待更新"}
JOB_LABELS = {"submitting": "已领取，提交结果待回写", "submitted": "已提交", "running": "生成中",
              "submission_unknown": "提交结果不明，只能查原任务", "succeeded": "生成完成",
              "failed": "生成失败", "cancel_requested": "已请求取消，未确认取消", "cancelled": "已确认取消"}


def skill_diff(s):
    r = rt.ensure(s)
    current, adopted = rt.fingerprint(), r["adopted_rules"]
    old, new = adopted.get("files", {}), current["files"]
    changed = [p for p in sorted(set(old) | set(new)) if old.get(p) != new.get(p)]
    return {"changed": bool(changed), "current_hash": current["hash"], "adopted_hash": adopted["hash"],
            "version": current["version"], "files": changed,
            "affected_nodes": [n for n in rt.NODE_NAMES if adopted.get("nodes", {}).get(n) != current["nodes"][n]]}


def node_status(s, node, flow):
    r = rt.ensure(s)
    activity = r["activity"].get(node)
    if activity:
        return activity["status"], activity.get("reason", "")
    if node in r["stale"]:
        return "stale", "上游或规则已变，旧产物保留但不能作为新结果使用"
    if node == "N01":
        return ("completed", "需求已登记") if s.get("brief") else ("not_started", "尚未整理需求")
    if node == "N02":
        if not s.get("brief"):
            return "not_started", "等待需求"
        missing = s["brief"].get("missing", [])
        pending = [rid for rid in s.get("ref_plan", {}) if rid not in s.get("ref_picks", {})]
        if missing or pending:
            return "waiting", "待补/待选：" + "、".join(missing + pending)
        return "completed", "当前登记素材无待补项；实际能力以工具核验为准"
    if node in {"N03", "N04", "N05"}:
        output = r["outputs"].get(node)
        if not output:
            return "not_started", "等待上游产物"
        if s.get("review", {}).get("verdict") != "pass":
            return "waiting", "同版文案审查尚未通过"
        if s.get("plan_ok") and (node != "N05" or s.get("batch_mode")):
            return "completed", "当前版已采用"
        return "draft", "产物已保存，尚未完成当前版确认或素材绑定"
    if node == "N06":
        if flow.get("awaiting_segment_approval"):
            return "waiting", "逐段提示词待交付或等待本次回复：" + "、".join(flow["awaiting_segment_approval"])
        if s.get("batch_mode"):
            return "completed", "素材与参数已定稿，生成按段确认" if s["batch_mode"] == "render" else "已确认仅导出，不生成视频"
        return ("waiting", "等待真实输入与生成参数确认") if s.get("planned_board") else ("not_started", "等待方案")
    if node == "N07":
        if flow.get("confirmation_blocked"):
            return "waiting", flow["action"]
        if flow.get("pending_repairs"):
            return "waiting", flow["action"]
        if s.get("batch_mode") != "render":
            return "not_started", "未授权生成" if s.get("batch_mode") != "export" else "本项目仅导出提示词"
        if any(j["kind"] == "video" for j in rt.open_jobs(s)):
            return "running", "以真实任务回执为准，无回执不猜百分比"
        if flow.get("needs_repair"):
            return "failed", "待处理：" + "、".join(flow["needs_repair"])
        if flow.get("awaiting_review") or flow.get("awaiting_selection") or flow.get("awaiting_receipt"):
            return "waiting", "片段已生成，等待复核或采用"
        if s.get("prompts") and all(s.get("clips", {}).get(p["id"], {}).get("accepted") for p in s["prompts"]):
            return "completed", "所有片段已采用；合成需明确授权"
        if flow.get("awaiting_segment_approval") or flow.get("input_issues"):
            return "waiting", flow["action"]
        return "ready", "可提交就绪片段；续接片段等待上段采用"
    if s.get("delivered"):
        return "completed", "最新成片已通过交付通道呈现"
    if s.get("final"):
        return "ready", "成片已登记，待实际交付"
    job = rt.current_job(s, "compose", "final")
    if job:
        status = "failed" if job["status"] in {"failed", "cancelled"} else "running"
        return status, JOB_LABELS[job["status"]]
    if s.get("prompts") and all(s.get("clips", {}).get(p["id"], {}).get("accepted") for p in s["prompts"]):
        from workflow import assembly_card
        authorized = s.get("assembly_ok", {}).get("hash") == assembly_card(s)["hash"]
        return ("ready", "当前清单已获合成授权") if authorized else ("waiting", "全部段已采用，等待明确同意合成")
    return "not_started", "等待全部片段采用和真实合成结果"


def safe(text):
    return str(text).replace("\n", " ").replace("\r", " ").replace("`", "ˋ").replace("|", "／")


def render(s, revision, flow):
    r = rt.ensure(s)
    states = {n: node_status(s, n, flow) for n in rt.NODE_NAMES}
    current = next((n for n, (status, _) in states.items() if status != "completed"), "N08")
    lines = [f"**项目进度｜{safe(r['name'])}｜记录版本 r{revision}**",
             f"**当前：{'已暂停；' if r['paused'] else ''}{rt.NODE_NAMES[current]} · {LABELS[states[current][0]]}**"]
    if skill_diff(s)["changed"]:
        lines.append("**规则提醒：Skill 已修改，尚未采用新版本；新提交已阻止。**")
    if r.get("legacy_unverified"):
        lines.append("**卡点：旧项目没有完整提交台账，须先核对原任务；不得直接补生成。**")
    lines += [f"- {n} {name}：{LABELS[states[n][0]]}。{safe(states[n][1])}" for n, name in rt.NODE_NAMES.items()]
    files = [(n, rows[-1]) for n, rows in r["artifacts"].items() if rows]
    lines.append("\n**中间产物**")
    saved = [f"{n}（r{v['revision']}，{'待更新' if n in r['stale'] else '已保存'}）" for n, v in sorted(r["outputs"].items())]
    lines.append("- " + ("结构化产物：" + "、".join(saved) + "；可查看对应节点。" if saved else "尚无结构化产物。"))
    for node, item in sorted(files):
        path = Path(item["path"])
        latest = r["outputs"].get(node, {})
        outdated = node in r["stale"] or (item.get("output_revision") is not None and item["output_revision"] != latest.get("revision"))
        state = "旧版文件，保留供查看" if outdated else ("文件不在当前环境" if not path.is_file() else (
            "文件已改变，需重新登记" if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"] else "已登记"))
        lines.append(f"- {node} {safe(item['label'])}：{state}；位置 `{safe(item['path'])}`")
    jobs = [j for j in r["jobs"].values() if j["status"] in rt.OPEN_JOBS]
    if jobs:
        lines.append("\n**在途任务**")
        for job in jobs:
            percent = f" · 工具返回 {job['progress']}%" if job.get("progress") is not None else ""
            lines.append(f"- {safe(job['id'])}：{JOB_LABELS[job['status']]}{percent}")
    lines += ["\n**下一步**", "- " + safe(flow["action"]),
              "- 可说：查看进度／查看 N04 产物／暂停／继续；改某一段或 Skill 时先明确改动范围。",
              "\n状态来自项目数据库；这不是实时网页或后台守护进程。历史消息不原地刷新。"]
    return "\n".join(lines)
