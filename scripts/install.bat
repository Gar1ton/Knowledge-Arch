@echo off
rem Knowledge Arch 独立版一键安装（Windows）。
rem 用法：scripts\install.bat [--cn] [--minimal]
setlocal enabledelayedexpansion
cd /d "%~dp0.."

set "MINIMAL=0"
for %%A in (%*) do (
  if "%%A"=="--cn" (
    set "UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"
    echo [INFO] 已启用清华 PyPI 镜像
  )
  if "%%A"=="--minimal" set "MINIMAL=1"
)

rem ── 1. 引导 uv ─────────────────────────────────────────────
where uv >nul 2>nul
if errorlevel 1 (
  echo [INFO] 未找到 uv，正在安装（astral.sh 官方脚本）...
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  where uv >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] uv 安装失败，请手动安装：https://docs.astral.sh/uv/
    exit /b 1
  )
)

rem ── 2. 安装钉定的 Python 与依赖 ────────────────────────────
echo [INFO] 安装 Python 与项目依赖...
uv python install
if "%MINIMAL%"=="1" (
  uv sync
) else (
  uv sync --extra graph --extra local-embedding --extra backup --extra notion
)
if errorlevel 1 exit /b 1

rem ── 3. 体检 ────────────────────────────────────────────────
uv run knowledge-arch doctor

echo.
echo [SUCCESS] 安装完成！启动：scripts\start.bat  （或 uv run knowledge-arch serve）
echo [NOTE] Windows 无 Milvus Lite 原生支持，向量检索三选一：
echo        1) 连接外部 Milvus 服务  2) 在 WSL 内运行本应用  3) 自动回落 SQLite 词法检索
echo 首次启动会在 %%USERPROFILE%%\.knowledge-arch 生成配置与随机登录密码（注意查看控制台输出）。
endlocal
