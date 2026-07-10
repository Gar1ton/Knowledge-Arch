@echo off
rem Knowledge Arch 独立版启动（Windows）。附加参数原样透传给 serve。
cd /d "%~dp0.."
where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 未找到 uv，请先运行 scripts\install.bat
  exit /b 1
)
uv run knowledge-arch serve %*
