"""多会话存储：一个会话一个 JSON，索引单独存，支持重命名/删除/导出 Markdown。

目录：<data_dir>/sessions/
  index.json      [{"id","title","updated","count"}...]
  <sid>.json      {"model", "messages":[{role,text,who,ts,hist}...], "title"}

迁移：旧的 <data_dir>/session.json 首次启动时自动搬成 default 会话，不丢历史。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT_SID = "default"
MAX_TITLE = 24


def _atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def auto_title(data: dict) -> str:
    """用第一条用户消息当会话名，省得用户手动起名。"""
    for m in (data or {}).get("messages") or []:
        if m.get("role") == "me":
            t = " ".join(str(m.get("text", "")).split())
            return (t[:MAX_TITLE] + "…") if len(t) > MAX_TITLE else (t or "新会话")
    return "新会话"


class SessionStore:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir) / "sessions"
        self.index_path = self.dir / "index.json"

    # ---------- 索引 ----------
    def _read_index(self) -> list:
        if not self.index_path.is_file():
            return self._rebuild_index()
        try:
            with open(self.index_path, encoding="utf-8") as f:
                d = json.load(f)
            rows = list(d.get("sessions") or [])
            # 索引存在但为空、磁盘上却有会话文件：说明索引被清过/写坏，重建一次
            return rows or self._rebuild_index()
        except Exception:
            # 索引损坏：先备份，再从会话文件重建，避免"会话凭空消失"
            try:
                bad = self.index_path.with_name(
                    "index.bad-%d.json" % int(time.time()))
                self.index_path.replace(bad)
            except Exception:
                pass
            return self._rebuild_index()

    def _rebuild_index(self) -> list:
        """扫描目录里的会话文件重建索引（标题取文件内记录，时间取 mtime）。"""
        rows = []
        try:
            files = sorted(self.dir.glob("*.json"))
        except Exception:
            return rows
        for p in files:
            if p.name.startswith("index"):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            msgs = data.get("messages") or []
            ts = int(p.stat().st_mtime * 1000)
            rows.append({
                "id": p.stem,
                "title": (data.get("title") or "新会话")[:60],
                "count": len(msgs),
                "model": data.get("model") or "",
                "updated": time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.localtime(ts / 1000)),
                "updated_ts": ts,
                "pinned": bool(data.get("pinned")),
            })
        if rows:
            rows.sort(key=lambda r: (bool(r.get("pinned")),
                                     int(r.get("updated_ts") or 0)), reverse=True)
            try:
                self._write_index(rows)
            except Exception:
                pass
        return rows

    def _write_index(self, rows: list):
        _atomic_write(self.index_path,
                      json.dumps({"sessions": rows}, ensure_ascii=False, indent=1))

    def all(self) -> list:
        rows = self._read_index()
        # 置顶优先，再按毫秒时间戳（字符串只到秒，同秒多次写入分不出先后）
        rows.sort(key=lambda r: (bool(r.get("pinned")),
                                 int(r.get("updated_ts") or 0)), reverse=True)
        return rows

    def set_pinned(self, sid: str, pinned: bool) -> bool:
        """置顶/取消置顶：只动索引，不重写会话正文。"""
        rows = self._read_index()
        hit = False
        for r in rows:
            if r.get("id") == sid:
                r["pinned"] = bool(pinned)
                hit = True
        if hit:
            self._write_index(rows)
        return hit

    def meta(self, sid: str) -> dict:
        for r in self._read_index():
            if r.get("id") == sid:
                return r
        return {}

    def path_for(self, sid: str) -> Path:
        return self.dir / ("%s.json" % sid)

    # ---------- 读写 ----------
    def get(self, sid: str) -> dict:
        p = self.path_for(sid)
        if not p.is_file():
            return {}
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def put(self, sid: str, data: dict, title: str = None):
        """写入会话内容并同步索引（标题未指定时自动取首条用户消息）。"""
        prev_row = self.meta(sid)
        pinned = bool(prev_row.get("pinned"))
        keep = title or (data.get("title") if isinstance(data, dict) else None)
        if not keep:
            prev = self.meta(sid).get("title") or ""
            # 已有名字且不是自动生成的默认名就别覆盖；否则跟内容走
            keep = prev if prev and prev != "新会话" else auto_title(data)
        data = dict(data or {})
        data["title"] = keep
        _atomic_write(self.path_for(sid),
                      json.dumps(data, ensure_ascii=False, indent=1))
        rows = [r for r in self._read_index() if r.get("id") != sid]
        rows.append({"id": sid, "title": keep, "pinned": pinned,
                     "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "updated_ts": int(time.time() * 1000),
                     "count": len(data.get("messages") or [])})
        self._write_index(rows)
        return keep

    def ensure(self, sid: str = None) -> str:
        sid = sid or DEFAULT_SID
        if not self.path_for(sid).is_file():
            self.put(sid, {"model": "", "messages": []}, title="新会话")
        return sid

    def new_id(self) -> str:
        return "s%d" % int(time.time() * 1000)

    def create(self, title: str = "新会话") -> str:
        sid = self.new_id()
        while self.path_for(sid).is_file():
            time.sleep(0.01)
            sid = self.new_id()
        self.put(sid, {"model": "", "messages": []}, title=title)
        return sid

    def rename(self, sid: str, title: str) -> bool:
        title = (title or "").strip()[:60] or "新会话"
        data = self.get(sid)
        if not data and not self.meta(sid):
            return False
        self.put(sid, data, title=title)
        return True

    def delete(self, sid: str) -> bool:
        try:
            p = self.path_for(sid)
            if p.is_file():
                p.unlink()
        except OSError:
            return False
        self._write_index([r for r in self._read_index() if r.get("id") != sid])
        return True

    # ---------- 导出 ----------
    def export_md(self, sid: str, dest=None) -> Path:
        data = self.get(sid)
        title = data.get("title") or self.meta(sid).get("title") or sid
        lines = ["# %s" % title, "",
                 "> 导出时间：%s　模型：%s" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                             data.get("model") or "-"),
                 ""]
        for m in data.get("messages") or []:
            who = m.get("who") or ("你" if m.get("role") == "me" else "AI")
            ts = m.get("ts") or ""
            lines.append("### %s　%s" % (who, ts))
            lines.append("")
            lines.append(str(m.get("text", "")))
            lines.append("")
        dest = Path(dest) if dest else self.dir / ("%s.md" % sid)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(lines), encoding="utf-8")
        return dest

    # ---------- 迁移 ----------
    def migrate_legacy(self, legacy: Path) -> bool:
        """把旧的 session.json 搬进 sessions/default.json（只做一次）。"""
        legacy = Path(legacy)
        if not legacy.is_file() or self.path_for(DEFAULT_SID).is_file():
            return False
        try:
            with open(legacy, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        if not (data.get("messages") or []):
            return False
        self.put(DEFAULT_SID, data)
        try:
            legacy.rename(str(legacy) + ".migrated")
        except OSError:
            pass
        return True
