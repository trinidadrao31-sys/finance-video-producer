#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finance-video-producer 环境自检 + 可选安装（技能内置脚本）

两种调用方式:
  独立运行:
    python3 env_check.py              # 只检查，不安装
    python3 env_check.py --install    # 经确认后安装可选依赖(edge-tts); ffmpeg 只给命令不代提权
    python3 env_check.py --install --yes
  经状态机入口:
    python3 workflow.py <db> env-check [--install] [--yes]
    （env-check 只读环境，不要求项目数据库存在）

检查范围:
  A. 本地运行时: Python3、标准库、ffmpeg/ffprobe、edge-tts(可选中文TTS)、网络
  B. 豆包工作自带能力(宿主会话提供，本地脚本只能列清单，不能探测或代装)
  C. 技能包目录与脚本完整性 + 随包自测
退出码: 0 必需项就绪; 1 有必需项缺失; 2 检查过程出错
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import urllib.request

# ---- 颜色 ----
def c(text, code):
    return f"\033[{code}m{text}\033[0m"
OK = lambda t: c(t, "32")
WARN = lambda t: c(t, "33")
BAD = lambda t: c(t, "31")
INFO = lambda t: c(t, "36")

REQUIRED_STDLIB = ["json", "sqlite3", "hashlib", "pathlib", "re", "math", "argparse", "subprocess"]

# 技能包关键相对路径（相对技能根目录）
SKILL_KEY_FILES = [
    "SKILL.md",
    "references/video-workflow/scripts/workflow.py",
    "references/video-workflow/scripts/engine.py",
    "references/video-workflow/scripts/runtime_state.py",
    "references/video-workflow/scripts/state_card.py",
    "references/video-finish/scripts/splice_audio.py",
]

# 豆包侧能力清单: 本地无法探测真实挂载，只做说明，由用户在会话内核对
HOST_CAPABILITIES = [
    ("image_gen", "图片生成(文生图): 角色/场景/物料定妆图"),
    ("image_edit", "图片编辑(图生图): 参考实拍或旧图重绘"),
    ("text_to_video", "文生视频: 只有文字且无必需媒体时按需使用"),
    ("image_to_video", "图生视频: 本次媒体输入只有图片时按需使用"),
    ("media_to_video", "媒体到视频: 有音频或视频输入时按需使用; 图片+样音可无参考视频, 非三模态必填; 以真实 schema 为准"),
    ("video_token_compute", "可选消耗信息: 按用户或实际宿主要求查询; 只有付费提醒、失败或缺数值不附加挂起; 保留高消耗提醒与本批生成确认"),
    ("FileBatchUpload", "本地文件上传取 CDN 句柄(图片/音频/成片)"),
    ("present_files", "音频/文档等非图像视频文件交付; 图片和视频使用宿主 RichMediaCard"),
]

results = []  # (类别, 名称, 状态, 说明, 修复建议)

def record(cat, name, status, note, fix=""):
    results.append((cat, name, status, note, fix))

def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"

# ---------- A. 本地运行时 ----------
def check_python():
    v = sys.version_info
    note = f"{sys.version.split()[0]} ({sys.executable})"
    ok = v >= (3, 8)
    record("本地", "Python 3.8+", "pass" if ok else "fail", note,
           "安装 Python 3.8 及以上; macOS 可用 brew install python@3.12" if not ok else "")

def check_stdlib():
    missing = [m for m in REQUIRED_STDLIB if importlib.util.find_spec(m) is None]
    if missing:
        record("本地", "Python 标准库", "fail", "缺失: " + ", ".join(missing),
               "标准库随 Python 自带; 重装官方 Python 发行版")
    else:
        record("本地", "Python 标准库", "pass", f"{len(REQUIRED_STDLIB)} 个模块可用: " + ", ".join(REQUIRED_STDLIB))

def check_ffmpeg():
    for bin_name in ["ffmpeg", "ffprobe"]:
        path = shutil.which(bin_name)
        if not path:
            record("本地", bin_name, "fail", "未找到可执行文件",
                   "Ubuntu/Debian: sudo apt install ffmpeg; macOS: brew install ffmpeg; "
                   "Windows: 下载 https://www.gyan.dev/ffmpeg/builds/ 并加入 PATH")
            continue
        rc, out = run([bin_name, "-version"])
        ver = out.splitlines()[0] if out else ""
        record("本地", bin_name, "pass" if rc == 0 else "warn", f"{path} | {ver[:80]}")

def check_edge_tts():
    if importlib.util.find_spec("edge_tts") is None:
        record("本地", "edge-tts(可选中文 TTS 样音)", "warn", "未安装; 样音锚定路线需要它生成角色样音",
               "pip install edge-tts  (或 python3 -m pip install edge-tts)")
        return
    try:
        import edge_tts  # noqa
        ver = getattr(edge_tts, "__version__", "unknown")
        record("本地", "edge-tts(可选中文 TTS 样音)", "pass", f"版本 {ver}")
    except Exception as e:  # noqa
        record("本地", "edge-tts(可选中文 TTS 样音)", "warn", f"导入异常: {e}", "pip install --upgrade edge-tts")

def check_network():
    # 仅探测连通性; CDN 对 HEAD 可能直接 405/403, 故用 GET 只读小字节, 能连上即算可达
    targets = ["https://aka.doubaocdn.com", "https://speech.platform.bing.com"]
    ok_all, notes = True, []
    for url in targets:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "env-check"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                notes.append(url + f" 可达(HTTP {resp.status})")
        except urllib.error.HTTPError as e:
            notes.append(url + f" 网络通(HTTP {e.code})")
        except Exception as e:  # noqa
            notes.append(url + " 不可达(" + type(e).__name__ + ")")
            ok_all = False
    record("本地", "网络连通(下载素材/TTS/CDN)", "pass" if ok_all else "warn", "; ".join(notes),
           "若内网受限, 需开通素材 CDN 与 TTS 域名访问")

# ---------- C. 技能包完整性 ----------
def check_skill_package():
    # 脚本位于 references/video-workflow/scripts/ 下，技能根目录为上溯三层；
    # 兼容独立分发包布局(tools/env-check 旁)与已安装目录。
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.abspath(os.path.join(here, "..", "..", "..")),
        os.path.expanduser("~/.doubao/agent_mode/workspace/.user_skills/finance-video-producer"),
        os.path.abspath(os.path.join(here, "..", "..", "finance-video-producer")),
        os.path.abspath(os.path.join(here, "..", "..")),
    ]
    root = next((c for c in candidates if os.path.exists(os.path.join(c, "SKILL.md"))), None)
    if not root:
        record("技能包", "finance-video-producer R3", "fail", "未定位到技能根目录",
               "确认技能已安装到 user_skills/finance-video-producer")
        return
    missing = [f for f in SKILL_KEY_FILES if not os.path.exists(os.path.join(root, f))]
    if missing:
        record("技能包", "finance-video-producer R3", "fail", "关键文件缺失: " + ", ".join(missing),
               "从技能分发包补齐缺失文件")
    else:
        record("技能包", "finance-video-producer R3", "pass", f"根目录: {root}; {len(SKILL_KEY_FILES)} 个关键文件齐全")
    test_file = os.path.join(root, "references/video-workflow/scripts/test_workflow.py")
    if os.path.exists(test_file):
        rc, out = run([sys.executable, test_file], timeout=60)
        record("技能包", "workflow.py 随包自测", "pass" if rc == 0 else "fail",
               "test_workflow.py 通过" if rc == 0 else out[-300:],
               "查看脚本输出定位事件合同/状态机问题")

# ---------- 安装 ----------
def ask_yes_no(question, default=False):
    if ARGS.yes:
        return True
    hint = " [Y/n] " if default else " [y/N] "
    ans = input(question + hint).strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes", "是", "好")

def install_edge_tts():
    rc, out = run([sys.executable, "-m", "pip", "install", "edge-tts"], timeout=180)
    return rc == 0, out

def install_ffmpeg_advice():
    if shutil.which("brew"):
        return ["brew", "install", "ffmpeg"], "macOS(brew)"
    if shutil.which("apt-get"):
        return ["sudo", "apt-get", "update && sudo apt-get", "install", "-y", "ffmpeg"], "Debian/Ubuntu"
    return None, "Windows 请手动下载 ffmpeg 并加入 PATH"

def apply_installs():
    print(INFO("\n=== 可选安装处理 ==="))
    need_tts = any(r[1].startswith("edge-tts") and r[2] in ("warn", "fail") for r in results)
    if need_tts and ask_yes_no("安装可选中文 TTS 引擎 edge-tts?"):
        ok, out = install_edge_tts()
        print(("edge-tts 安装完成" if ok else "edge-tts 安装失败:\n" + out))
    need_ff = any(r[1] in ("ffmpeg", "ffprobe") and r[2] == "fail" for r in results)
    if need_ff:
        cmd, platform = install_ffmpeg_advice()
        print(WARN(f"ffmpeg/ffprobe 缺失({platform}); 需你在终端执行, 脚本不代为提权:"))
        print("  " + (" ".join(cmd) if cmd else "手动安装 ffmpeg 并加入 PATH"))

def print_report():
    print(INFO("\n=== 环境自检报告 ==="))
    groups = {}
    for cat, name, status, note, fix in results:
        groups.setdefault(cat, []).append((name, status, note, fix))
    for cat, items in groups.items():
        print(f"\n## {cat}")
        for name, status, note, fix in items:
            mark = {"pass": OK("[就绪]"), "warn": WARN("[可选/提醒]"), "fail": BAD("[缺失]")}[status]
            print(f"  {mark} {name}: {note}")
            if fix and status != "pass":
                print(f"        修复: {fix}")
    print("\n## 豆包工作自带能力(本地脚本无法探测, 请在会话内核对已挂载)")
    for cap, desc in HOST_CAPABILITIES:
        print(f"  · {cap}: {desc}")

def summary():
    fails = [r for r in results if r[2] == "fail"]
    warns = [r for r in results if r[2] == "warn"]
    print(INFO(f"\n汇总: 就绪 {len(results)-len(fails)-len(warns)} 项, 提醒 {len(warns)} 项, 缺失 {len(fails)} 项"))
    if fails:
        print(BAD("存在必需项缺失, 先修复后再开拍; 仅提醒项可在缺样音路线时降级使用。"))
        return 1
    print(OK("必需项全部就绪, 可进入 R3 制片流程。"))
    return 0

def run_checks(install=False, yes=False):
    global ARGS
    ARGS = argparse.Namespace(install=install, yes=yes)
    check_python()
    check_stdlib()
    check_ffmpeg()
    check_edge_tts()
    check_network()
    check_skill_package()
    print_report()
    if ARGS.install:
        apply_installs()
    return summary()

def main(argv=None):
    ap = argparse.ArgumentParser(description="finance-video-producer 环境自检/安装")
    ap.add_argument("--install", action="store_true", help="经确认后安装可选本地依赖")
    ap.add_argument("--yes", action="store_true", help="跳过安装逐项确认")
    args = ap.parse_args(argv)
    return run_checks(install=args.install, yes=args.yes)

if __name__ == "__main__":
    sys.exit(main())
