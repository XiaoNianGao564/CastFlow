"""run_python tool - hardened subprocess sandbox.

安全加固：
- 环境变量白名单：只透传跑 Python 必需的变量，DASHSCOPE_API_KEY / MYSQL_* / LANGFUSE_*
  等敏感凭据**绝不**进入候选代码进程；同时清空代理变量 + 设 NO_PROXY=*。
- cwd 隔离：每次执行使用一次性临时目录作为 cwd，候选代码无法写入项目目录。
- 可选 Docker 模式：SANDBOX_MODE=docker 时，调用本机 docker 把代码放进
  --network=none 一次性容器执行，进一步隔离文件系统和网络。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Callable

from langchain_core.tools import tool

from castflow.config import settings
from castflow.tools.schema import tool_error, tool_success

# 候选代码进程允许继承的环境变量；其余一律剥离
_ENV_WHITELIST = {
    "PATH",
    "PYTHONPATH",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "USERPROFILE",
    "HOMEPATH",
    "HOMEDRIVE",
    "HOME",
    "COMSPEC",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
}

# 模块级回调：LangGraph 可能在线程池中执行 tool，thread-local 不可靠
_output_callback: Callable[[str], None] | None = None
_callback_lock = threading.Lock()


def set_output_callback(callback: Callable[[str], None] | None) -> None:
    """设置全局实时输出回调。每行 stdout/stderr 都会调用此回调。"""
    global _output_callback
    with _callback_lock:
        _output_callback = callback


def _get_output_callback() -> Callable[[str], None] | None:
    with _callback_lock:
        return _output_callback


def _build_safe_env() -> dict:
    base = {k: v for k, v in os.environ.items() if k in _ENV_WHITELIST}
    base.setdefault("PYTHONIOENCODING", "utf-8")
    base.setdefault("PYTHONUTF8", "1")
    # 显式禁止网络代理 / 关闭出网链路（候选代码不该联网）
    base["NO_PROXY"] = "*"
    base["no_proxy"] = "*"
    base["HTTP_PROXY"] = ""
    base["HTTPS_PROXY"] = ""
    base["http_proxy"] = ""
    base["https_proxy"] = ""
    # 清除 PYTHONPATH 中包含项目目录的路径，阻止 import castflow 等内部模块
    project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if "PYTHONPATH" in base:
        paths = base["PYTHONPATH"].split(os.pathsep)
        paths = [p for p in paths if not p.startswith(project_dir)]
        base["PYTHONPATH"] = os.pathsep.join(paths) if paths else ""
    return base


def _parse_last_json(stdout: str) -> dict | None:
    for line in reversed((stdout or "").strip().split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _run_subprocess(code: str) -> dict:
    work_dir = tempfile.mkdtemp(prefix="castflow_sbx_")
    script_path = os.path.join(work_dir, "main.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code)
    callback = _get_output_callback()
    timed_out = False
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", "-s", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_build_safe_env(),
            cwd=work_dir,
        )
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def _watchdog():
            nonlocal timed_out
            timed_out = True
            try:
                proc.kill()
            except OSError:
                pass

        timer = threading.Timer(settings.python_timeout_sec, _watchdog)
        timer.start()

        def _read_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)
                if callback:
                    callback(f"[stderr] {line.rstrip()}")

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        for line in proc.stdout:
            stdout_lines.append(line)
            if callback:
                callback(line.rstrip())

        proc.wait(timeout=10)
        stderr_thread.join(timeout=5)
        timer.cancel()

        if timed_out:
            raise subprocess.TimeoutExpired(cmd="python", timeout=settings.python_timeout_sec)

        return {
            "returncode": proc.returncode,
            "stdout": "".join(stdout_lines),
            "stderr": "".join(stderr_lines),
        }
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _run_docker(code: str) -> dict:
    work_dir = tempfile.mkdtemp(prefix="castflow_sbx_")
    script_path = os.path.join(work_dir, "main.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        cmd = [
            "docker", "run", "--rm",
            "--network=none",
            "--cpus=1", "--memory=1g",
            "-v", f"{work_dir}:/work:ro",
            "-w", "/work",
            "-e", "PYTHONIOENCODING=utf-8",
            "-e", "PYTHONUTF8=1",
            settings.sandbox_docker_image,
            "python", "main.py",
        ]
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.python_timeout_sec + 10,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "returncode": r.returncode,
            "stdout": r.stdout or "",
            "stderr": r.stderr or "",
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@tool
def run_python(code: str) -> dict:
    """在隔离子进程中执行 Python 代码并捕获 stdout/stderr。

    沙箱保证：环境变量白名单（不泄漏 API key / DB 密码 / Langfuse 凭据），
    cwd 隔离到一次性临时目录，禁用网络代理。

    约定：代码末尾必须 print(json.dumps({"predictions": [数字...]}))，
    本工具会把最后一行 JSON 解析后放在返回的 parsed 字段。

    可直接 import pandas / numpy / statsmodels / sklearn / lightgbm。
    """
    try:
        if settings.sandbox_mode == "docker":
            r = _run_docker(code)
        else:
            r = _run_subprocess(code)
    except subprocess.TimeoutExpired:
        return tool_error(
            "Python 代码执行超时。",
            f"timeout {settings.python_timeout_sec}s",
            next_action="简化代码或降低模型复杂度后重试。",
        )
    except FileNotFoundError as e:
        return tool_error(
            "沙箱启动失败（docker 模式需安装 docker）。",
            str(e),
            next_action="将 SANDBOX_MODE 改回 subprocess 或安装/启动 Docker。",
        )

    parsed = _parse_last_json(r["stdout"])
    payload = {
        "returncode": r["returncode"],
        "stdout": r["stdout"][-3000:],
        "stderr": r["stderr"][-1500:],
        "parsed": parsed,
        "sandbox_mode": settings.sandbox_mode,
    }
    return tool_success(
        "Python 代码执行完成。" if r["returncode"] == 0 else "Python 代码执行失败。",
        data=payload,
        next_action="检查 parsed/predictions 后进入 verifier 或修复代码。",
        **payload,
    )
