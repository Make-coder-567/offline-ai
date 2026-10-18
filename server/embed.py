"""本地文本向量化：fastembed + bge-small-zh-v1.5（ONNX，纯 CPU，完全离线）。

模型放在仓库内 chatui/model_assets/embed/... ，通过 specific_model_path 直接指向目录，
因此 fastembed 不会去 HuggingFace 拉取任何东西（断网可用）。

降级策略：
- 没装 fastembed / 模型目录缺失 / 加载失败 → available() 恒为 False，
  调用方（rag.py）自动退回纯 BM25，功能不中断。
- 失败会被记住（_BROKEN），避免在每次检索时反复重试拖慢响应。

用法：
    from server.embed import available, embed
    if available():
        vecs = embed(["第一段", "第二段"])     # (n, 512) float32，已 L2 归一化
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_REL = Path("model_assets") / "embed" / "BAAI" / "bge-small-zh-v1.5"
_REQUIRED_FILES = ("model_optimized.onnx", "tokenizer.json")

_lock = threading.Lock()
_model = None
_broken: str | None = None
_dim: int | None = None


def _candidates() -> list[Path]:
    root = Path(__file__).resolve().parent.parent          # 项目根（server/ 的上级）
    out = []
    env = os.environ.get("OFFLINE_EMBED_DIR")
    if env:
        out.append(Path(env))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:                                            # PyInstaller 单文件解压目录
        out.append(Path(meipass) / _REL)
    out.append(root / "chatui" / _REL)                     # 开发态
    out.append(root / _REL)                                # 另一种打包布局
    return out


def model_dir() -> Path | None:
    """返回第一个"文件齐全"的模型目录；都没有则返回 None。"""
    for d in _candidates():
        try:
            if d.is_dir() and all((d / f).is_file() for f in _REQUIRED_FILES):
                return d
        except OSError:
            continue
    return None


def unavailable_reason() -> str:
    return _broken or ""


def _load():
    """懒加载模型；失败原因写入 _broken，之后不再重试。"""
    global _model, _broken, _dim
    with _lock:
        if _model is not None:
            return _model
        if _broken:
            return None
        d = model_dir()
        if d is None:
            _broken = ("未找到向量模型目录（需含 model_optimized.onnx）："
                       + " / ".join(str(x) for x in _candidates()[:3]))
            return None
        try:
            from fastembed import TextEmbedding
            m = TextEmbedding(model_name=MODEL_NAME, specific_model_path=str(d))
            vec = next(iter(m.embed(["维度探测"])))
            _dim = len(vec)
            _model = m
            return _model
        except Exception as e:                              # 离线环境常见：缺库/模型损坏
            _broken = "向量模型加载失败：%s" % e
            return None


def available() -> bool:
    return _load() is not None


def dim() -> int:
    _load()
    return _dim or 0


def embed(texts) -> "object | None":
    """把一串文本编码成 (n, dim) float32 矩阵（已 L2 归一化）；不可用返回 None。"""
    m = _load()
    if m is None:
        return None
    texts = [t if isinstance(t, str) else str(t) for t in texts]
    if not texts:
        return None
    try:
        import numpy as np
        with _lock:                                        # onnxruntime 会话非线程安全
            rows = [v for v in m.embed(texts)]
        a = np.asarray(rows, dtype="float32")
        norm = np.linalg.norm(a, axis=1, keepdims=True)
        return a / (norm + 1e-9)
    except Exception as e:
        global _broken
        _broken = "向量化失败：%s" % e
        return None


def embed_one(text: str):
    """单条文本 → (dim,) float32；不可用返回 None。"""
    m = _load()
    if m is None:
        return None
    try:
        import numpy as np
        with _lock:
            v = next(iter(m.embed([text])))
        a = np.asarray(v, dtype="float32")
        return a / (np.linalg.norm(a) + 1e-9)
    except Exception as e:
        global _broken
        _broken = "向量化失败：%s" % e
        return None


def reset():
    """仅供测试：丢弃已加载模型与失败记忆。"""
    global _model, _broken, _dim
    _model, _broken, _dim = None, None, None
