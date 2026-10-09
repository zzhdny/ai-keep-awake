# -*- coding: utf-8 -*-
"""诊断v2：测量候选进程 CPU 增量 + 显示进程角色。

这里列出的是"已被确认为在工作"的进程（两次 CPU 活动间隔 ≤ 确认窗口），
它们才会阻止息屏。单次孤立脉冲（心跳/轮询型后台活动）不算工作、不会出现
在这里，也不会挡息屏——参见 README 的"持续活动确认"。
"""
import time
import psutil
import ai_keepawake as ak

cfg = ak.load_config(ak.DEFAULT_CONFIG_PATH, create_if_missing=False)
confirm = int(cfg.get("burst_confirm_seconds", 120))

SNAP, POLL = 30, 5
det = ak.Detector(cfg)
samples = {}
last = {}
for _ in range(SNAP // POLL):
    time.sleep(POLL)
    act, mat = det.evaluate()
    for pid, pr in act.items():
        try:
            t = pr["obj"].cpu_times()
            total = (t.user or 0.0) + (t.system or 0.0)
        except Exception:
            continue
        if pid in last:
            samples.setdefault(pid, []).append((pr["name"], pr.get("reason", "?"), total - last[pid]))
        last[pid] = total
    for pid in list(last):
        if pid not in act:
            del last[pid]

print("已确认在工作（会阻止息屏）的进程数: %d（阈值：名单=0.2s/轮、子进程=0.05s/轮，确认窗口=%ds）" % (len(samples), confirm))
print("%-24s %-9s %-20s %s" % ("进程", "原因", "每5秒增量(秒)", "均值%"))
agg = []
objs = {}
for pid, lst in samples.items():
    name, reason = lst[0][0], lst[0][1]
    deltas = [d for _, _, d in lst]
    avg_pct = sum(deltas) / len(deltas) / POLL * 100
    agg.append((avg_pct, pid, name, reason, deltas))
    objs[pid] = lst[0]
for avg_pct, pid, name, reason, deltas in sorted(agg, reverse=True)[:12]:
    eps = 0.05 if reason == "AI子进程" else 0.2
    mark = "  ← 有活动" if max(deltas) >= eps else "  ← 宽限期内"
    print("%-24s %-9s %-20s %.2f%%%s"
          % ("%s(%d)" % (name, pid), reason,
             ",".join("%.2f" % d for d in deltas), avg_pct, mark))

print()
for avg_pct, pid, name, reason, deltas in sorted(agg, reverse=True)[:12]:
    eps = 0.05 if reason == "AI子进程" else 0.2
    if max(deltas) >= eps:
        try:
            cl = " ".join(psutil.Process(pid).cmdline() or [])
            print("活跃 %s(%d): %s" % (name, pid, cl[:150]))
        except Exception as e:
            print("活跃 %s(%d): 无法读取 (%s)" % (name, pid, e))

print("\n说明：单次孤立脉冲（心跳/轮询）不算工作，不会阻止息屏；"
      "两次活动间隔 ≤ %d 秒才确认为'在工作'（见 README 的持续活动确认）。" % confirm)
