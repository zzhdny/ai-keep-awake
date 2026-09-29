#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI KeepAwake —— 电脑里有 AI 正在工作时阻止 Windows 息屏/待机。

工作原理：
  1. 每隔 poll_interval_seconds 秒用 psutil 轮询一次所有进程。
  2. 满足任一条件的进程被视为"AI 进程"：
       - 进程名在 process_names 白名单里（不区分大小写）
       - 进程命令行包含 cmdline_keywords 中任一关键词
     命令行包含 exclude_cmdline_keywords 中任一关键词的进程永远不算。
  3. AI 进程自己，以及它派生的所有子进程（AI 启动的安装/转码/脚本等任务），
     只要在 grace_seconds 内有过 CPU 活动（超过 min_cpu_seconds_per_poll），
     就视为"AI 正在工作"。
  4. 有 AI 在工作 → SetThreadExecutionState 阻止息屏/待机；
     全部空闲 → 恢复系统默认。程序退出或崩溃时系统自动恢复，无残留。

用法：
    py -3.12 ai_keepawake.py                  常驻运行（默认）
    py -3.12 ai_keepawake.py --scan           列出当前会被识别为 AI 的进程
    py -3.12 ai_keepawake.py --once           只检测一次，输出结论后退出
    py -3.12 ai_keepawake.py --test-hold 30   手动阻止息屏 30 秒（验证用）
    py -3.12 ai_keepawake.py --install-startup  写入开机自启（启动文件夹 VBS）
    可选参数：--config <路径>  --verbose  --duration <秒>
"""
import argparse
import ctypes
import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

try:
    import psutil
except ImportError:
    sys.stderr.write("缺少依赖 psutil，请先执行：py -3.12 -m pip install psutil\n")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "ai_keepawake_config.json")
PID_FILE = os.path.join(SCRIPT_DIR, "ai_keepawake.pid")
LOG_FILE = os.path.join(SCRIPT_DIR, "ai_keepawake.log")
CRASH_LOG = os.path.join(SCRIPT_DIR, "ai_keepawake_crash.log")

DEFAULT_CONFIG = {
    "_说明": {
        "用途": "有 AI 进程在工作时阻止息屏/待机，全部空闲后恢复系统默认行为。改完此文件保存即可，程序会自动热加载。",
        "process_names": "AI 进程名白名单（不区分大小写，.exe 可省略），精确匹配进程名。",
        "cmdline_keywords": "命令行关键词，进程命令行包含任一词（子串匹配）即视为 AI 进程。",
        "exclude_cmdline_keywords": "排除名单：命令行包含任一词的进程永远不算 AI（用于排除名字撞车、误伤的后台工具）。",
        "include_descendants": "AI 进程派生的子进程（AI 启动的安装、转码、脚本任务）的 CPU 活动是否也算 AI 在工作。",
        "grace_seconds": "宽限期：AI 进程停止 CPU 活动后仍维持'工作中'状态多少秒，容忍 AI 等待网络响应的空档。",
        "min_cpu_seconds_per_poll": "AI 程序自身进程（按名单/关键词匹配的）一个轮询周期内 CPU 累计超过该秒数才算'有活动'。实测空闲 Electron 进程噪声在 0.1 以下、真实工作在 0.25 以上，默认 0.2 居中。",
        "min_cpu_seconds_per_poll_descendant": "AI 派生子进程（pip/npm/编译等真实任务）的活动阈值，保持灵敏，默认 0.05。",
        "poll_interval_seconds": "轮询间隔秒数。",
        "prevent_display_off": "true 时阻止息屏。",
        "prevent_system_sleep": "true 时阻止系统待机睡眠。",
        "block_only_on_ac": "true 时仅在使用外接电源时阻止息屏；用电池时不干预（防止耗电）。",
    },
    "process_names": [
        "zcode.exe", "claude.exe", "codex.exe", "codex-windows-sandbox-service.exe",
        "ollama.exe", "ollama_app.exe",
        "lm studio.exe", "lmstudio.exe", "chatgpt.exe", "copilot.exe",
        "jan.exe", "gpt4all.exe", "msty.exe", "chatbox.exe",
        "cherry studio.exe", "cherrystudio.exe", "anythingllm.exe",
        "comfyui.exe", "koboldcpp.exe", "llamafile.exe",
        "llama-server.exe", "llama-cli.exe", "llama-swarm.exe",
        "cursor.exe", "windsurf.exe", "trae.exe",
        "workbuddy.exe", "deepseek.exe", "doubao.exe", "kimi.exe",
        "qwen.exe", "chatglm.exe", "openai.exe",
    ],
    "cmdline_keywords": [
        "zcode", "claude", "codex", "ollama", "llama", "lmstudio", "lm studio",
        "chatgpt", "gpt4all", "comfyui", "vllm", "sglang", "koboldcpp",
        "llamafile", "text-generation-webui", "open-webui", "openwebui",
        "openai", "anthropic", "deepseek", "deepseek-ai", "\\dsh\\", "qwen", "zhipu", "chatglm",
        "bigmodel", "moonshot", "kimi", "doubao", "anythingllm",
        "cherry-studio", "cherry studio", "msty", "jan.ai", "copilot",
        "gemini", "grok", "aider", "cursor-agent", "windsurf", "trae",
        "workbuddy", "codebuddy",
    ],
    "exclude_cmdline_keywords": [
        "zcode-activity-monitor",
        "ai-keep-awake",
        "ai_keepawake",
    ],
    "include_descendants": True,
    "grace_seconds": 180,
    "min_cpu_seconds_per_poll": 0.2,
    "min_cpu_seconds_per_poll_descendant": 0.05,
    "poll_interval_seconds": 5,
    "prevent_display_off": True,
    "prevent_system_sleep": True,
    "block_only_on_ac": True,
}

# ---------------------------------------------------------------- Windows API

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
_k32.SetThreadExecutionState.restype = ctypes.c_uint


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_byte),
        ("BatteryFlag", ctypes.c_byte),
        ("BatteryLifePercent", ctypes.c_byte),
        ("Reserved1", ctypes.c_byte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def set_execution_state(hold, prevent_display, prevent_sleep):
    """hold=True 阻止息屏/待机；hold=False 恢复系统默认。线程退出时自动失效。"""
    flags = ES_CONTINUOUS
    if hold:
        if prevent_display:
            flags |= ES_DISPLAY_REQUIRED
        if prevent_sleep:
            flags |= ES_SYSTEM_REQUIRED
    _k32.SetThreadExecutionState(flags)


def on_battery():
    sps = _SYSTEM_POWER_STATUS()
    if _k32.GetSystemPowerStatus(ctypes.byref(sps)):
        return sps.ACLineStatus == 0
    return False


# ---------------------------------------------------------------- 配置

def load_config(path, create_if_missing=True):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                user = json.load(f)
            cfg.update(user)
        except Exception as e:
            logging.getLogger("ai_keepawake").warning("读取配置失败(%s)，使用默认配置", e)
    elif create_if_missing:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
    return cfg


# ---------------------------------------------------------------- 检测核心

class Detector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.cpu_last = {}        # pid -> 上次累计 CPU 时间
        self.last_active = {}     # pid -> 上次判定为"有活动"的时刻
        self.cmdline_cache = {}   # (pid, create_time) -> 小写命令行
        self._apply(cfg)

    def _apply(self, cfg):
        self.names = set()
        for n in cfg.get("process_names", []):
            n = str(n).strip().lower()
            if n:
                self.names.add(n[:-4] if n.endswith(".exe") else n)
        self.keywords = [str(k).lower() for k in cfg.get("cmdline_keywords", []) if k]
        self.excludes = [str(k).lower() for k in cfg.get("exclude_cmdline_keywords", []) if k]

    def update_config(self, cfg):
        self.cfg = cfg
        self._apply(cfg)

    def _cmdline(self, pr):
        key = (pr["pid"], pr["create"])
        hit = self.cmdline_cache.get(key)
        if hit is not None:
            return hit
        try:
            cl = " ".join(pr["obj"].cmdline()).lower()
        except Exception:
            cl = ""
        if len(self.cmdline_cache) > 4096:
            self.cmdline_cache.clear()
        self.cmdline_cache[key] = cl
        return cl

    def _snapshot(self):
        procs = []
        for p in psutil.process_iter(["pid", "name", "ppid", "create_time"]):
            info = p.info
            procs.append({
                "pid": info["pid"],
                "name": (info["name"] or "").lower(),
                "ppid": info["ppid"],
                "create": info["create_time"] or 0.0,
                "obj": p,
            })
        return procs

    def evaluate(self):
        """返回 (active: pid->pr, matched_pids: set, 原因描述 dict)。"""
        cfg = self.cfg
        now = time.time()
        grace = float(cfg.get("grace_seconds", 180))
        eps_matched = float(cfg.get("min_cpu_seconds_per_poll", 0.2))
        eps_desc = float(cfg.get("min_cpu_seconds_per_poll_descendant", eps_matched * 0.25))
        procs = self._snapshot()
        self_pid = os.getpid()

        # 1) 名单/关键词匹配
        matched = {}
        for pr in procs:
            pid = pr["pid"]
            if pid == self_pid or not pr["name"]:
                continue
            stem = pr["name"][:-4] if pr["name"].endswith(".exe") else pr["name"]
            if pr["name"] in self.names or stem in self.names:
                matched[pid] = pr
                pr["reason"] = "AI名单"
                continue
            if self.excludes and any(x in self._cmdline(pr) for x in self.excludes if x):
                continue
            cl = self._cmdline(pr)
            if cl and any(k in cl for k in self.keywords):
                matched[pid] = pr
                pr["reason"] = "关键词"

        # 2) AI 的子孙进程也算候选（AI 派生的安装/转码/脚本任务）
        candidates = dict(matched)
        if cfg.get("include_descendants", True):
            parent_of = {pr["pid"]: pr["ppid"] for pr in procs}
            for pr in procs:
                pid = pr["pid"]
                if pid in candidates or pid == self_pid:
                    continue
                cur, seen = parent_of.get(pid), set()
                while cur is not None and cur not in seen and len(seen) < 64:
                    if cur in matched:
                        candidates[pid] = pr
                        pr["reason"] = "AI子进程"
                        break
                    seen.add(cur)
                    cur = parent_of.get(cur)

        # 3) 用 CPU 活动判定"正在工作"
        active = {}
        for pid, pr in candidates.items():
            try:
                t = pr["obj"].cpu_times()
                total = (t.user or 0.0) + (t.system or 0.0)
            except psutil.Error:
                total = self.cpu_last.get(pid, 0.0)
            prev = self.cpu_last.get(pid)
            self.cpu_last[pid] = total
            fresh_process = pr["create"] >= now - grace
            epsilon = eps_matched if pid in matched else eps_desc
            if fresh_process or prev is None:
                self.last_active[pid] = now
            elif total - prev >= epsilon:
                self.last_active[pid] = now
            if now - self.last_active.get(pid, 0.0) <= grace:
                active[pid] = pr

        # 4) 清理已退出进程的跟踪记录
        live = {pr["pid"] for pr in procs}
        for table in (self.cpu_last, self.last_active):
            for pid in list(table):
                if pid not in live:
                    del table[pid]

        return active, set(matched)

    # ------------------------------------------------------------ 扫描报告

    def scan(self, extra_keywords=()):
        """列出当前匹配 AI 名单/关键词的进程（供 --scan 和扩展配置参考）。"""
        keywords = list(self.keywords) + [k.lower() for k in extra_keywords]
        rows = []
        for pr in self._snapshot():
            if not pr["name"] or pr["pid"] == os.getpid():
                continue
            stem = pr["name"][:-4] if pr["name"].endswith(".exe") else pr["name"]
            why = None
            if pr["name"] in self.names or stem in self.names:
                why = "名单"
            else:
                cl = self._cmdline(pr)
                if cl:
                    if any(x in cl for x in self.excludes if x):
                        continue
                    hits = [k for k in keywords if k and k in cl]
                    if hits:
                        why = "关键词:" + ",".join(hits[:4])
            if why:
                rows.append((pr["pid"], pr["name"], why, self._cmdline(pr)[:120]))
        return rows


# ---------------------------------------------------------------- 输出/日志

def _setup_logging(verbose):
    if sys.stdout is None:  # pythonw.exe 下没有 stdout
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    log = logging.getLogger("ai_keepawake")
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.raiseExceptions = False  # handler 出错(如日志文件被占用)不炸进程
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(LOG_FILE, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    return log


def _describe(active, matched_pids, once_mode=False):
    parts = []
    for pid, pr in sorted(active.items()):
        reason = "AI进程" if pid in matched_pids else "AI的子进程"
        s = "%s(%d)[%s]" % (pr["name"] or "?", pid, reason)
        if once_mode:
            cl = " ".join(pr["obj"].cmdline() or [])[:90]
            if cl:
                s += " ← " + cl
        parts.append(s)
    return "；".join(parts)


# ---------------------------------------------------------------- 子命令

def cmd_scan(detector):
    extra = ("gpt", "llm", "glm", "zhipuai", "agent", "prompt", "openrouter",
             "groq", "mistral", "minimax", "hunyuan", "ernie", "gguf", "diffusers")
    rows = detector.scan(extra)
    if not rows:
        print("当前没有匹配到任何 AI 相关进程。")
    else:
        print("当前会被视为 AI 的进程（不含子进程推导）：")
        for pid, name, why, cl in rows:
            print("  PID=%-7d %-28s %s" % (pid, name, why))
            if cl:
                print("      CMD: %s" % cl)
    print()
    print("提示：若某 AI 工具没被识别，把它的进程名或命令行关键词加进")
    print("      ai_keepawake_config.json 的 process_names / cmdline_keywords 即可。")


def cmd_once(detector):
    active, matched = detector.evaluate()
    if active:
        print("检测结果：检测到 %d 个 AI 相关进程正在活动 → 会阻止息屏" % len(active))
        print("  " + _describe(active, matched, once_mode=True))
    else:
        print("检测结果：当前没有 AI 活动 → 按系统设定正常息屏")


def cmd_test_hold(detector, seconds):
    cfg = detector.cfg
    log = logging.getLogger("ai_keepawake")
    log.info("手动测试：阻止息屏 %d 秒（可用管理员 PowerShell 运行 powercfg /requests 验证）", seconds)
    set_execution_state(True, cfg.get("prevent_display_off", True), cfg.get("prevent_system_sleep", True))
    try:
        time.sleep(seconds)
    finally:
        set_execution_state(False, True, True)
    log.info("测试结束，已恢复系统默认息屏行为")


def install_startup():
    """写入开机自启（启动文件夹 VBS，与 zcode_activity_monitor 同一方式）。"""
    startup = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                           "Start Menu", "Programs", "Startup")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    vbs = os.path.join(startup, "ai_keepawake.vbs")
    content = ('CreateObject("WScript.Shell").Run '
               '"""%s"" ""%s""", 0, False\r\n' % (pythonw, os.path.join(SCRIPT_DIR, "ai_keepawake.py")))
    with open(vbs, "w", encoding="ascii") as f:
        f.write(content)
    print("已写入开机自启：%s" % vbs)
    print('启动命令："%s" "%s"' % (pythonw, os.path.join(SCRIPT_DIR, "ai_keepawake.py")))
    print("如需取消自启，删除上面那个 .vbs 文件即可。")


def another_instance():
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        p = psutil.Process(pid)
        if p.is_running() and "python" in (p.name() or "").lower():
            return pid
    except Exception:
        pass
    return None


def run_loop(detector, duration, config_path):
    log = logging.getLogger("ai_keepawake")
    cfg = detector.cfg
    if not (cfg.get("prevent_display_off", True) or cfg.get("prevent_system_sleep", True)):
        log.warning("prevent_display_off 和 prevent_system_sleep 均为 false，程序不会阻止任何息屏行为！")
    holding = None
    started = time.time()
    last_detail = time.time()
    last_beat = time.time()
    try:
        config_mtime = os.path.getmtime(config_path)
    except OSError:
        config_mtime = 0.0

    log.info("AI KeepAwake 已启动：有 AI 工作时阻止息屏，AI 空闲 %d 秒后恢复系统默认。"
             "宽限期=%ds 轮询=%ds 电池供电时不干预=%s",
             int(cfg.get("grace_seconds", 180)), int(cfg.get("grace_seconds", 180)),
             int(cfg.get("poll_interval_seconds", 5)), cfg.get("block_only_on_ac", True))

    while True:
        try:
            # 配置热加载
            try:
                mtime = os.path.getmtime(config_path)
            except OSError:
                mtime = config_mtime
            if mtime != config_mtime:
                config_mtime = mtime
                new_cfg = load_config(config_path, create_if_missing=False)
                detector.update_config(new_cfg)
                log.info("检测到配置文件变化，已重新加载")

            cfg = detector.cfg
            active, matched = detector.evaluate()
            want = bool(active)
            battery_skip = False
            if want and cfg.get("block_only_on_ac", True) and on_battery():
                want, battery_skip = False, True

            if want != holding:
                if want:
                    set_execution_state(True, cfg.get("prevent_display_off", True),
                                        cfg.get("prevent_system_sleep", True))
                    log.info("【阻止息屏】AI 正在工作：%s", _describe(active, matched))
                else:
                    set_execution_state(False, True, True)
                    if battery_skip:
                        log.info("【恢复正常】已切换为电池供电，暂不阻止息屏（配置 block_only_on_ac）")
                    else:
                        log.info("【恢复正常】AI 工作已结束/空闲，恢复系统默认息屏行为")
                holding = want
                last_detail = time.time()
            elif want and time.time() - last_detail >= 600:
                log.info("【持续阻止息屏】AI 仍在工作：%s", _describe(active, matched))
                last_detail = time.time()

            if not want and time.time() - last_beat >= 3600:
                log.info("程序运行中，当前无 AI 活动（正常状态）")
                last_beat = time.time()
            if want:
                last_beat = time.time()
        except Exception:
            log.exception("本轮检测异常，稍后重试")

        if duration and time.time() - started >= duration:
            log.info("到达 --duration 限时，正常退出")
            break
        time.sleep(max(1, int(detector.cfg.get("poll_interval_seconds", 5))))


def main():
    parser = argparse.ArgumentParser(description="AI KeepAwake：有 AI 工作时阻止息屏")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--scan", action="store_true", help="列出当前会被识别为 AI 的进程")
    parser.add_argument("--once", action="store_true", help="只检测一次并输出结论")
    parser.add_argument("--test-hold", type=int, metavar="秒", help="手动阻止息屏 N 秒（验证用）")
    parser.add_argument("--duration", type=int, default=0, help="常驻运行 N 秒后退出（测试用）")
    parser.add_argument("--install-startup", action="store_true", help="写入开机自启（启动文件夹 VBS）后退出")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args()

    log = _setup_logging(args.verbose)
    cfg = load_config(args.config)
    detector = Detector(cfg)

    if args.scan:
        cmd_scan(detector)
        return
    if args.once:
        cmd_once(detector)
        return
    if args.test_hold:
        cmd_test_hold(detector, args.test_hold)
        return
    if args.install_startup:
        install_startup()
        return

    # 常驻模式：单实例保护
    other = another_instance()
    if other:
        log.info("AI KeepAwake 已在运行（PID=%d），本实例退出。", other)
        return

    with open(PID_FILE, "w", encoding="ascii") as f:
        f.write(str(os.getpid()))
    try:
        backoff = 5
        while True:
            try:
                run_loop(detector, args.duration, args.config)
                break  # 正常退出（--duration 到期等）
            except KeyboardInterrupt:
                raise
            except Exception:
                # 兜底监督：run_loop 意外崩溃时记录到独立 crash 日志并重启，
                # 避免任何未预料异常让守护进程无声消失
                import traceback
                try:
                    with open(CRASH_LOG, "a", encoding="utf-8") as f:
                        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " run_loop 异常，%.0f 秒后重启\n" % backoff)
                        f.write(traceback.format_exc() + "\n")
                except OSError:
                    pass
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
    finally:
        set_execution_state(False, True, True)
        try:
            os.remove(PID_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    main()
