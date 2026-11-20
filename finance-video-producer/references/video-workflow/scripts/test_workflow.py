"""Deterministic offline regression tests. No real generation or provider calls."""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow as wf
import runtime_state as rt
import state_card
from engine import digest

QA = {"visual": "pass", "speech": "pass", "sync": "na", "text": "na", "seam": "pass",
      "duration": "pass", "evidence": "OFFLINE MOCK ONLY — not video quality evidence"}


def plan_fixture():
    segments = []
    for i in range(3):
        segments.append({"id": f"S0{i+1}", "keep_ms": 5000, "opening": "模拟起点", "subjects": "模拟方块",
                         "ending": "模拟末态", "seam": "opening" if i == 0 else ("first_frame" if i == 1 else "cut"),
                         "join": "模拟接法", "shots": [{"end_ms": 5000, "visual": "方块从左移到右"}],
                         "asset_ids": [], "cues": [], "titles": []})
    return {"story": {"synopsis": "离线测试无词片", "duration_ms": 15000, "lines": [], "captions": []},
            "review": {"verdict": "pass", "findings": [], "basis": "OFFLINE MOCK"}, "references": [],
            "board": {"ratio": "9:16", "max_seconds": 15, "segments": segments,
                      "execution": {"tool": "offline_mock", "model": "offline_mock", "verified": True,
                                    "media_roles": ["first_frame", "reference_audio", "reference_image", "reference_video"], "min_seconds": 5, "max_seconds": 15}}}


def policy_folder_fixture():
    plan = plan_fixture()
    references = [
        ("M", "妈妈定妆", "role", "锁定妈妈的脸型、发型、服装"),
        ("F", "爸爸定妆", "role", "锁定爸爸的脸型、发型、服装"),
        ("P", "保单夹与护盾", "product", "锁定深蓝保单夹和金色半透明盾牌的外形与材质"),
        ("R", "开放式客餐厅", "scene", "锁定窗户、沙发、茶几与餐区的空间关系"),
    ]
    assets = [dict(id=key, label=label, use=use, reference_purpose=purpose, uri=f"asset://fixture/{key}-v1")
              for key, label, use, purpose in references]
    words = "这个时候，一份重疾险的意义就体现出来了。"
    brief = dict(type="scene", people=True, speech=True, product_mode="illustrative", assets=assets, missing=[],
                 exact_copy=[dict(line_id="VO", text=words)])
    plan["story"].update(synopsis="从夜间的账单切到白天的保单与护盾。", duration_ms=12000, speech_complete=True,
                         lines=[dict(id="VO", mode="vo", lang="zh-CN", speaker="女讲解者", text=words)])
    night = plan["board"]["segments"][0]
    night.update(subjects="夜晚看账单", opening="昏暗客厅，父母坐在茶几后低头看账单。",
                 ending="固定双人中景，账单停在茶几中央。", asset_ids=["M", "F", "R"],
                 shots=[dict(end_ms=5000, visual="妈妈坐在画面左侧，爸爸坐在右侧。两人低头看摊开的账单。")])
    day = dict(id="S02", keep_ms=7000, seam="cut",
               join="从夜晚账单画面硬切至同户型白天客厅。沿用父母定妆和左右坐位，茶几上改为深蓝保单夹。",
               subjects="观众要看见重疾险从一份具体保单进入这个家。",
               opening="竖屏 3D 家庭动画。连续单镜头，镜头在沙发对面，从茶几桌沿高度起拍。白天，敞开的窗帘让阳光斜照茶几。妈妈坐画面左侧，爸爸坐画面右侧。两人略显紧绷，视线落向桌面。深蓝保单夹合拢，放在茶几左侧、靠妈妈手边。护盾尚未出现。",
               shots=[
                   dict(end_ms=1800, visual="妈妈用右手把保单夹沿桌面推到中央。保单夹停稳，妈妈的右手留在夹子左侧。镜头开始平稳抬升。"),
                   dict(end_ms=3600, visual="保单夹停稳后，爸爸用左手翻开封面，底夹始终贴着桌面。镜头继续抬升，把打开的保单夹和父母的上半身同时纳入画面。"),
                   dict(end_ms=5500, visual="封面打开后，淡金光点从内页上方聚拢成一面半透明护盾。护盾垂直升起，悬在打开的保单夹正上方。父母的目光随护盾抬起，表情从紧绷转为平静。"),
                   dict(end_ms=7000, visual="镜头停在双人中景。两人的手停在保单夹两侧，少量光点沿盾缘缓慢绕行，阳光透过盾面。"),
               ],
               ending="最后一帧同时看清打开的深蓝保单夹、悬在其正上方的透明金盾和两人的脸。",
               locks="保持妈妈在左、爸爸在右。金盾亮度柔和，位于两张脸之间偏下方。桌上只有一个保单夹。画面无字幕、品牌标识和可读条款。",
               asset_ids=["M", "F", "P", "R"], cues=[dict(id="VO", from_ms=1000, to_ms=6300)],
               audio_route="post_voiceover", titles=[])
    plan["board"]["segments"] = [night, day]
    return brief, plan


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE four_events(rev INTEGER PRIMARY KEY,value TEXT,event TEXT)")
        self.rev, self.s = wf.transaction(self.db, {"op": "init", "expected": 0, "idea": "离线测试", "name": "测试项目"})
        self.apply("brief", {"type": "showcase", "people": False, "speech": False,
                             "product_mode": "none", "assets": [], "missing": []})
        self.apply("plan", plan_fixture())

    def tearDown(self):
        self.db.close()

    def apply(self, op, data=None, **extra):
        event = {"op": op, "expected": self.rev, **extra}
        if data is not None:
            event["data"] = data
        self.rev, self.s = wf.transaction(self.db, event)
        return self.s

    def authorize(self):
        self.apply("approve_plan", hash=wf.plan_card(self.s)["hash"], user_message="模拟授权", mode="render")

    def approve_segment(self, pid="S01"):
        card = rt.segment_card(self.s, pid)
        message = f"mock-show-{self.rev}"
        self.apply("present_segment", {"id": pid, "content": rt.segment_text(self.s, pid), "message_id": message}, hash=card["hash"])
        self.apply("approve_segment", {"id": pid, "presentation_id": message, "reply_id": f"mock-reply-{self.rev}"},
                   hash=card["hash"], user_message="模拟采用本次完整输入")

    def claim(self, pid="S01", kind="video"):
        if kind == "video":
            self.approve_segment(pid)
        key, req = rt.can_submit(self.s, kind, pid)
        self.apply("job_submit", {"kind": kind, "id": pid, "request_hash": digest(req)})
        return key, req

    def complete(self, pid="S01", accepted=True, qa=QA):
        key, req = self.claim(pid)
        self.apply("job_update", {"key": key, "status": "succeeded", "task_id": "mock-task-" + pid,
                                   "uri": "mock://video/" + pid, "evidence": "OFFLINE MOCK"})
        clip = {"id": pid, "task_id": "mock-task-" + pid, "uri": "mock://video/" + pid,
                "attempt": req["attempt"], "prompt_hash": req["prompt_hash"],
                "first_frame": "mock://frame/first/" + pid, "last_frame": "mock://frame/last/" + pid,
                "qa": copy.deepcopy(qa), "sha256": "a" * 64, "last_frame_sha256": "b" * 64}
        for media in req["media"]:
            if media["role"] == "first_frame":
                clip["used_frame"] = media["uri"]
        self.apply("clip", clip)
        if accepted:
            self.apply("accept_batch", {"clips": [{"id": pid, "task_id": clip["task_id"]}]}, user_message="模拟采用")
        return key, clip

    def test_authorization_required(self):
        with self.assertRaises(ValueError):
            rt.can_submit(self.s, "video", "S01")

    def test_version_transaction_rolls_back(self):
        with self.assertRaises(ValueError):
            wf.transaction(self.db, {"op": "pause", "expected": 0, "user_message": "暂停"})
        self.assertEqual(wf.latest(self.db)[0], self.rev)

    def test_claim_prevents_repeat_and_dispatch(self):
        self.authorize()
        key, _ = self.claim()
        with self.assertRaises(ValueError):
            self.claim()
        flow = wf.dispatch(self.s)
        self.assertNotIn("S01", flow["ready"])
        self.assertIn("S01", flow["in_flight"])
        self.assertEqual(self.s["runtime"]["jobs"][key]["status"], "submitting")

    def test_pause_receipts_and_resume(self):
        self.authorize()
        key, _ = self.claim()
        self.apply("pause", user_message="暂停")
        with self.assertRaises(ValueError):
            rt.can_submit(self.s, "video", "S03")
        self.apply("job_update", {"key": key, "status": "running", "task_id": "mock-running", "evidence": "MOCK"})
        self.apply("resume", user_message="继续")
        self.approve_segment("S03")
        rt.can_submit(self.s, "video", "S03")

    def test_unknown_submission_cannot_retry(self):
        self.authorize()
        key, _ = self.claim()
        self.apply("job_update", {"key": key, "status": "submission_unknown", "evidence": "MOCK timeout"})
        with self.assertRaises(ValueError):
            self.apply("retry_job", {"key": key}, user_message="重跑")
        with self.assertRaises(ValueError):
            self.apply("job_update", {"key": key, "status": "failed", "error": "MOCK not found", "evidence": "MOCK"})
        self.apply("job_update", {"key": key, "status": "failed", "error": "MOCK not found", "evidence": "MOCK", "queried": True})
        self.apply("retry_job", {"key": key}, user_message="重跑失败任务")
        self.approve_segment()
        new_key, _ = rt.can_submit(self.s, "video", "S01")
        self.assertNotEqual(new_key, key)

    def test_task_handle_and_terminal_are_immutable(self):
        self.authorize()
        key, _ = self.claim()
        self.apply("job_update", {"key": key, "status": "submitted", "task_id": "mock-a", "evidence": "MOCK"})
        with self.assertRaises(ValueError):
            self.apply("job_update", {"key": key, "status": "running", "task_id": "mock-b", "evidence": "MOCK"})
        self.apply("job_update", {"key": key, "status": "succeeded", "uri": "mock://one", "evidence": "MOCK"})
        with self.assertRaises(ValueError):
            self.apply("job_update", {"key": key, "status": "succeeded", "uri": "mock://two", "evidence": "MOCK"})

    def test_cancel_request_is_not_cancelled(self):
        self.authorize()
        key, _ = self.claim()
        self.apply("job_update", {"key": key, "status": "cancel_requested", "task_id": "mock-a", "evidence": "MOCK"})
        with self.assertRaises(ValueError):
            self.apply("retry_job", {"key": key}, user_message="重试")
        self.apply("job_update", {"key": key, "status": "cancelled", "evidence": "MOCK confirmed"})
        self.apply("retry_job", {"key": key}, user_message="重试已取消任务")

    def test_inflight_blocks_plan_edit(self):
        self.authorize()
        self.claim()
        with self.assertRaises(ValueError):
            self.apply("plan", plan_fixture())

    def test_parent_frame_and_selective_retry(self):
        self.authorize()
        with self.assertRaises(ValueError):
            self.claim("S02")
        _, clip = self.complete("S01")
        self.approve_segment("S02")
        self.assertEqual(rt.can_submit(self.s, "video", "S02")[1]["media"][0]["uri"], clip["last_frame"])
        self.complete("S02")
        self.complete("S03")
        self.apply("retry_clip", {"id": "S01", "task_id": clip["task_id"]}, user_message="只修第一段")
        self.assertNotIn("S01", self.s["clips"])
        self.assertNotIn("S02", self.s["clips"])
        self.assertTrue(self.s["clips"]["S03"]["accepted"])

    def test_unknown_qa_stays_reviewable(self):
        self.authorize()
        qa = dict(QA, visual="unknown")
        _, clip = self.complete("S01", accepted=False, qa=qa)
        self.assertIn("S01", self.s["unreviewed_clips"])
        self.apply("review_clip", {"id": "S01", "task_id": clip["task_id"], "qa": qa})
        self.assertIn("S01", self.s["unreviewed_clips"])
        self.apply("review_clip", {"id": "S01", "task_id": clip["task_id"], "qa": QA})
        self.assertIn("S01", self.s["clips"])

    def test_rules_edit_and_rerun_can_progress(self):
        self.authorize()
        old_hash = self.s["runtime"]["adopted_rules"]["hash"]
        self.s["runtime"]["adopted_rules"]["hash"] = "old-rule-mock"
        self.db.execute("UPDATE four_events SET value=? WHERE rev=?", (json.dumps(self.s), self.rev))
        self.db.commit()
        with self.assertRaises(ValueError):
            self.claim()
        self.apply("adopt_skills", {"scope": "rerun", "nodes": ["N04"], "hash": old_hash}, user_message="新规则重跑分镜")
        self.apply("node_start", {"node": "N04"})
        self.apply("plan", plan_fixture())
        self.authorize()
        self.claim()

    def test_generic_node_rerun_clears_downstream(self):
        self.authorize()
        self.apply("rerun_node", {"node": "N05"}, user_message="只重编投模词")
        self.assertIsNone(self.s.get("batch_mode"))
        self.apply("plan", plan_fixture())
        self.authorize()
        self.claim()

    def test_execution_model_is_in_request(self):
        self.authorize()
        self.approve_segment()
        req = rt.can_submit(self.s, "video", "S01")[1]
        self.assertEqual(req["model"], "offline_mock")
        self.assertEqual(req["tool"], "offline_mock")

    def test_missing_execution_blocks_render_not_export(self):
        data = plan_fixture()
        data["board"].pop("execution")
        self.apply("plan", data)
        with self.assertRaises(ValueError):
            self.authorize()
        self.apply("approve_plan", hash=wf.plan_card(self.s)["hash"], user_message="仅导出", mode="export")

    def test_compose_and_delivery(self):
        self.authorize()
        for pid in ("S01", "S02", "S03"):
            self.complete(pid)
        with self.assertRaisesRegex(ValueError, "合成授权"):
            self.claim("final", "compose")
        self.apply("authorize_assembly", hash=wf.assembly_card(self.s)["hash"], user_message="模拟明确授权合成")
        key, _ = self.claim("final", "compose")
        self.apply("job_update", {"key": key, "status": "succeeded", "task_id": "mock-compose", "uri": "mock://final", "evidence": "MOCK"})
        self.apply("finish", {"uri": "mock://final", "tasks": ["mock-task-S01", "mock-task-S02", "mock-task-S03"], "qa": QA})
        self.apply("delivered", {"uri": "mock://final", "evidence": "OFFLINE present mock"})
        self.assertTrue(self.s["delivered"])

    def test_actual_rule_file_change_and_adoption(self):
        self.authorize()
        original = rt.ROOT
        with tempfile.TemporaryDirectory(prefix="video-rules-test-") as folder:
            copied = Path(folder) / "finance-video-producer"
            shutil.copytree(original, copied)
            rt.ROOT = copied
            try:
                rule = copied / "references/video-prompt/guide.md"
                rule.write_text(rule.read_text(encoding="utf-8") + "\n离线测试规则变更\n", encoding="utf-8")
                diff = state_card.skill_diff(self.s)
                self.assertTrue(diff["changed"])
                self.assertIn("N05", diff["affected_nodes"])
                with self.assertRaises(ValueError):
                    self.claim()
                self.apply("adopt_skills", {"scope": "future", "hash": diff["current_hash"]}, user_message="只用到后续")
                self.assertFalse(state_card.skill_diff(self.s)["changed"])
                self.claim()
            finally:
                rt.ROOT = original

    def test_model_change_does_not_reuse_old_takes(self):
        self.authorize()
        self.complete("S01")
        data = plan_fixture()
        data["board"]["execution"]["model"] = "offline_other_model"
        self.apply("plan", data)
        self.authorize()
        self.assertNotIn("S01", self.s["clips"])
        self.claim()

    def test_rerun_intake_and_assets_can_progress(self):
        self.apply("rerun_node", {"node": "N01"}, user_message="重做需求")
        self.apply("brief", {"type": "showcase", "people": False, "speech": False,
                             "product_mode": "none", "assets": [], "missing": []})
        self.apply("plan", plan_fixture())
        self.apply("rerun_node", {"node": "N02"}, user_message="重核素材")
        self.apply("plan", plan_fixture())
        self.authorize()
        self.claim()

    def test_image_lifecycle_is_tracked(self):
        data = plan_fixture()
        data["story"]["ref_needs"] = ["ROLE01"]
        data["references"] = [{"id": "ROLE01", "label": "模拟角色图", "use": "role", "purpose": "固定模拟角色",
                               "ratio": "9:16", "method": "generate", "prompt": "模拟图", "source_ids": [],
                               "segments": ["S01"]}]
        data["board"]["segments"][0]["asset_ids"] = ["ROLE01"]
        self.apply("plan", data)
        self.apply("approve_plan", hash=wf.plan_card(self.s)["hash"], user_message="模拟确认制图", mode="prepare")
        key, req = self.claim("ROLE01", "image")
        with self.assertRaises(ValueError):
            self.claim("ROLE01", "image")
        self.apply("job_update", {"key": key, "status": "succeeded", "task_id": "mock-image-task",
                                   "uri": "asset://offline-test-only", "evidence": "MOCK"})
        self.apply("ref_candidate", {"ref_id": "ROLE01", "candidate_id": "mock-image-v1", "uri": "asset://offline-test-only",
                                     "sha256": "a" * 64, "receipt": "mock-image-task", "inspection": "OFFLINE MOCK",
                                     "spec_hash": req["spec_hash"]})
        self.assertIn("mock-image-v1", self.s["ref_candidates"])

    def test_edit_brief_marks_old_outputs_stale(self):
        self.authorize()
        self.apply("brief", {"type": "showcase", "people": False, "speech": False,
                             "product_mode": "none", "assets": [], "missing": [], "intent": {"audience_goal": "已改变的目标"}})
        self.assertIn("N04", self.s["runtime"]["stale"])
        self.assertEqual(state_card.node_status(self.s, "N04", wf.dispatch(self.s))[0], "stale")
        self.apply("plan", plan_fixture())
        self.authorize()
        self.claim()

    def test_short_keep_uses_provider_minimum(self):
        data = plan_fixture()
        data["story"]["duration_ms"] = 1000
        data["board"]["segments"] = [dict(data["board"]["segments"][0], keep_ms=1000,
                                           shots=[{"end_ms": 1000, "visual": "模拟动作"}])]
        self.apply("plan", data)
        self.authorize()
        self.approve_segment()
        self.assertEqual(rt.can_submit(self.s, "video", "S01")[1]["duration_s"], 5)

    def test_legacy_requires_reconciliation(self):
        self.authorize()
        old = copy.deepcopy(self.s)
        old.pop("runtime")
        with self.assertRaises(ValueError):
            rt.can_submit(old, "video", "S01")
        self.assertTrue(old["runtime"]["legacy_unverified"])

    def test_artifact_checks_real_file_and_mutation(self):
        with tempfile.TemporaryDirectory(prefix="video-state-test-") as folder:
            path = Path(folder) / "分镜.md"
            path.write_text("离线测试分镜", encoding="utf-8")
            self.apply("register_artifact", {"node": "N04", "path": str(path), "label": "离线分镜", "env_id": "offline_test"})
            self.assertIn("已登记", state_card.render(self.s, self.rev, wf.dispatch(self.s)))
            path.write_text("被修改", encoding="utf-8")
            self.assertIn("文件已改变", state_card.render(self.s, self.rev, wf.dispatch(self.s)))

    def test_cli_read_only_and_atomic_claim(self):
        self.authorize()
        self.approve_segment()
        script = str(Path(wf.__file__))
        with tempfile.TemporaryDirectory(prefix="video-cli-test-") as folder:
            path = Path(folder) / "project.sqlite"
            target = sqlite3.connect(path)
            self.db.backup(target)
            target.close()
            def run(*args):
                return subprocess.run([sys.executable, "-B", script, str(path), *args], capture_output=True, text=True)
            self.assertEqual(run("status-card").returncode, 0)
            first = run("claim-video", "S01", "--expected", str(self.rev))
            self.assertEqual(first.returncode, 0, first.stderr)
            second = run("claim-video", "S01", "--expected", str(self.rev + 1))
            self.assertNotEqual(second.returncode, 0)
            missing = Path(folder) / "absent.sqlite"
            result = subprocess.run([sys.executable, "-B", script, str(missing), "status-card"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(missing.exists())

    def test_identical_brief_preserves_revision_and_approval(self):
        self.authorize()
        before = copy.deepcopy(self.s)
        rev = self.rev
        self.apply("brief", copy.deepcopy(self.s["brief"]))
        self.assertEqual((self.rev, self.s), (rev, before))
        self.assertEqual(wf.latest(self.db), (rev, before))

    def test_accept_and_authorize_assembly_atomically(self):
        self.authorize()
        clips = []
        for pid in ("S01", "S02", "S03"):
            _, clip = self.complete(pid, accepted=(pid != "S03"))
            clips.append({"id": pid, "task_id": clip["task_id"]})
        rev = self.rev
        with self.assertRaisesRegex(ValueError, "清单已变"):
            self.apply("accept_batch", {"clips": clips, "assembly_hash": "old"}, user_message="模拟采用并合成")
        self.assertEqual(wf.latest(self.db)[0], rev)
        self.assertFalse(wf.latest(self.db)[1]["clips"]["S03"]["accepted"])
        self.apply("accept_batch", {"clips": clips, "assembly_hash": wf.assembly_card(self.s)["hash"]},
                   user_message="模拟采用并合成")
        rt.can_submit(self.s, "compose", "final")
        self.apply("retry_clip", {"id": "S03", "task_id": "mock-task-S03"}, user_message="模拟只修第三段")
        self.assertNotIn("assembly_ok", self.s)

    def test_creative_review_is_in_rule_scope(self):
        original = rt.ROOT
        with tempfile.TemporaryDirectory(prefix="creative-rule-test-") as folder:
            copied = Path(folder) / "finance-video-producer"
            shutil.copytree(original, copied)
            rt.ROOT = copied
            try:
                rule = copied / "references/creative-review.md"
                rule.write_text(rule.read_text(encoding="utf-8") + "\n离线规则测试\n", encoding="utf-8")
                diff = state_card.skill_diff(self.s)
                self.assertTrue({"N03", "N04"} <= set(diff["affected_nodes"]))
            finally:
                rt.ROOT = original

    def test_same_prompt_new_attempt_needs_approval(self):
        self.authorize()
        first = rt.segment_card(self.s, "S01")
        with self.assertRaisesRegex(ValueError, "本段本次"):
            wf.video_request(self.s, "S01")
        with self.assertRaises(ValueError):
            self.apply("approve_segment", {"id": "S03"}, hash=first["hash"], user_message="只采用第一段")
        self.approve_segment()
        self.assertEqual(first["request"], wf.video_request(self.s, "S01"))
        _, clip = self.complete()
        self.apply("retry_clip", {"id": "S01", "task_id": clip["task_id"]}, user_message="同词再抽一次")
        second = rt.segment_card(self.s, "S01")
        self.assertEqual(first["request"]["prompt"], second["request"]["prompt"])
        self.assertNotEqual(first["hash"], second["hash"])
        with self.assertRaises(ValueError):
            self.apply("approve_segment", {"id": "S01"}, hash=first["hash"], user_message="旧卡")
        with self.assertRaises(ValueError):
            wf.video_request(self.s, "S01")

    def test_local_repair_keeps_independent_approval(self):
        self.authorize()
        self.approve_segment("S03")
        _, clip = self.complete()
        old = self.s["prompts"][0]["prompt"]
        new = old.replace("从左移到右", "从左缓缓移到右")
        self.apply("retry_clip", {"id": "S01", "task_id": clip["task_id"], "prompt": new,
                                  "categories": ["动作／手势"], "timecode": "1.2s"}, user_message="只改速度")
        card = rt.segment_card(self.s, "S01")
        self.assertTrue(card["repair"]["changed"])
        self.assertEqual(card["request"]["prompt"], new)
        self.assertEqual(card["repair"]["previous_prompt"], old)
        self.assertEqual(wf.video_request(self.s, "S03")["attempt"], 1)

    def test_brand_mute_and_ambience_routes(self):
        data = plan_fixture()
        data["board"]["segments"][0]["audio_route"] = "mute"
        self.apply("plan", data)
        self.authorize()
        self.assertFalse(rt.segment_card(self.s, "S01")["request"]["generate_audio"])
        self.assertTrue(rt.segment_card(self.s, "S03")["request"]["generate_audio"])
        self.assertEqual(wf.plan_card(self.s)["brief_card"]["spoken_chars"], 0)

    def test_budget_before_script_and_after_reading(self):
        brief = dict(self.s["brief"], speech=True, target_duration_ms=60000, silent_ms=12000,
                     spoken_zh="好" * 230, proposed_segments=[dict(id="S01", reason="话轮落音")])
        self.apply("brief", brief)
        card = wf.brief_card(self.s)
        self.assertEqual((card["budget_chars"], card["range_chars"]), (200, [192, 216]))
        self.assertEqual((card["trim_chars_at_default"], card["keep_copy_seconds"]), (30, 67.2))
        self.apply("confirm_brief", hash=card["hash"], user_message="确认总长与初分段")
        self.apply("brief", dict(brief, read_ms=51000))
        self.assertEqual(wf.brief_card(self.s)["keep_copy_seconds"], 63)
        self.assertIn("实读", wf.brief_card(self.s)["timing"])

    def test_speaker_sample_lifecycle_and_limits(self):
        import hashlib
        import wave
        self.apply("brief", dict(self.s["brief"], type="scene", people=True, speech=True))
        data = plan_fixture()
        data["story"].update(speech_complete=True, lines=[
            dict(id="L1", speaker="小宁", mode="dialogue", lang="普通话", text="等我一下。"),
            dict(id="L2", speaker="小宁", mode="dialogue", lang="普通话", text="我们到了。")])
        item = dict(id="V1", use="audio", label="小宁样音", purpose="锁定小宁音色", method="generate",
                    speaker="小宁", provider="offline", voice="fixture", sample_text="窗外有阵风。")
        data["references"] = [item]
        for idx, lid in ((0, "L1"), (2, "L2")):
            seg = data["board"]["segments"][idx]
            seg.update(asset_ids=["V1"], cues=[dict(id=lid, from_ms=100, to_ms=4800)])
        self.apply("plan", data)
        self.apply("approve_plan", mode="prepare", hash=wf.plan_card(self.s)["hash"], user_message="准备角色样音")
        with self.assertRaises(ValueError):
            rt.can_submit(self.s, "image", "V1")
        key, req = self.claim("V1", "audio")
        self.assertEqual(req["capability"], "generate_voice_sample")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.wav"
            with wave.open(str(path), "wb") as out:
                out.setparams((1, 2, 8000, 0, "NONE", ""))
                out.writeframes(bytes(80000))
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            self.apply("job_update", dict(key=key, status="succeeded", task_id="mock-voice", uri=str(path), evidence="OFFLINE MOCK"))
            self.apply("ref_candidate", dict(ref_id="V1", candidate_id="v1", uri=str(path), sha256=sha,
                       duration_s=5, receipt="mock-voice", inspection="离线测试 WAV", spec_hash=req["spec_hash"]))
            self.apply("ref_choice", dict(ref_id="V1", candidate_id="v1", sha256=sha), user_message="采用样音")
            c = wf.production_card(self.s, {"V1": "v1"})
            self.assertEqual(c["references"]["V1"]["duration_s"], 5)
            self.apply("start_batch", hash=c["hash"], mode="render", user_message="定稿")
            for pid in ("S01", "S03"):
                card = rt.segment_card(self.s, pid)
                self.assertEqual(card["assets"][0]["role"], "reference_audio")
                self.assertEqual(card["assets"][0]["sha256"], sha)
                self.assertIn("小宁 声音参考 <音频1>", card["request"]["prompt"])
                part = next(p for p in self.s["board"]["segments"] if p["id"] == pid)
                line = next(l for l in self.s["story"]["lines"] if l["id"] == part["cues"][0]["id"])
                self.assertIn(f'人声 0.1–4.8秒｜小宁｜现场对白｜普通话｜{line["text"]}', card["request"]["prompt"])
                self.assertIn("只有当前发声者做说话口型", card["request"]["prompt"])
                self.assertNotIn(item["sample_text"], card["request"]["prompt"])
            self.approve_segment()
            self.s["board"]["execution"]["max_reference_audio_s"] = 4
            with self.assertRaisesRegex(ValueError, "超出"):
                rt.segment_card(self.s, "S01")

    def test_product_image_and_video_roles(self):
        assets = [dict(id="P", label="客户水杯", use="product", uri="asset://cup-original-v1"),
                  dict(id="M", label="旋转参考", use="video", uri="asset://motion-v1")]
        self.apply("brief", dict(self.s["brief"], product_mode="exact", assets=assets))
        plan = plan_fixture()
        plan["board"]["segments"][0]["asset_ids"] = ["P", "M"]
        self.apply("plan", plan)
        self.authorize()
        self.approve_segment()
        req = wf.video_request(self.s, "S01")
        self.assertEqual([a["role"] for a in req["media"]], ["reference_image", "reference_video"])
        self.assertIn("<图片1>", req["prompt"])
        self.assertIn("<视频1>", req["prompt"])
        self.s["board"]["execution"]["tool"] = "changed"
        with self.assertRaisesRegex(ValueError, "本段本次"):
            wf.video_request(self.s, "S01")
        self.s["board"]["execution"]["media_roles"].remove("reference_video")
        with self.assertRaisesRegex(ValueError, "reference_video"):
            rt.segment_card(self.s, "S01")

    def test_post_voiceover_assembled_with_actual_track(self):
        self.apply("brief", dict(self.s["brief"], speech=True))
        data = plan_fixture()
        data["story"].update(speech_complete=True, lines=[dict(id="L1", mode="vo", lang="普通话", speaker="旁白", text="轻轻转动。")])
        data["board"]["segments"][0].update(audio_route="post_voiceover", cues=[dict(id="L1", from_ms=0, to_ms=4500)])
        self.apply("plan", data)
        self.authorize()
        self.assertFalse(rt.segment_card(self.s, "S01")["request"]["generate_audio"])
        for pid in ("S01", "S02", "S03"):
            self.complete(pid)
        with self.assertRaisesRegex(ValueError, "后期旁白轨"):
            self.apply("authorize_assembly", hash=wf.assembly_card(self.s)["hash"], user_message="合成")
        self.apply("post_track", dict(id="S01", task_id="mock-task-S01", uri="asset://offline-vo-v1", sha256="a"*64,
                                      duration_s=5, evidence="离线测试配音轨"))
        self.apply("authorize_assembly", hash=wf.assembly_card(self.s)["hash"], user_message="确认含旁白轨的合成")
        self.assertEqual(rt.can_submit(self.s, "compose", "final")[1]["post_tracks"]["S01"]["uri"], "asset://offline-vo-v1")

    def test_policy_folder_prompt_is_the_full_visual_request_and_keeps_copy_in_post(self):
        brief, plan = policy_folder_fixture()
        self.apply("brief", brief)
        self.apply("plan", plan)
        draft = wf.plan_card(self.s)["segments"][1]
        prompt = draft["prompt"]
        words = plan["story"]["lines"][0]["text"]
        day = plan["board"]["segments"][1]
        for metadata in (words, day["subjects"], day["join"]):
            self.assertNotIn(metadata, prompt)
        in_order = ("放在茶几左侧", "妈妈用右手", "保单夹停稳后，爸爸",
                    "封面打开后", "镜头停在双人中景", day["ending"])
        offsets = [prompt.index(action) for action in in_order]
        self.assertEqual(offsets, sorted(offsets))
        self.assertNotIn("0–1.8秒", prompt)
        self.assertNotIn("1.8–3.6秒", prompt)
        self.assertEqual(draft["post_voices"], [dict(id="VO", from_ms=1000, to_ms=6300,
                                                     speaker="女讲解者", mode="vo", lang="zh-CN", text=words)])
        draft_text = wf.draft_text(self.s)
        self.assertIn("1–6.3秒", draft_text)
        self.assertIn(plan["board"]["segments"][0]["ending"], draft_text)
        self.assertIn(day["join"], draft_text)
        self.assertIn(day["opening"], draft_text)
        timed = copy.deepcopy(self.s)
        timed["planned_board"]["segments"][1]["timing_required"] = True
        self.assertIn("0–1.8秒，妈妈", wf.plan_card(timed)["segments"][1]["prompt"])

        self.apply("approve_plan", hash=wf.plan_card(self.s)["hash"], user_message="模拟确认", mode="prepare")
        edits = {"S02": prompt.replace("亮度柔和", "亮度低于窗边高光")}
        prepared = wf.production_card(self.s, {}, edits)
        self.apply("start_batch", {"edits": edits}, hash=prepared["hash"], mode="render", user_message="模拟采用")
        neighbor = rt.segment_card(self.s, "S01")
        card = rt.segment_card(self.s, "S02")
        self.assertEqual([a["prompt_tag"] for a in card["assets"]], [f"<图片{i}>" for i in range(1, 5)])
        self.assertEqual([a["id"] for a in card["assets"]], day["asset_ids"])
        self.assertEqual(neighbor["assets"][2]["prompt_tag"], "<图片3>")
        self.assertEqual(neighbor["assets"][2]["uri"], card["assets"][3]["uri"])
        shown = rt.segment_text(self.s, "S02")
        self.assertIn(edits["S02"], shown)
        self.assertIn(words, shown.split("后期旁白轨（段内时间）", 1)[1])
        self.assertIn(day["join"], shown)
        self.complete("S01")
        key, clip = self.complete("S02")
        request = rt.ensure(self.s)["jobs"][key]["request"]
        self.assertEqual(request["prompt"], edits["S02"])
        self.assertEqual(request["duration_s"], 7)
        self.assertFalse(request["generate_audio"])
        self.assertNotIn("post_voices", request)
        self.assertNotIn("first_frame", [item["role"] for item in request["media"]])
        self.assertEqual([item["uri"] for item in request["media"]], [item["uri"] for item in brief["assets"]])
        self.apply("post_track", dict(id="S02", task_id=clip["task_id"], uri="asset://fixture/post-vo",
                                      sha256="a"*64, duration_s=7, evidence="OFFLINE MOCK"))
        self.apply("authorize_assembly", hash=wf.assembly_card(self.s)["hash"], user_message="模拟确认合成")
        _, assembly = rt.can_submit(self.s, "compose", "final")
        self.assertEqual(assembly["seams"], [dict(previous="S01", next="S02", kind="cut", join=day["join"],
                         last_frame=self.s["clips"]["S01"]["last_frame"], first_frame=clip["first_frame"])])

    def test_explicit_user_prompt_is_preserved_with_post_voiceover(self):
        brief, plan = policy_folder_fixture()
        supplied = "按图片顺序参考四张图。\n生成白天客厅的静音镜头。"
        plan["board"]["segments"][1]["user_prompt"] = supplied
        self.apply("brief", brief)
        self.apply("plan", plan)
        self.authorize()
        card = rt.segment_card(self.s, "S02")
        self.assertEqual(card["request"]["prompt"], supplied)
        self.assertEqual(card["post_voices"][0]["text"], plan["story"]["lines"][0]["text"])

    def test_120_second_brand_through_final_selection(self):
        self.apply("brief", dict(self.s["brief"], audio_policy="mute", target_duration_ms=120000))
        plan = plan_fixture()
        plan["story"]["duration_ms"] = 120000
        plan["board"]["segments"] = [
            dict(id=f"C{i+1:02}", opening="空台", subjects="纸灯", ending="灯光渐亮",
                 keep_ms=15000, seam="cut" if i else "opening", join="光位衔接",
                 split_reason="一盏灯的动作完成", shots=[dict(end_ms=15000, visual="纸灯转半圈")],
                 asset_ids=[], cues=[], titles=[]) for i in range(8)]
        self.apply("plan", plan)
        self.assertEqual(wf.brief_card(self.s)["segments"][-1]["to_ms"], 120000)
        self.authorize()
        for segment in plan["board"]["segments"]:
            self.complete(segment["id"])
        self.apply("authorize_assembly", hash=wf.assembly_card(self.s)["hash"], user_message="合成离线样例")
        req = rt.can_submit(self.s, "compose", "final")[1]
        self.assertEqual(sum(p["keep_ms"] for p in req["clips"]), 120000)

    def test_missing_or_wrong_speaker_is_caught_in_plan(self):
        self.apply("brief", dict(self.s["brief"], people=True, speech=True, type="scene", assets=[
            dict(id="B", speaker="乙", use="audio", label="乙样音", uri="asset://offline-b")]))
        plan = plan_fixture()
        plan["story"].update(speech_complete=True, lines=[dict(id="L", speaker="甲", mode="dialogue", lang="普通话", text="请进。")])
        part = plan["board"]["segments"][0]
        part["cues"] = [dict(id="L", from_ms=100, to_ms=2800)]
        with self.assertRaisesRegex(ValueError, "缺少本段"):
            self.apply("plan", plan)
        part["asset_ids"] = ["B"]
        with self.assertRaisesRegex(ValueError, "实际发声者"):
            self.apply("plan", plan)

    def test_upgrade_recovers_inflight_job_without_new_approval(self):
        self.authorize()
        # Simulate a v2.1 immutable in-flight request. It had neither a route nor a segment approval.
        old = copy.deepcopy(self.s)
        for p in old["prompts"]:
            p.pop("audio_route", None)
        old["authorized"] = digest(old["prompts"])
        old["approved"]["prompts"]["hash"] = old["authorized"]
        req = rt.base_request(old, "video", "S01")
        self.assertNotIn("audio_route", req)
        key = rt.task_key(old, "video", "S01", req)
        old["runtime"]["jobs"][key] = dict(key=key, id="S01", kind="video", status="submission_unknown",
                                           attempt=1, request=req, task_id=None)
        with self.assertRaises(ValueError):
            rt.segment_card(old, "S01")
        rt.change(old, dict(op="job_update", data=dict(key=key, status="succeeded", task_id="original-task",
                                                      uri="mock://old-result", evidence="OFFLINE queried", queried=True)))
        self.assertEqual(rt.current_job(old, "video", "S01")["task_id"], "original-task")

    def test_post_titles_stay_out_of_model_prompt(self):
        plan = plan_fixture()
        plan["story"]["captions"] = [dict(id="T", text="鎏金杯", render="post", placement="右下")]
        plan["board"]["segments"][0]["titles"] = [dict(id="T", from_ms=1000, to_ms=4000)]
        self.apply("plan", plan)
        self.authorize()
        card = rt.segment_card(self.s, "S01")
        self.assertNotIn("鎏金杯", card["request"]["prompt"])
        self.assertEqual(card["post_titles"][0]["text"], "鎏金杯")
        for pid in ("S01", "S02", "S03"):
            self.complete(pid)
        self.apply("authorize_assembly", hash=wf.assembly_card(self.s)["hash"], user_message="确认含后期排字")
        self.assertEqual(rt.can_submit(self.s, "compose", "final")[1]["post_titles"]["S01"][0]["to_ms"], 4000)

    def repair_plan(self, method="video_edit"):
        data = dict(id="S01", task_id=self.s["clips"]["S01"]["task_id"], source_sha256="a"*64,
                    method=method, from_ms=1000, to_ms=2000, problem="指定位置仍有瑕疵",
                    desired="该位置完整清晰", preserve=["其余画面", "原声", "总时长"],
                    execution=dict(tool="offline_editor", model="offline", verified=True,
                                   schema_evidence="OFFLINE fixture schema",
                                   controls=["source_video", "time_range", method, "region"]),
                    assets=[])
        if method == "video_edit":
            data.update(region=[0.2, 0.3, 0.1, 0.2], frame_evidence="原视频 1500ms 帧",
                        assets=[dict(uri="asset://offline-source-frame", sha256="c"*64, role="source_frame", purpose="定位瑕疵", at_ms=1500)])
        else:
            data.update(audio_component="mixed", audition_evidence="回听段内 1–2 秒")
        return data

    def approve_repair(self):
        import repairs
        c = repairs.card(self.s, "S01")
        message = f"mock-repair-show-{self.rev}"
        self.apply("present_repair", dict(id="S01", message_id=message, content=repairs.text(self.s, "S01")), hash=c["hash"])
        self.apply("approve_repair", dict(id="S01", presentation_id=message, reply_id=f"mock-reply-{self.rev}"),
                   hash=c["hash"], user_message="确认指定区域和完成标准")
        return c

    def finish_repair(self, ending_changed=False, status="pass"):
        key, req = self.claim("S01", "repair")
        self.apply("job_update", dict(key=key, status="succeeded", task_id="mock-edit-1", uri="asset://edited-video",
                                      evidence="OFFLINE edit receipt"))
        checks = {name: dict(status=status, observed="OFFLINE synthetic observation", evidence="OFFLINE before/after comparison") for name in ("target", "preservation", "boundaries")}
        result = dict(id="S01", task_id="mock-edit-1", uri="asset://edited-video", sha256="d"*64,
                      first_frame="asset://edited-first", last_frame="asset://edited-last",
                      last_frame_sha256=("e" if ending_changed else "b")*64, ending_changed=ending_changed,
                      media_type="video", duration_ms=5000, qa=QA, checks=checks)
        self.apply("repair_result", result)
        return result

    def test_confirmation_needs_actual_presentation_record(self):
        self.authorize()
        c = rt.segment_card(self.s, "S01")
        with self.assertRaises(ValueError):
            self.apply("approve_segment", dict(id="S01", presentation_id="missing", reply_id="reply"),
                       hash=c["hash"], user_message="生成")
        with self.assertRaises(ValueError):
            self.apply("present_segment", dict(id="S01", message_id="m1", content="摘要而非全文"), hash=c["hash"])
        self.apply("present_segment", dict(id="S01", message_id="m1", content=rt.segment_text(self.s, "S01")), hash=c["hash"])
        with self.assertRaises(ValueError):
            self.apply("approve_segment", dict(id="S01", presentation_id="m1", reply_id="m1"), hash=c["hash"], user_message="生成")

    def test_new_presentation_invalidates_previous_reply(self):
        self.authorize()
        self.approve_segment()
        c = rt.segment_card(self.s, "S01")
        self.apply("present_segment", dict(id="S01", message_id="new-message", content=rt.segment_text(self.s, "S01")), hash=c["hash"])
        with self.assertRaises(ValueError):
            wf.video_request(self.s, "S01")

    def test_scoped_repair_requires_actual_edit_capability(self):
        self.authorize()
        self.complete()
        data = self.repair_plan()
        data["execution"]["controls"] = ["source_video", "time_range"]
        with self.assertRaises(ValueError):
            self.apply("repair_plan", data, user_message="只修这里")
        data = self.repair_plan()
        data["region"] = [0.9, 0.1, 0.3, 0.1]
        with self.assertRaises(ValueError):
            self.apply("repair_plan", data, user_message="指定区域")
        data = self.repair_plan()
        data["to_ms"] = 6000
        with self.assertRaises(ValueError):
            self.apply("repair_plan", data, user_message="指定秒点")

    def test_targeted_request_has_original_region_and_verbatim_lines(self):
        self.authorize()
        self.complete()
        self.apply("repair_plan", self.repair_plan(), user_message="修 1–2 秒局部")
        with self.assertRaises(ValueError):
            rt.can_submit(self.s, "repair", "S01")
        card = self.approve_repair()
        key, req = self.claim("S01", "repair")
        self.assertEqual(req, card["request"])
        self.assertEqual(req["source"]["task_id"], "mock-task-S01")
        self.assertEqual((req["from_ms"], req["to_ms"], req["region"]), (1000, 2000, [0.2, 0.3, 0.1, 0.2]))
        self.apply("job_update", dict(key=key, status="submission_unknown", evidence="MOCK timeout"))
        with self.assertRaises(ValueError):
            self.claim("S01", "repair")
        with self.assertRaises(ValueError):
            self.apply("cancel_repair", dict(id="S01"), user_message="先取消")

    def test_repair_target_not_fixed_keeps_original(self):
        self.authorize()
        self.complete()
        self.apply("repair_plan", self.repair_plan(), user_message="修局部")
        self.approve_repair()
        result = self.finish_repair(status="fail")
        with self.assertRaises(ValueError):
            self.apply("accept_repair", dict(id="S01", task_id=result["task_id"]), hash=digest(result), user_message="采用")
        self.assertEqual(self.s["clips"]["S01"]["task_id"], "mock-task-S01")
        self.assertTrue(self.s["clips"]["S01"]["accepted"])
        self.assertIn("S01", wf.dispatch(self.s)["pending_repairs"])

    def test_audio_repair_preserves_downstream_frames(self):
        self.authorize()
        self.complete()
        self.complete("S02")
        self.apply("repair_plan", self.repair_plan("audio_edit"), user_message="修指定声音")
        self.approve_repair()
        result = self.finish_repair()
        self.apply("accept_repair", dict(id="S01", task_id=result["task_id"]), hash=digest(result), user_message="回听后采用")
        self.assertEqual(self.s["clips"]["S01"]["last_frame"], "mock://frame/last/S01")
        self.assertTrue(self.s["clips"]["S02"]["accepted"])
        self.assertEqual(self.s["clips"]["S01"]["task_id"], "mock-edit-1")

    def test_edit_changed_end_frame_invalidates_dependent_clip(self):
        self.authorize()
        for pid in ("S01", "S02", "S03"):
            self.complete(pid)
        self.apply("repair_plan", self.repair_plan(), user_message="修画面")
        self.approve_repair()
        result = self.finish_repair(ending_changed=True)
        self.apply("accept_repair", dict(id="S01", task_id=result["task_id"]), hash=digest(result), user_message="采用")
        self.assertNotIn("S02", self.s["clips"])
        self.assertTrue(self.s["clips"]["S03"]["accepted"])

    def test_mixed_audio_needs_stems_for_speech_only_edit(self):
        self.authorize()
        self.complete()
        data = self.repair_plan("audio_edit")
        data["audio_component"] = "speech"
        with self.assertRaisesRegex(ValueError, "分轨"):
            self.apply("repair_plan", data, user_message="只改人声")
        data["separated_track"] = True
        data["assets"] = [dict(uri="asset://mock-speech-stem", sha256="f"*64, role="speech_track", purpose="实际人声轨")]
        self.apply("repair_plan", data, user_message="使用已分离人声轨")

    def pronunciation_plan(self):
        self.apply("brief", dict(self.s["brief"], speech=True))
        plan = plan_fixture()
        plan["story"].update(speech_complete=True, lines=[
            dict(id="L1", mode="vo", lang="普通话", speaker="旁白", text="遥墙机场直接进贵宾厅。")])
        plan["board"]["segments"][0]["cues"] = [dict(id="L1", from_ms=500, to_ms=4500)]
        self.apply("plan", plan)
        self.authorize()
        self.complete(qa=dict(QA, sync="pass"))
        self.complete("S02")
        data = self.repair_plan("audio_edit")
        data["execution"]["controls"].append("audio_splice")
        data["pronunciation"] = dict(cue_id="L1", term="遥墙机场", observed="墙读成挡", expected="yáo qiáng jī chǎng")
        return data

    def register_pronunciation_result(self, key, request):
        self.apply("job_update", dict(key=key, status="succeeded", task_id="mock-splice", uri="asset://spliced",
                                      evidence="OFFLINE actual tool callback fixture"))
        proof = dict(source_sha256=request["source"]["sha256"], replacement_sha256=request["replacement"]["sha256"],
                     output_sha256="d"*64, source_video_sha256="e"*64, output_video_sha256="e"*64,
                     source_duration_ms=5100, duration_ms=5100, from_ms=1000, to_ms=2000,
                     replacement_from_ms=request["replacement"]["from_ms"],
                     replacement_to_ms=request["replacement"]["to_ms"])
        checks = {name: dict(status="pass", observed="OFFLINE repaired target", evidence="OFFLINE comparison")
                  for name in ("target", "preservation", "boundaries")}
        result = dict(id="S01", task_id="mock-splice", uri="asset://spliced", sha256="d"*64,
                      first_frame="mock://frame/first/S01", last_frame="mock://frame/last/S01",
                      last_frame_sha256="b"*64, ending_changed=False, media_type="video",
                      duration_ms=5100, qa=dict(QA, sync="pass"), checks=checks, audio_splice=proof)
        wrong = dict(result, audio_splice=dict(proof, output_video_sha256="f"*64))
        with self.assertRaisesRegex(ValueError, "视频流"):
            self.apply("repair_result", wrong)
        self.apply("repair_result", result)
        return result

    def test_pronunciation_import_route_never_claims_seedance(self):
        data = self.pronunciation_plan()
        data["replacement"] = dict(uri="asset://user-wav", sha256="c"*64, media_type="audio",
                                   from_ms=0, to_ms=970)
        with self.assertRaisesRegex(ValueError, "选择"):
            self.apply("repair_plan", data, user_message="墙字念错了")
        data["pronunciation_route"] = "import_audio"
        self.apply("repair_plan", data, user_message="用我传的音频")
        with self.assertRaisesRegex(ValueError, "确认"):
            self.claim("S01", "repair")
        self.approve_repair()
        with self.assertRaisesRegex(ValueError, "只有已选 Seedance"):
            self.claim("S01", "repair-source")
        key, request = self.claim("S01", "repair")
        self.assertEqual(request["replacement"]["uri"], "asset://user-wav")
        self.assertNotIn("seedance_request", request)
        result = self.register_pronunciation_result(key, request)
        self.assertEqual(self.s["clips"]["S01"]["task_id"], "mock-task-S01")
        self.apply("accept_repair", dict(id="S01", task_id=result["task_id"]), hash=digest(result), user_message="试听通过，采用")
        self.assertEqual(self.s["clips"]["S01"]["uri"], "asset://spliced")
        self.assertTrue(self.s["clips"]["S02"]["accepted"])

    def test_pronunciation_seedance_candidate_must_be_spliced(self):
        data = self.pronunciation_plan()
        data.update(pronunciation_route="seedance",
                    seedance=dict(tool="offline", model="Seedance 2.5", verified=True,
                                  schema_evidence="OFFLINE schema fixture",
                                  controls=["reference_video", "video_edit", "generate_audio"],
                                  min_ms=4000, max_ms=30000,
                                  source=dict(uri="asset://source-context", sha256="f"*64, from_ms=400, to_ms=5000)))
        self.apply("repair_plan", data, user_message="走 Seedance")
        with self.assertRaisesRegex(ValueError, "确认"):
            self.claim("S01", "repair-source")
        self.approve_repair()
        with self.assertRaisesRegex(ValueError, "Seedance 候选"):
            self.claim("S01", "repair")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.sqlite"
            with sqlite3.connect(path) as target:
                self.db.backup(target)
            claim = subprocess.check_output([sys.executable, "-B", wf.__file__, str(path),
                                             "claim-repair-source", "S01", "--expected", str(self.rev)], text=True)
            self.assertIn("遥墙机场", json.loads(claim)["request"]["prompt"])
        key, request = self.claim("S01", "repair-source")
        self.assertEqual(request["media"][0]["role"], "reference_video")
        self.assertIn("[600, 1600)", request["prompt"])
        self.apply("job_update", dict(key=key, status="submission_unknown", evidence="OFFLINE timeout"))
        with self.assertRaises(ValueError):
            self.claim("S01", "repair-source")
        self.apply("job_update", dict(key=key, status="succeeded", task_id="mock-seedance",
                                      uri="asset://from-seedance", evidence="OFFLINE original query", queried=True))
        patch = dict(uri="asset://from-seedance", sha256="c"*64, media_type="video", from_ms=625, to_ms=1625,
                     evidence="OFFLINE detected revised phrase")
        with self.assertRaisesRegex(ValueError, "本次 Seedance 返回"):
            self.apply("repair_patch", dict(id="S01", task_id="mock-seedance", replacement=dict(patch, uri="asset://old-take")))
        with self.assertRaisesRegex(ValueError, "回执"):
            self.apply("repair_result", dict(id="S01", task_id="mock-seedance", uri=patch["uri"]))
        self.apply("repair_patch", dict(id="S01", task_id="mock-seedance", replacement=patch))
        key, request = self.claim("S01", "repair")
        self.assertEqual(request["replacement"], patch)
        self.assertEqual(request["source"]["task_id"], "mock-task-S01")
        result = self.register_pronunciation_result(key, request)
        self.apply("accept_repair", dict(id="S01", task_id=result["task_id"]), hash=digest(result), user_message="采用修音后的原片")
        self.assertEqual(self.s["clips"]["S01"]["repair_request"]["replacement"], patch)

    def test_cli_full_text_and_scoped_repair_request(self):
        self.authorize()
        script = str(Path(wf.__file__))
        with tempfile.TemporaryDirectory(prefix="scoped-repair-cli-") as folder:
            path = Path(folder) / "project.sqlite"
            with sqlite3.connect(path) as target:
                self.db.backup(target)
            shown = subprocess.run([sys.executable, "-B", script, str(path), "segment-text", "S01"],
                                   capture_output=True, text=True, check=True)
            self.assertIn(self.s["prompts"][0]["prompt"], shown.stdout)
            with sqlite3.connect(path) as target:
                self.assertEqual(wf.latest(target)[0], self.rev)
            self.complete()
            self.apply("repair_plan", self.repair_plan(), user_message="修指定区域")
            self.approve_repair()
            with sqlite3.connect(path) as target:
                self.db.backup(target)
            claim = subprocess.run([sys.executable, "-B", script, str(path), "claim-repair", "S01", "--expected", str(self.rev)],
                                   capture_output=True, text=True, check=True)
            request = json.loads(claim.stdout)["request"]
            self.assertEqual(request["capability"], "video_edit")
            self.assertEqual(request["source"]["task_id"], "mock-task-S01")
            repeat = subprocess.run([sys.executable, "-B", script, str(path), "claim-repair", "S01", "--expected", str(self.rev+1)],
                                    capture_output=True, text=True)
            self.assertNotEqual(repeat.returncode, 0)

    def test_image_and_voice_without_reference_video(self):
        assets = [dict(id="R", label="角色参考图", use="role", uri="asset://offline-role", sha256="a"*64),
                  dict(id="V", label="甲样音", use="audio", speaker="甲", provider="offline", voice="fixture",
                       duration_s=5, uri="asset://offline-voice", sha256="b"*64)]
        self.apply("brief", dict(self.s["brief"], type="scene", people=True, speech=True, assets=assets))
        plan = plan_fixture()
        plan["story"].update(speech_complete=True, lines=[dict(id="L1", speaker="甲", mode="dialogue", lang="普通话", text="欢迎回来。")])
        plan["board"]["segments"][0].update(asset_ids=["R", "V"], cues=[dict(id="L1", from_ms=500, to_ms=4500)])
        plan["board"]["execution"]["tool"] = "media_to_video"
        self.apply("plan", plan)
        self.authorize()
        card = rt.segment_card(self.s, "S01")
        self.assertEqual([m["role"] for m in card["request"]["media"]], ["reference_image", "reference_audio"])
        self.assertNotIn("<视频1>", card["request"]["prompt"])
        self.assertIn("本次不使用参考视频", rt.segment_text(self.s, "S01"))
        self.approve_segment()
        self.assertEqual(wf.video_request(self.s, "S01")["tool"], "media_to_video")

    def test_real_confirmation_blocker_prevents_claim(self):
        self.authorize()
        self.approve_segment()
        self.apply("node_failed", dict(node="N06", reason="S01 当前批次实际执行确认未取得；尚未提交"))
        self.assertIn("实际执行确认未取得", wf.dispatch(self.s)["action"])
        self.assertEqual(wf.dispatch(self.s)["ready"], [])
        self.assertTrue(wf.dispatch(self.s)["confirmation_blocked"])
        self.assertEqual(state_card.node_status(self.s, "N07", wf.dispatch(self.s))[0], "waiting")
        self.assertIn("实际执行确认未取得", state_card.render(self.s, self.rev, wf.dispatch(self.s)))
        self.assertIn("本次投模全文", rt.segment_text(self.s, "S01"))
        with self.assertRaisesRegex(ValueError, "N06 生成确认已挂起"):
            wf.video_request(self.s, "S01")
        self.assertFalse(rt.ensure(self.s)["jobs"])
        # OFFLINE only: simulate an actual host confirmation, not proof of quota.
        self.apply("node_start", dict(node="N06", reason="OFFLINE 模拟取得当前批次实际执行确认"))
        wf.video_request(self.s, "S01")

    def test_cost_note_is_not_an_estimate_or_generation_approval(self):
        self.authorize()
        text = rt.segment_text(self.s, "S01")
        self.assertIn("消耗与执行确认", text)
        self.assertIn("视频生成为高消耗生成", text)
        self.assertIn("具体消耗预估仅作可选信息", text)
        self.assertIn("不额外挂起", text)
        self.assertIn("不编造数值或写成 0", text)
        with self.assertRaises(ValueError):
            wf.video_request(self.s, "S01")
        self.approve_segment()
        # No estimate value or quota type exists; local submission readiness still works.
        self.assertEqual(wf.video_request(self.s, "S01")["tool"], "offline_mock")

    def test_optional_estimate_policy_is_consistent(self):
        import env_check
        caps = dict(env_check.HOST_CAPABILITIES)
        self.assertIn("可选消耗信息", caps["video_token_compute"])
        self.assertIn("不附加挂起", caps["video_token_compute"])
        host = (rt.ROOT / "references/video-workflow/references/host.md").read_text()
        self.assertIn("消耗预估是可选信息", host)
        self.assertIn("不单独登记", host)
        self.assertIn("不按文字关键词自动解除旧失败记录", host)
        self.assertNotIn("当前豆包内置交互合同明确规定预估不可用", host)
        self.authorize()
        self.approve_segment()
        self.assertEqual(wf.dispatch(self.s)["ready"], ["S01"])
        key, req = rt.can_submit(self.s, "video", "S01")
        self.apply("job_submit", dict(kind="video", id="S01", request_hash=digest(req)))
        self.assertEqual(rt.ensure(self.s)["jobs"][key]["status"], "submitting")
        with self.assertRaises(ValueError):
            rt.can_submit(self.s, "video", "S01")

    def test_legacy_estimate_blocker_resumes_without_numeric_estimate(self):
        self.authorize()
        self.approve_segment()
        self.apply("node_failed", dict(node="N06", reason="旧记录：消耗预估仅返回付费提醒；尚未提交"))
        with self.assertRaisesRegex(ValueError, "N06 生成确认已挂起"):
            wf.video_request(self.s, "S01")
        # Review old receipts and obtain current host confirmation before recovery.
        # Do not automatically clear arbitrary failures by matching the reason text.
        self.apply("node_start", dict(node="N06", reason="OFFLINE 模拟核对无在途任务并取得本批确认；取消本包预估硬门槛"))
        self.assertEqual(wf.dispatch(self.s)["ready"], ["S01"])
        self.assertEqual(wf.video_request(self.s, "S01")["tool"], "offline_mock")
        self.assertFalse(rt.ensure(self.s)["jobs"])

    def test_host_capability_list_does_not_require_three_modalities(self):
        import env_check
        caps = dict(env_check.HOST_CAPABILITIES)
        self.assertIn("可无参考视频", caps["media_to_video"])
        self.assertIn("非三模态必填", caps["media_to_video"])
        self.assertIn("video_token_compute", caps)
        self.assertIn("text_to_video", caps)

    def test_legacy_board_retains_legacy_prompt_format(self):
        # A saved old board is not silently converted by compile/query operations.
        state = copy.deepcopy(self.s)
        state["board"] = copy.deepcopy(state["planned_board"])
        state["board"].pop("prompt_format", None)
        state["board"]["segments"][0]["timing_required"] = True
        parts = wf.core.compile_prompts(state)
        self.assertIn("一、输入与零秒状态", parts[0]["prompt"])
        self.assertIn("五、一致性与禁区", parts[0]["prompt"])
        self.assertNotIn("prompt_format", state["board"])

    def test_shooting_continuation_binds_actual_selected_end_frame(self):
        self.authorize()
        self.complete("S01")
        card = rt.segment_card(self.s, "S02")
        self.assertEqual(card["join"]["kind"], "first_frame")
        media = [m for m in card["request"]["media"] if m["role"] == "first_frame"]
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]["uri"], self.s["clips"]["S01"]["last_frame"])
        self.assertIn("以提交的上一段有效末帧为本段首帧", card["request"]["prompt"])
        self.assertNotIn("```", rt.segment_text(self.s, "S02"))

    def test_shooting_presentation_keeps_exact_post_copy_separate(self):
        brief, plan = policy_folder_fixture()
        self.apply("brief", brief)
        self.apply("plan", plan)
        self.authorize()
        card = rt.segment_card(self.s, "S02")
        words = plan["story"]["lines"][0]["text"]
        self.assertNotIn(words, card["request"]["prompt"])
        self.assertEqual(card["post_voices"][0]["text"], words)
        text = rt.segment_text(self.s, "S02")
        self.assertIn("消耗与执行确认", text)
        self.assertIn("本次不使用参考视频", text)
        self.assertIn(card["request"]["prompt"], text)
        self.assertNotIn("```", text)

    def test_snapshot_reopen_and_human_card(self):
        self.authorize()
        self.apply("pause", user_message="暂停")
        other = sqlite3.connect(":memory:")
        self.db.backup(other)
        rev, s = wf.latest(other)
        self.assertEqual((rev, s), (self.rev, self.s))
        card = state_card.render(s, rev, wf.dispatch(s))
        self.assertIn("已暂停", card)
        self.assertIn("中间产物", card)
        self.assertNotIn("mock-task", card)
        other.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
