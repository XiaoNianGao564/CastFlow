import json
import os
import subprocess
import sys
import tempfile

from langchain_core.tools import tool

from castflow.config import settings


@tool
def run_python(code: str) -> dict:
    """在隔离子进程中执行 Python 代码并捕获 stdout/stderr。

    约定：代码末尾必须 print(json.dumps({"predictions": [数字...]}))，
    本工具会把最后一行 JSON 解析后放在返回的 parsed 字段。

    可直接 import pandas / numpy / statsmodels / sklearn / lightgbm。
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        path = f.name
    try:
        r = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            timeout=settings.python_timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
        parsed = None
        for line in reversed((r.stdout or "").strip().split("\n")):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        return {
            "returncode": r.returncode,
            "stdout": (r.stdout or "")[-3000:],
            "stderr": (r.stderr or "")[-1500:],
            "parsed": parsed,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"timeout {settings.python_timeout_sec}s"}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
