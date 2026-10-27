#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
SURF_PY="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [ ! -x "$SURF_PY" ]; then SURF_PY=python3; fi

# 统一入口：启动控制台（内含 QQ 扫码登录）。bot 服务（supervisor）作为本终端的
# 子进程随窗口同生共死——关闭窗口即停止；需要 7×24 常驻时请在 tmux 内运行本命令，
# 或直接 python3 -m app.supervisor（前台）。
exec "$SURF_PY" -m app.console "$@"
