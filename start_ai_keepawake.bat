@echo off
chcp 65001 >nul
echo Starting AI KeepAwake in console mode (Ctrl+C to stop)...
py -3.12 "%~dp0ai_keepawake.py"
pause
