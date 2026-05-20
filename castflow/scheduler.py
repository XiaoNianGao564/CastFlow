"""轮询式调度器 — 真实值到达自动触发预测 run。

启用方式：.env 设置 SCHEDULER_ENABLED=true
工作原理：后台 daemon 线程定时检查 DB 中是否有新真实值出现，
         有则自动创建 forecast run，无则待命。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from threading import Event, Thread

from castflow.config import settings

STATE_PATH = Path("data/scheduler_state.json")


class ActualValueScheduler:
    def __init__(self) -> None:
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._state: dict = self._load_state()

    def _load_state(self) -> dict:
        if STATE_PATH.exists():
            try:
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"triggered": {}}

    def _save_state(self) -> None:
        os.makedirs(STATE_PATH.parent, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    def _get_target_months(self) -> list[str]:
        raw = settings.scheduler_target_months.strip()
        if raw:
            return [m.strip() for m in raw.split(",") if m.strip()]
        year = datetime.now().year
        return [f"{year}-{m:02d}" for m in range(1, 13)]

    def _poll_once(self) -> None:
        from castflow.db import db

        try:
            orgs = db.list_orgs()
        except Exception as e:  # noqa: BLE001
            print(f"[Scheduler] list_orgs 失败: {e}")
            return

        target_months = self._get_target_months()
        triggered_any = False

        for org in orgs:
            for month in target_months:
                key = f"{org}-{month}"
                if key in self._state["triggered"]:
                    continue
                try:
                    actual = db.load_actual(org, month)
                except Exception as e:  # noqa: BLE001
                    print(f"[Scheduler] load_actual({org}, {month}) 失败: {e}")
                    continue
                if actual is not None:
                    print(f"[Scheduler] 检测到新真实值: {org} {month} = {actual}")
                    self._trigger_run(org, month)
                    self._state["triggered"][key] = datetime.now().isoformat()
                    triggered_any = True

        if triggered_any:
            self._save_state()

    def _trigger_run(self, org: str, month: str) -> None:
        from api.schemas import ForecastRequest
        from api.server import _make_initial, _runner
        from castflow.ui.session_store import STORE

        req = ForecastRequest(org=org, target_month=month, require_approval=False)
        initial, config, thread_id = _make_initial(req)
        session = STORE.create(thread_id, config, initial)
        session.status = "starting"
        print(f"[Scheduler] 触发预测 run: {thread_id} ({org} {month})")
        thread = Thread(target=_runner, args=(session,), daemon=True)
        thread.start()

    def _loop(self) -> None:
        print(f"[Scheduler] 启动，轮询间隔 {settings.scheduler_interval_sec}s，"
              f"监控月份: {self._get_target_months()}")
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as e:  # noqa: BLE001
                print(f"[Scheduler] 轮询异常: {e}")
            self._stop_event.wait(timeout=settings.scheduler_interval_sec)
        print("[Scheduler] 已停止")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def triggered_count(self) -> int:
        return len(self._state.get("triggered", {}))

    @property
    def triggered_records(self) -> dict:
        return dict(self._state.get("triggered", {}))

    def reset_state(self) -> None:
        self._state = {"triggered": {}}
        self._save_state()

    def remove_triggered(self, key: str) -> bool:
        if key in self._state.get("triggered", {}):
            del self._state["triggered"][key]
            self._save_state()
            return True
        return False
