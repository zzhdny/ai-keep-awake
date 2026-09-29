# AI KeepAwake —— AI 工作时阻止息屏

电脑里有 AI（ZCode、Claude、WorkBuddy、豆包、DeepSeek dsh、ChatGPT、Ollama 等）
正在执行任务时，自动阻止 Windows 息屏/待机；**所有 AI 都空闲下来之后**，恢复
系统默认的息屏设置，不会一直亮屏。

在作者机器上已部署为开机自启常驻，日常无需任何操作。

## 快速开始（从 GitHub 部署）

1. 把本仓库的文件下载/克隆到任意目录，例如
   `git clone https://github.com/zzhdny/ai-keep-awake.git`
2. 安装 Python 3.12 与依赖：`py -3.12 -m pip install psutil`
3. 先体验检测效果：`py -3.12 ai_keepawake.py --scan`、`--once`、`--test-hold 30`
4. 确认无误后常驻 + 自启：`py -3.12 ai_keepawake.py --install-startup`

## 工作原理

1. 每 5 秒扫描一次所有进程，按三层规则识别"AI 正在工作"：
   - **进程名单**：进程名精确匹配（如 `ZCode.exe`、`WorkBuddy.exe`、`ollama.exe`…）；
   - **命令行关键词**：命令行包含关键词（如 `zcode`、`claude`、`codebuddy`、`comfyui`…）；
   - **子进程推导**：AI 进程派生的子进程（AI 启动的安装、转码、脚本、本地模型推理等）
     干活时同样算"AI 在工作"。
2. 光被识别还不够，还要看**CPU 活动**：一个轮询周期内 CPU 时间超过
   `min_cpu_seconds_per_poll`（默认 0.05 秒）才算"有活动"。所以：
   - AI 正在干活 / 正在等你回复 / 正在跑工具 → 保持亮屏；
   - AI 只是开着挂在后台（比如聊天窗口闲置）→ 宽限期（默认 180 秒）后不算工作，
     系统正常息屏。
3. 有 AI 在工作 → 调用 Windows 官方 API `SetThreadExecutionState` 同时阻止
   息屏和待机（**不拦截**你手动点的睡眠/合盖）；没有 → 恢复系统默认。
   程序退出或崩溃时系统自动恢复，无残留。

## 文件

| 文件 | 作用 |
|---|---|
| `ai_keepawake.py` | 主程序 |
| `ai_keepawake_config.json` | 配置（AI 名单、关键词、宽限期等），**改完保存即自动生效**，无需重启 |
| `start_ai_keepawake.bat` | 前台启动（调试看输出用） |
| `stop_ai_keepawake.bat` | 停止后台常驻进程 |
| `ai_keepawake.log` | 运行日志（只记状态变化，自动轮转） |
| `ai_keepawake.pid` | 当前进程号（自动生成） |
| `secret_scan.py` | 发布前密钥泄漏扫描器（通用小工具，对本项目目录运行） |

## 发布其他项目前：先扫密钥

把任何 AI 生成的项目发布到 GitHub 前，运行
`py -3.12 secret_scan.py <项目目录>`（不带参数则扫描当前目录），
确认没有硬编码的 API key/token 再发布。
它只读扫描、输出打码，本身不联网。退出码：0=干净，1=有疑似发现。

```bash
py -3.12 secret_scan.py
```

## 常用命令

```bat
py -3.12 ai_keepawake.py                  :: 前台常驻（调试用）
py -3.12 ai_keepawake.py --scan           :: 列出当前会被识别为 AI 的进程
py -3.12 ai_keepawake.py --once           :: 只检测一次，报告结论
py -3.12 ai_keepawake.py --test-hold 30   :: 手动阻止息屏 30 秒（验证用）
py -3.12 ai_keepawake.py --install-startup :: 写入开机自启
```

后台常驻由启动文件夹里的 `ai_keepawake.vbs` 完成（随 Windows 登录自动以
pythonw 静默启动，无窗口）。

## 验证它确实在起作用

以**管理员**身份打开 PowerShell，在 AI 干活时执行：

```powershell
powercfg /requests
```

能看到 `pythonw.exe` 持有 `DISPLAY` 和 `SYSTEM` 请求，即表示阻止息屏已生效。
（查看该命令本身需要管理员，程序运行不需要管理员。）

## 如何添加新的 AI

装了新的 AI 工具后，先 `--scan` 看它是否已被识别；没识别到就编辑
`ai_keepawake_config.json`：

- 知道进程名（任务管理器 → 详细信息里看到的，如 `foo.exe`）→ 加进 `process_names`；
- 只知道命令/安装路径里有特征词 → 加进 `cmdline_keywords`；
- 某个后台工具被误判成 AI → 把它的特征路径加进 `exclude_cmdline_keywords`。

保存后立即生效（程序每轮自动重读配置）。

## 可调参数

- `grace_seconds`（默认 180）：AI 停止活动后仍保持亮屏的宽限秒数。
  AI 任务经常长时间等网络响应、息屏误判过早时调大。
- `min_cpu_seconds_per_poll`（默认 0.2）：AI 程序自身进程（按名单/关键词匹配的）
  每个轮询周期（5 秒）内 CPU 累计超过该秒数才算"有活动"。实测参考：空闲挂着的
  AI 窗口（Electron 渲染进程等）噪声约 0.1 以下，真实工作在 0.25 以上。
  出现"明明没 AI 干活却一直亮屏"时调大，"AI 在干活却息屏了"时调小。
- `min_cpu_seconds_per_poll_descendant`（默认 0.05）：AI 派生子进程（pip、npm、
  编译、转码等真实任务）的活动阈值，保持灵敏。
- `prevent_display_off` / `prevent_system_sleep`：是否分别阻止息屏 / 待机。
- `block_only_on_ac`（默认 true）：使用电池时不阻止息屏（防耗电）。
  想电池下也生效就改成 `false`。

## 排查"为什么不息屏"

运行 `py -3.12 diagnose.py`：实时测量 30 秒，列出所有候选进程每轮的 CPU 增量、
平均占用和是否越线，并打印越线进程的命令行，一眼看出是谁在维持亮屏、
是真实工作还是空闲噪声。

## 卸载

1. 双击 `stop_ai_keepawake.bat` 停止当前进程；
2. 删除启动文件夹里的自启文件：
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ai_keepawake.vbs`；
3. 删除整个 `D:\glm\ai-keep-awake` 文件夹。

## 依赖

Python 3.12 + `psutil`（已安装；重装命令：`py -3.12 -m pip install psutil`）。
