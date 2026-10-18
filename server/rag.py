"""本地 RAG（BM25 检索）：不依赖 embeddings，纯 Python 实现。

背景：本机 Ollama 构建未开放 embeddings 接口，但离线检索仍可工作。
方案：中文按字符 bigram、英文按单词分词，BM25 打分，索引落盘为 JSON。

v2：接入 server/docparse.py，PDF / DOCX / XLSX / PPTX / EPUB / 图片OCR 也入库；
    每个 chunk 带 src_type（text|doc|image）便于前端标注与后续向量化分流。

用法（也可通过 MCP 工具 local_rag_index / local_rag_query / local_rag_answer 调用）：
    idx = RagIndex(workspace_root)
    idx.build()
    hits = idx.query("传感器控制", top_k=4)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from pathlib import Path

_TEXT_EXTS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".csv", ".log", ".ini", ".toml"}
_SKIP_DIRS = {".git", ".offline-rag", ".offline-bridge", "__pycache__", "node_modules",
              "gguf_models", "bundle_models", "dist", "build", ".venv", "venv", "data"}
_SENSITIVE = {".env", ".ssh", "id_rsa", "id_ed25519", "secrets", "credentials", ".netrc"}
_SENSITIVE_SUFFIX = {".pem", ".key", ".p12", ".pfx", ".keystore", ".kdbx"}

_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

MAX_FILES = 400             # 纯文本文件上限
MAX_DOC_FILES = 80          # 二进制文档（PDF/Office）上限
MAX_IMAGE_FILES = 20        # 图片（走 OCR）上限
MAX_FILE_BYTES = 1 * 1024 * 1024
MAX_DOC_BYTES = 50 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
CHUNK_CHARS = 450
CHUNK_OVERLAP = 60

# ---- 混合检索（BM25 + 稠密向量，加权 RRF 融合）----
VEC_W_BM25 = 0.55          # 关键词命中仍是主力（中文专名/数字/缩写靠它）
VEC_W_DENSE = 0.45         # 语义召回负责"说法不同但意思一样"
RRF_K = 60                 # RRF 平滑系数
CANDIDATE_TOP = 20         # 两路各自取前 N 参与融合
MAX_VEC_CHUNKS = 4000      # 超过则不再算向量（纯 BM25），防 npy 膨胀
VEC_TEXT_CHARS = 1200      # 向量化只取片段前 1200 字（bge 上限 512 token）
VEC_BATCH = 800            # 单次补算向量的上限，避免首次检索卡死

INDEX_VERSION = 2

_DOC_EXTS_FALLBACK = {".pdf", ".docx", ".xlsx", ".pptx", ".epub"}
_OCR_EXTS_FALLBACK = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _docparse():
    """懒加载 docparse；未随包发布时返回 None，自动退化为纯文本检索。"""
    try:
        from server import docparse
        return docparse
    except Exception:
        try:
            import docparse  # type: ignore
            return docparse
        except Exception:
            return None


def _kind_of(suffix: str) -> str:
    dp = _docparse()
    doc_exts = getattr(dp, "DOC_EXTS", _DOC_EXTS_FALLBACK) if dp else _DOC_EXTS_FALLBACK
    ocr_exts = getattr(dp, "OCR_EXTS", _OCR_EXTS_FALLBACK) if dp else _OCR_EXTS_FALLBACK
    if suffix in doc_exts:
        return "doc"
    if suffix in ocr_exts:
        return "image"
    return "text"


def _chunk_id(rel: str, text: str) -> str:
    """片段稳定 id：向量按 id 增量补算，文本变了才会重算。"""
    return hashlib.sha1(("%s\n%s" % (rel, text[:200])).encode("utf-8")).hexdigest()[:16]


def _read_text(path: Path, kind: str, use_ocr: bool) -> str:
    """按类型取文本；失败返回空串（build 里统计到 skipped）。"""
    if kind == "text":
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    dp = _docparse()
    if dp is None:
        return ""
    try:
        return dp.extract_text(path, ocr_fallback=use_ocr)
    except Exception as e:
        return f"[error] {e}"


def tokenize(text: str) -> list[str]:
    """英文/数字按词，中文按字符 bigram（单字成段时保留单字）。"""
    toks = [w.lower() for w in _WORD_RE.findall(text)]
    for seg in _CJK_RE.findall(text):
        if len(seg) == 1:
            toks.append(seg)
        else:
            toks.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return toks


def chunk_text(text: str) -> list[str]:
    """按字符数切块，尽量在换行处断开，块间保留少量重叠。"""
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + CHUNK_CHARS)
        if end < n:
            nl = text.rfind("\n", start + CHUNK_CHARS // 2, end)
            if nl > start:
                end = nl
        piece = text[start:end].strip()
        last = end >= n
        # 末尾块即使很短也保留（否则短文档整篇被丢弃、无法检索）
        if len(piece) >= 40 or (last and not chunks and piece):
            chunks.append(piece)
        if last:
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


class RagIndex:
    def __init__(self, workspace_root: str | Path):
        self.root = Path(workspace_root).resolve()
        self.dir = self.root / ".offline-rag"
        self.path = self.dir / "index.json"
        self.vec_path = self.dir / "vectors.npy"
        self.data: dict | None = None

    # ---------- 扫描 / 建库 ----------
    def _scan(self, use_ocr: bool = True) -> dict:
        """返回 rel -> [mtime, size, kind]；kind ∈ text/doc/image。"""
        files: dict[str, list] = {}
        n_text = n_doc = n_img = 0
        dp = _docparse()
        all_exts = set(_TEXT_EXTS)
        if dp is not None:
            all_exts |= set(dp.SUPPORTED_EXTS) | set(dp.OCR_EXTS)
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                p = Path(dirpath) / fn
                ext = p.suffix.lower()
                kind = _kind_of(ext)
                if ext not in all_exts:
                    continue
                if kind == "image" and not use_ocr:
                    continue
                # .env / .env.local 以及 secrets.json / credentials.yaml 等一律不索引
                stem = p.stem.lower()
                if (fn.lower() in _SENSITIVE or stem in _SENSITIVE
                        or fn.lower().startswith(".env")
                        or ext in _SENSITIVE_SUFFIX):
                    continue
                try:
                    rp = p.resolve()
                    if not rp.is_relative_to(self.root):
                        continue          # 符号链接指向工作区外，跳过
                    st = rp.stat()
                except (OSError, RuntimeError):
                    continue
                limit = (MAX_FILE_BYTES if kind == "text"
                         else MAX_IMAGE_BYTES if kind == "image"
                         else MAX_DOC_BYTES)
                if st.st_size > limit or st.st_size == 0:
                    continue
                if kind == "text" and n_text >= MAX_FILES:
                    continue
                if kind == "doc" and n_doc >= MAX_DOC_FILES:
                    continue
                if kind == "image" and n_img >= MAX_IMAGE_FILES:
                    continue
                rel = str(p.relative_to(self.root)).replace("\\", "/")
                files[rel] = [int(st.st_mtime), st.st_size, kind]
                if kind == "text":
                    n_text += 1
                elif kind == "doc":
                    n_doc += 1
                else:
                    n_img += 1
        return files

    def _chunks_for(self, rel: str, kind: str, use_ocr: bool,
                    skipped: list) -> tuple[list, int]:
        """为单个文件生成 chunk 列表，返回 (chunks, token 总长)。

        rel 可能是工作区相对路径，也可能是工作区外文件的绝对路径。
        """
        rp = Path(rel)
        p = rp if rp.is_absolute() else self.root / rel
        out = []
        total = 0
        text = _read_text(p, kind, use_ocr)
        if text.startswith("[error]"):
            skipped.append(f"{rel} {text[:80]}")
            return out, 0
        if not text.strip():
            skipped.append(f"{rel} (空内容)")
            return out, 0
        return self._chunks_from_text(rel, text, kind)

    def _chunks_from_text(self, rel: str, text: str, kind: str = "text"):
        """把"已有文本"切成片段（不碰磁盘）。

        add_text 必须走这里：若再调 _read_text 会把文档重新解析一遍，
        扫描件还会因为不带 OCR 而直接切出 0 个片段。
        """
        out, total = [], 0
        for piece in chunk_text(text):
            toks = tokenize(piece)
            if len(toks) < 5:
                continue
            tf = Counter(toks)
            out.append({"path": rel, "text": piece, "tf": dict(tf),
                        "len": len(toks), "src": kind, "id": _chunk_id(rel, piece)})
            total += len(toks)
        return out, total

    def build(self, force: bool = False, use_ocr: bool = True) -> dict:
        files = self._scan(use_ocr)
        self._load()
        if (not force and self.data
                and self.data.get("files") == files
                and self.data.get("chunks")):
            return {"indexed": False, "files": len(files),
                    "chunks": len(self.data["chunks"]),
                    "built_at": self.data.get("built_at")}

        chunks = []
        df = Counter()
        total_len = 0
        skipped = []
        for rel, meta in files.items():
            kind = meta[2] if len(meta) > 2 else _kind_of(Path(rel).suffix.lower())
            got, tl = self._chunks_for(rel, kind, use_ocr, skipped)
            for c in got:
                chunks.append(c)
                for t in c["tf"].keys():
                    df[t] += 1
            total_len += tl

        self.data = {
            "version": INDEX_VERSION,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": files,
            "chunks": chunks,
            "df": dict(df),
            "avgdl": (total_len / len(chunks)) if chunks else 0.0,
            "skipped": skipped,
            "vec_ids": self._surviving_vec_ids(chunks),
        }
        self._save()
        return {"indexed": True, "files": len(files), "chunks": len(chunks),
                "built_at": self.data["built_at"], "skipped": skipped}

    def _surviving_vec_ids(self, chunks) -> list:
        """重建索引后保留仍有效片段的向量，避免每次全量重算。"""
        old = (self.data or {}).get("vec_ids") or []
        if not old:
            return []
        alive = {c.get("id") for c in chunks}
        return [cid for cid in old if cid in alive]

    def key_for(self, path) -> str:
        """文件在索引里的 key：工作区内用相对路径，工作区外用绝对路径。"""
        try:
            p = Path(path).resolve()
        except Exception:
            return str(path).replace("\\", "/")
        if p.is_relative_to(self.root):
            return str(p.relative_to(self.root)).replace("\\", "/")
        return str(p).replace("\\", "/")

    def add_paths(self, paths, use_ocr: bool = True) -> dict:
        """增量入库：只解析指定文件，合并进现有索引（不重建全库）。

        用于"拖入长文档自动检索"与 MCP local_doc_ask。
        """
        self._load()
        base = dict(self.data or {})
        chunks = list(base.get("chunks") or [])
        files = dict(base.get("files") or {})
        added_files, added_chunks, skipped = 0, 0, []

        for raw in paths:
            try:
                p = Path(raw).resolve()
            except Exception:
                skipped.append(f"{raw} (路径非法)")
                continue
            if not p.is_file():
                skipped.append(f"{p.name} (文件不存在)")
                continue
            if p.is_relative_to(self.root):
                rel = str(p.relative_to(self.root)).replace("\\", "/")
            else:                     # 工作区外：用绝对路径做 key，检索时照样能定位
                rel = str(p).replace("\\", "/")
            kind = _kind_of(p.suffix.lower())
            if kind == "image" and not use_ocr:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            chunks = [c for c in chunks if c.get("path") != rel]
            got, _ = self._chunks_for(rel, kind, use_ocr, skipped)
            if not got:
                continue
            for c in got:
                chunks.append(c)
            files[rel] = [int(st.st_mtime), st.st_size, kind]
            added_files += 1
            added_chunks += len(got)

        df = Counter()
        total_len = 0
        for c in chunks:
            for t in (c.get("tf") or {}).keys():
                df[t] += 1
            total_len += c.get("len") or 0

        self.data = {
            "version": INDEX_VERSION,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": files,
            "chunks": chunks,
            "df": dict(df),
            "avgdl": (total_len / len(chunks)) if chunks else 0.0,
            "skipped": skipped,
            "vec_ids": self._surviving_vec_ids(chunks),
        }
        self._save()
        return {"indexed": True, "added_files": added_files,
                "added_chunks": added_chunks, "chunks": len(chunks),
                "skipped": skipped}

    def add_text(self, key: str, text: str, kind: str = "doc") -> dict:
        """把"已经解析好的"文本直接入库（避免重复解析，OCR 文档尤其关键）。"""
        self._load()
        base = dict(self.data or {})
        chunks = [c for c in (base.get("chunks") or []) if c.get("path") != key]
        got, _ = self._chunks_from_text(key, text, kind)
        for c in got:
            chunks.append(c)

        files = dict(base.get("files") or {})
        try:
            st = (Path(key) if Path(key).is_absolute() else self.root / key).stat()
            files[key] = [int(st.st_mtime), st.st_size, kind]
        except OSError:
            files[key] = [0, len(text), kind]

        df = Counter()
        total_len = 0
        for c in chunks:
            for t in (c.get("tf") or {}).keys():
                df[t] += 1
            total_len += c.get("len") or 0
        self.data = {
            "version": INDEX_VERSION,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": files,
            "chunks": chunks,
            "df": dict(df),
            "avgdl": (total_len / len(chunks)) if chunks else 0.0,
            "skipped": [],
            "vec_ids": self._surviving_vec_ids(chunks),
        }
        self._save()
        return {"key": key, "chunks": len(got), "total": len(chunks)}

    def _load(self):
        if self.data is not None:
            return
        if not self.path.is_file():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("version") in (1, INDEX_VERSION):
                for c in d.get("chunks") or []:
                    c.setdefault("src", "text")
                    if not c.get("id"):                    # v1 索引补 id
                        c["id"] = _chunk_id(c.get("path", ""), c.get("text", ""))
                self.data = d
        except Exception:
            self.data = None

    # ---------- 稠密向量（可选；缺模型自动降级为纯 BM25） ----------
    def _load_vecs(self):
        """返回 (ids, matrix)；matrix 为 float16 (n, dim)，缺失/损坏返回 ([], None)。"""
        ids = list(self.data.get("vec_ids") or [])
        if not self.vec_path.is_file():
            # 向量文件被删/未生成：必须把 ids 一并作废，否则 ids 与矩阵行数错位，
            # 后续按 id 查行会取到别人的向量（检索结果悄悄变差）
            return [], None
        try:
            import numpy as np
            a = np.load(self.vec_path)
        except Exception:
            return [], None
        if len(a) != len(ids):
            return [], None                                # 与索引不同步，整体作废重算
        return ids, a

    def _save_vecs(self, ids, mat):
        try:
            import numpy as np
            self.dir.mkdir(parents=True, exist_ok=True)
            # 注意：np.save 会给非 .npy 结尾的路径再补一个 .npy，
            # 必须自己带 .npy 后缀，否则 os.replace 找不到源文件（向量将永久丢盘）
            tmp = str(self.vec_path) + ".tmp.npy"
            np.save(tmp, np.asarray(mat, dtype="float16"))
            os.replace(tmp, self.vec_path)
        except Exception:
            pass

    def ensure_vectors(self, batch: int = VEC_BATCH) -> int:
        """增量补算缺失的片段向量：只算新片段，已算过的原地保留。

        返回本次新算的条数；模型不可用 / 片段过多时返回 0（检索自动走纯 BM25）。
        """
        self._load()
        chunks = self.data.get("chunks") if self.data else None
        if not chunks or len(chunks) > MAX_VEC_CHUNKS:
            return 0
        try:
            from server import embed as emb
        except Exception:
            import embed as emb  # type: ignore
        if not emb.available():
            return 0
        import numpy as np

        ids, mat = self._load_vecs()
        row_of = {cid: r for r, cid in enumerate(ids)}
        alive = {c.get("id") for c in chunks}

        keep_ids = [cid for cid in ids if cid in alive]     # 丢掉已删除片段的陈旧行
        keep_rows = ([mat[row_of[cid]] for cid in keep_ids]
                     if mat is not None and len(mat) else [])
        have = set(keep_ids)
        todo = [c for c in chunks if c.get("id") not in have]
        if not todo and len(keep_ids) == len(ids):
            self.data["vec_ids"] = keep_ids
            return 0
        todo = todo[:batch]
        if not todo:
            self.data["vec_ids"] = keep_ids
            return 0

        new_mat = emb.embed([c["text"][:VEC_TEXT_CHARS] for c in todo])
        if new_mat is None:
            return 0
        blocks = list(keep_rows) + [new_mat[i] for i in range(len(todo))]
        arr = np.asarray(blocks, dtype="float32")
        arr = arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)
        all_ids = keep_ids + [c["id"] for c in todo]
        self.data["vec_ids"] = all_ids
        self._save_vecs(all_ids, arr)
        return len(todo)

    def _dense_ranked(self, idxs, question, limit=CANDIDATE_TOP):
        """返回 [(chunk_idx, cos_sim), ...] 降序；无向量返回 None。"""
        ids, mat = self._load_vecs()
        if mat is None or not ids:
            return None
        try:
            from server import embed as emb
        except Exception:
            import embed as emb  # type: ignore
        qv = emb.embed_one(question or "")
        if qv is None:
            return None
        import numpy as np
        row_of = {cid: r for r, cid in enumerate(ids)}
        chunks = self.data["chunks"]
        pairs = []
        for i in idxs:
            r = row_of.get(chunks[i].get("id"))
            if r is not None:
                pairs.append((i, r))
        if not pairs:
            return None
        rows = np.asarray([r for _, r in pairs])
        sims = np.asarray(mat, dtype="float32")[rows] @ np.asarray(qv, dtype="float32")
        order = np.argsort(-sims)[:limit]
        return [(pairs[k][0], float(sims[k])) for k in order]

    @staticmethod
    def _rrf(ranked_lists, weights):
        """加权 RRF：只看名次不看分数，天然规避两路分数量纲不一致的问题。"""
        fused: dict[int, float] = {}
        for w, ranked in zip(weights, ranked_lists):
            if not ranked:
                continue
            for rank, item in enumerate(ranked, 1):
                idx = item[0]
                fused[idx] = fused.get(idx, 0.0) + w / (RRF_K + rank)
        return sorted(fused.items(), key=lambda x: x[1], reverse=True)

    def _save(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ---------- 检索 ----------
    def query(self, question: str, top_k: int = 4, paths=None,
              hybrid: bool = True) -> list[dict]:
        """paths 给出时只在这些文件（可传子串）内检索，用于单文档问答。

        hybrid=True 且向量模型可用时走 BM25+向量 的加权 RRF 融合；
        否则自动退回纯 BM25（功能不受影响，只是少了语义召回）。
        """
        self._load()
        if not self.data or not self.data.get("chunks"):
            return []
        q = tokenize(question or "")
        if not q:
            return []
        chunks = self.data["chunks"]
        if paths:
            want = [str(x).replace("\\", "/") for x in paths]
            idxs = [i for i, c in enumerate(chunks)
                    if any(w in c.get("path", "") for w in want)]
        else:
            idxs = list(range(len(chunks)))
        if not idxs:
            return []

        ranked = self._bm25_ranked(idxs, q, limit=CANDIDATE_TOP)
        scores = {i: s for i, s in ranked}
        order = ranked
        if hybrid:
            try:
                self.ensure_vectors()
                dense = self._dense_ranked(idxs, question)
            except Exception:
                dense = None
            if dense:
                order = self._rrf([ranked, dense], [VEC_W_BM25, VEC_W_DENSE])
                for i, s in dense:
                    scores.setdefault(i, round(s, 3))
        n = max(1, int(top_k))
        out = []
        for item in order[:n]:
            i = item[0]
            c = chunks[i]
            out.append({"score": round(scores.get(i, 0.0), 3), "path": c["path"],
                        "text": c["text"], "src": c.get("src", "text")})
        return out

    def _bm25_ranked(self, idxs, q, limit=CANDIDATE_TOP):
        """BM25 打分，返回 [(chunk_idx, score), ...] 降序。"""
        chunks = self.data["chunks"]
        df = self.data.get("df") or {}
        N = max(1, len(chunks))
        avgdl = self.data.get("avgdl") or 1.0
        k1, b = 1.5, 0.75
        qset = set(q)
        scored = []
        for i in idxs:
            c = chunks[i]
            dl = c.get("len") or 1
            s = 0.0
            tf = c.get("tf") or {}
            for t in qset:
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
                s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
            if s > 0:
                scored.append((i, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def stats(self) -> dict:
        self._load()
        if not self.data:
            return {"indexed": False}
        files = self.data.get("files") or {}
        kinds = {"text": 0, "doc": 0, "image": 0}
        for meta in files.values():
            k = meta[2] if len(meta) > 2 else "text"
            kinds[k] = kinds.get(k, 0) + 1
        try:
            from server import embed as emb
            hybrid = emb.available()
        except Exception:
            hybrid = False
        return {"indexed": True, "files": len(files), "by_type": kinds,
                "chunks": len(self.data.get("chunks") or []),
                "vectors": len(self.data.get("vec_ids") or []),
                "hybrid": hybrid,
                "built_at": self.data.get("built_at"),
                "version": self.data.get("version", 1)}
