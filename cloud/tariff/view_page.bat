@echo off
REM 双击运行：拉取最新的河北移动资费查询页并用默认浏览器打开。
REM 页面无公网入口，只存在私密仓库里，靠这个脚本取回本机。
setlocal enabledelayedexpansion
chcp 65001 >nul 2>nul
set "HERE=%~dp0"

set "PY="
if exist "%HERE%.venv\Scripts\python.exe" set "PY=%HERE%.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul
  if not errorlevel 1 set "PY=py"
)
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  if exist "D:\Work\WorkBuddy\.tools\venv\Scripts\python.exe" set "PY=D:\Work\WorkBuddy\.tools\venv\Scripts\python.exe"
)
if not defined PY (
  echo [错误] 没找到 Python。请安装 Python 3，或把本脚本里的 PY 改成实际路径。
  pause
  exit /b 1
)

"%PY%" "%HERE%view_page.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [失败] 退出码 %RC%
  pause
)
endlocal
