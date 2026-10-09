#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ai_keepawake 检测逻辑的离线单元测试（不需要真的 AI 在跑）。

用假进程 + 假时钟直接驱动 Detector.evaluate()，覆盖"持续活动确认"的两层规则：

  A 低频心跳（每 270 秒 0.31 秒）       永不确认 → 不阻止息屏（WorkBuddy 场景）
  B 真实任务（每 5 秒 1.5 秒）          第 3 轮确认 → 阻止息屏，停止后按宽限期释放
  C 中速真实工作（每 100 秒 1.2 秒）    第二次脉冲（间隔 ≤ 120 秒）即确认
  D 低于阈值的噪声（0.10 秒每 30 秒）   从未算脉冲 → 不确认
  E 派生子进程轻任务（每轮 0.07 秒）    窗口累计到 0.25 秒才确认（子进程下限）
  F 高频小脉冲（每 60 秒 0.25 秒）      凑够两次但累计 0.75 < 1.0 → 不确认
  G 轻量真实工作（每轮 0.4 秒）         第 4 轮累计 1.2 ≥ 1.0 → 确认
  H 排除名单（ai_keepawake / zcode-activity-monitor）永远不算 AI
  I 命令行关键词命中（node 跑 claude-code 的 CLI）

运行：py -3.12 test_ai_keepawake.py
"""
import types
import unittest

import ai_keepawake as ak


class FakeTime:
    """替代模块里的 time：手动推进时钟，测试不真的等待。"""

    def __init__(self, t=1_000_000.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def advance(self, s):
        self.t += s


class FakeProc:
    """模拟 psutil.Process 的最小子集。"""

    def __init__(self, pid, name, cpu=0.0, ppid=1, cmdline=None):
        self.pid = pid
        self._name = name
        self.cpu = cpu
        self._ppid = ppid
        self._cmdline = cmdline if cmdline is not None else ["C:\\Tools\\%s" % name]

    def name(self):
        return self._name

    def cpu_times(self):
        return types.SimpleNamespace(user=self.cpu, system=0.0)

    def cmdline(self):
        return list(self._cmdline)


class Harness:
    """把一个假进程表和假时钟接到 Detector 上。"""

    def __init__(self, **cfg_extra):
        cfg = dict(ak.DEFAULT_CONFIG)
        cfg.pop("_说明", None)
        cfg.update(cfg_extra)
        self.det = ak.Detector(cfg)
        self.ft = FakeTime()
        self._procs = {}
        self.det._snapshot = lambda: [dict(pr) for pr in self._procs.values()]

    def add(self, pid, name, ppid=1, cmdline=None):
        self._procs[pid] = {
            "pid": pid, "name": name.lower(), "ppid": ppid, "create": 1.0,
            "obj": FakeProc(pid, name, ppid=ppid, cmdline=cmdline),
        }

    def burn(self, pid, seconds):
        """让进程在两次轮询之间消耗 seconds 秒 CPU。"""
        self._procs[pid]["obj"].cpu += seconds

    def tick(self, seconds=5.0):
        self.ft.advance(seconds)
        return self.det.evaluate()

    def holds(self, seconds=5.0):
        active, _ = self.tick(seconds)
        return bool(active)


class Base(unittest.TestCase):
    def make(self, **cfg_extra):
        h = Harness(**cfg_extra)
        old = ak.time
        ak.time = h.ft
        self.addCleanup(lambda: setattr(ak, "time", old))
        return h


class Test持续活动确认(Base):
    def test_A_低频心跳永不确认(self):
        """WorkBuddy 场景：每 270 秒一次 0.31 秒脉冲，一小时零确认。"""
        h = self.make()
        h.add(100, "workbuddy.exe")
        pulses = held = 0
        for i in range(720):          # 3600 秒，5 秒一轮
            if i and i % 54 == 0:     # 每 270 秒一次脉冲
                h.burn(100, 0.31)
                pulses += 1
            if h.holds():
                held += 1
        self.assertEqual(pulses, 13)
        self.assertEqual(held, 0)

    def test_B_真实任务确认并按宽限期释放(self):
        """ZCode 场景：连续 1.5 秒/轮干活 → 确认；停手后约 180 秒释放。"""
        h = self.make()
        h.add(100, "zcode.exe")
        results = []
        for _ in range(12):
            h.burn(100, 1.5)
            results.append(h.holds())
        # 第 1 轮只建基线；第 2 轮出现首个脉冲；第 3 轮累计 3.0 ≥ 1.0 → 确认
        self.assertEqual(results[:2], [False, False])
        self.assertTrue(all(results[2:]))
        i = 0
        while h.holds() and i < 100:  # 停止工作，数到释放为止
            i += 1
        self.assertGreaterEqual(i * 5, 180)    # 至少维持一个宽限期
        self.assertLessEqual(i * 5, 180 + 10)  # 也不会多拖

    def test_C_百秒间隔的真实工作确认(self):
        """工作呈稀疏脉冲但间隔 ≤ 120 秒、量够 → 仍确认（不会误杀）。"""
        h = self.make()
        h.add(100, "claude.exe")
        first_hold_at = None
        for i in range(120):          # 600 秒
            if i and i % 20 == 0:     # 每 100 秒一次脉冲
                h.burn(100, 1.2)
            if h.holds() and first_hold_at is None:
                first_hold_at = i * 5
        self.assertEqual(first_hold_at, 200)  # 第二次脉冲（间隔 100 秒）确认

    def test_D_低于阈值的噪声从不确认(self):
        h = self.make()
        h.add(100, "zcode.exe")
        for _ in range(240):          # 1200 秒
            h.burn(100, 0.10)         # 名单进程阈值 0.2，永远够不着
            self.assertFalse(h.holds())

    def test_E_派生子进程轻任务确认(self):
        h = self.make()
        h.add(100, "zcode.exe")
        h.add(200, "python.exe", ppid=100)
        for _ in range(4):
            h.burn(200, 0.07)
            self.assertFalse(h.holds())   # 累计 0.07/0.14/0.21 都不到子进程下限 0.25
        h.burn(200, 0.07)
        self.assertTrue(h.holds())        # 第 4 个脉冲累计 0.28 ≥ 0.25 → 确认

    def test_F_高频小脉冲被累计下限挡住(self):
        """心跳周期短于确认窗口：成对出现但总量太小，仍不算工作。"""
        h = self.make()
        h.add(100, "chatgpt.exe")
        for i in range(300):          # 1500 秒
            if i and i % 12 == 0:     # 每 60 秒一次 0.25 秒小脉冲
                h.burn(100, 0.25)
            self.assertFalse(h.holds())   # 窗口内最多 3 个脉冲 = 0.75 < 1.0

    def test_G_轻量真实工作仍能确认(self):
        h = self.make()
        h.add(100, "zcode.exe")
        seq = []
        for _ in range(5):
            h.burn(100, 0.4)
            seq.append(h.holds())
        # 第 1 轮基线；累计 0.4 / 0.8 / 1.2 ≥ 1.0 → 第 4 轮确认
        self.assertEqual(seq, [False, False, False, True, True])


class Test识别与排除(Base):
    def test_H_排除名单不误报(self):
        """本机两个 pythonw 常驻工具即使命令行走过 AI 目录也不算 AI。"""
        h = self.make()
        h.add(100, "python.exe",
              cmdline=["python.exe", "D:\\glm\\ai-keep-awake\\ai_keepawake.py"])
        h.add(200, "pythonw.exe",
              cmdline=["pythonw.exe", "D:\\glm\\zcode-activity-monitor\\monitor.py"])
        for _ in range(10):
            h.burn(100, 2.0)
            h.burn(200, 2.0)
            active, matched = h.tick()
            self.assertEqual(matched, set())
            self.assertFalse(active)

    def test_I_命令行关键词命中(self):
        """Claude Code CLI：node 进程，命令行含 claude，照样识别为 AI。"""
        h = self.make()
        h.add(100, "node.exe",
              cmdline=["node.exe", "C:\\Users\\u\\AppData\\Roaming\\npm\\node_modules"
                       "\\@anthropic-ai\\claude-code\\cli.js"])
        h.tick()                      # 基线
        h.burn(100, 8.0)
        h.tick()                      # 第 1 个脉冲
        h.burn(100, 8.0)
        active, matched = h.tick()    # 第 2 个脉冲 → 确认
        self.assertIn(100, matched)
        self.assertTrue(active)

    def test_J_本机AI名单覆盖(self):
        """本机实际在用的 AI 进程名/路径都能被识别（回归用）。"""
        cfg = dict(ak.DEFAULT_CONFIG)
        cfg.pop("_说明", None)
        det = ak.Detector(cfg)
        self.assertIn("zcode", det.names)
        self.assertIn("workbuddy", det.names)
        self.assertIn("deepseek harness", det.names)
        self.assertIn("doubao", det.names)
        self.assertIn("cowork-svc", det.names)
        self.assertIn("msofficeplusservice", det.names)
        for kw in ("claude", "codex", "pdf2zh", "officeplus", "office16\\ai\\ai.exe",
                   "workbuddy", "codebuddy", "chatgpt", "deepseek"):
            self.assertIn(kw, det.keywords)


if __name__ == "__main__":
    unittest.main(verbosity=2)
