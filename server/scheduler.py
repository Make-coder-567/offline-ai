"""定时任务：按"间隔分钟"或"每天 HH:MM"触发一次本地大模型任务，结果写入 outbox。

落盘：<data_dir>/schedules.json
  [{"id","every_minutes"|"daily_at","prompt","model","enabled","last_run"}]

运行：python server/scheduler.py       （前台常驻，每 20 秒检查一次）
也可在 agent 进程里 import 后起线程。结果写 <workspace>/.offline-bridge/outbox，
并追加一行到 <data_dir>/schedule.log，便于事后查看。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVER))

POLL_SECONDS = 20
MAX_PROMPT_CHARS = 4000


def schedules_path(data_dir) -> Path:
    return Path(data_dir) / "schedules.json"


def log_path(data_dir) -> Path:
    return Path(data_dir) / "schedule.log"


def _load(data_dir) -> list:
    p = schedules_path(data_dir)
    if not p.is_file():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return list(json.load(f))
    except Exception:
        return []


def _save(data_dir, rows: list):
    p = schedules_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def add(data_dir, prompt: str, every_minutes: int = 0, daily_at: str = "",
        model: str = "") -> dict:
    """新增任务：every_minutes 与 daily_at 二选一（daily_at 形如 "09:00"）。"""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("prompt 过长")
    if not every_minutes and not daily_at:
        raise ValueError("需要 every_minutes 或 daily_at")
    if daily_at and not _valid_hhmm(daily_at):
        raise ValueError("daily_at 需形如 09:00")
    rows = _load(data_dir)
    item = {"id": "t%d" % int(time.time() * 1000),
            "every_minutes": int(every_minutes or 0),
            "daily_at": daily_at or "",
            "prompt": prompt, "model": model or "",
            "enabled": True, "last_run": ""}
    rows.append(item)
    _save(data_dir, rows)
    return item


def _valid_hhmm(s: str) -> bool:
    try:
        h, m = str(s).split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except Exception:
        return False


def remove(data_dir, task_id: str) -> bool:
    rows = _load(data_dir)
    keep = [r for r in rows if r.get("id") != task_id]
    _save(data_dir, keep)
    return len(keep) != len(rows)


def list_all(data_dir) -> list:
    return _load(data_dir)


def _due(item: dict, now: float) -> bool:
    if not item.get("enabled"):
        return False
    last = item.get("last_run") or ""
    if item.get("every_minutes"):
        if not last:
            return True
        try:
            return now - float(last) >= int(item["every_minutes"]) * 60
        except Exception:
            return True
    if item.get("daily_at"):
        hhmm = time.strftime("%H:%M", time.localtime(now))
        if hhmm != item["daily_at"]:
            return False
        return not (last and time.strftime("%Y-%m-%d", time.localtime(float(last)))
                    == time.strftime("%Y-%m-%d", time.localtime(now)))
    return False


def run_due(data_dir, runner, dry_run: bool = False) -> int:
    """触发到期任务；runner(prompt, model) -> 结果文本。返回执行条数。"""
    rows = _load(data_dir)
    now = time.time()
    fired = 0
    for item in rows:
        if not _due(item, now):
            continue
        if dry_run:
            print("[scheduler] would run %s" % item["id"], flush=True)
            item["last_run"] = str(now)
            fired += 1
            continue
        try:
            out = runner(item.get("prompt", ""), item.get("model") or "")
        except Exception as e:
            out = "[error] %s" % e
        item["last_run"] = str(now)
        fired += 1
        try:
            with open(log_path(data_dir), "a", encoding="utf-8") as f:
                f.write("[%s] %s\n%s\n\n" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"), item.get("id"),
                    (out or "")[:2000]))
        except Exception:
            pass
    if fired:
        _save(data_dir, rows)
    return fired


def loop(data_dir, runner, stop_flag=None):
    while not (stop_flag and stop_flag()):
        try:
            run_due(data_dir, runner)
        except Exception:
            pass
        time.sleep(POLL_SECONDS)


def start_thread(data_dir, runner):
    """在 agent/UI 进程里后台跑调度器（守护线程）。"""
    t = threading.Thread(target=loop, args=(data_dir, runner), daemon=True)
    t.start()
    return t


def _default_runner(prompt: str, model: str = "") -> str:
    import agent as ag
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    os.environ.setdefault("OLLAMA_HOST", str(cfg.get("ollama_base") or
                                             "http://127.0.0.1:11434"))
    return ag.run_llm(cfg, prompt, temperature=0.3, model=model or None)


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "chatui" / "data")
    print("[scheduler] data=%s  每 %d 秒检查一次" % (data_dir, POLL_SECONDS), flush=True)
    loop(data_dir, _default_runner)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[scheduler] stopped", flush=True)
