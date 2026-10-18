"""离线语音：录音→转写（faster-whisper）、文本→朗读（piper），全程本地不出网。

模型（随包发布，无需联网）：
  model_assets/stt/faster-whisper-base          语音识别（CTranslate2 int8）
  model_assets/tts/zh_CN-huayan-medium/*.onnx   中文语音合成

组件缺失时所有 available() 返回 False，UI 自动隐藏语音按钮，不影响其他功能。
播放用标准库 winsound（异步），不引入 QtMultimedia 插件依赖。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

SAMPLE_RATE = 16000
MAX_RECORD_SECONDS = 120
MAX_TTS_CHARS = 1200          # 太长既慢又没必要，读完前 1200 字即可

_missing: dict[str, str] = {}


def assets_dir() -> Path:
    """模型资源目录：打包态取 _MEIPASS/model_assets，开发态取 chatui/model_assets。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
        return base / "model_assets"
    here = Path(__file__).resolve().parent          # chatui/
    return here / "model_assets"


def stt_dir() -> Path:
    return assets_dir() / "stt" / "faster-whisper-base"


def tts_model() -> Path:
    return assets_dir() / "tts" / "zh_CN-huayan-medium" / "zh_CN-huayan-medium.onnx"


def _require(name: str, mod: str):
    if name in _missing:
        return None
    try:
        return __import__(mod)
    except Exception as e:
        _missing[name] = str(e)
        return None


def _installed(mod: str) -> bool:
    """只探测不导入：可用性检查在启动路径上，导入 faster_whisper 要 1-2s。"""
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def stt_available() -> bool:
    return (stt_dir().is_dir() and _installed("sounddevice")
            and _installed("faster_whisper"))


def tts_available() -> bool:
    return tts_model().is_file() and _installed("piper")


def unavailable_reason() -> str:
    return "; ".join(f"{k}: {v}" for k, v in _missing.items())


# ---------------- 录音 ----------------
_LOOPBACK_HINTS = ("立体声混音", "stereo mix", "loopback", "wave out", "monitor",
                   "what u hear", "混音")


def _is_loopback(name: str) -> bool:
    """回环/混音设备录的是"电脑放出来的声音"，不是人说话，语音输入要排除。"""
    low = (name or "").lower()
    return any(h in low for h in _LOOPBACK_HINTS)


def _enumerate_inputs():
    """返回 [(索引, 名称, 是否回环)]；查询失败返回空列表。"""
    sd = _require("sounddevice", "sounddevice")
    if sd is None:
        return []
    try:
        devs = sd.query_devices()
    except Exception:
        return []
    out = []
    for i, d in enumerate(devs):
        try:
            if int(d.get("max_input_channels", 0)) > 0:
                name = d.get("name", "") or ""
                out.append((i, name, _is_loopback(name)))
        except Exception:
            continue
    return out


def pick_input_device(allow_loopback: bool = True) -> int:
    """挑一个能用的输入设备索引；没有则返回 -1。

    Windows 上常见 `sd.default.device = (-1, 1)`（没设默认输入设备），
    此时 query_devices(kind="input") 直接抛 "Error querying device -1"。
    所以这里自己枚举：优先系统默认 → 第一个真麦克风 → 最后才是回环设备。
    """
    sd = _require("sounddevice", "sounddevice")
    if sd is None:
        return -1
    try:
        dflt = int(sd.default.device[0])
    except Exception:
        dflt = -1
    inputs = _enumerate_inputs()
    if not inputs:
        return -1
    real = [t for t in inputs if not t[2]]           # 真麦克风优先
    pool = real + ([t for t in inputs if t[2]] if allow_loopback else [])
    if not pool:
        return -1
    for i, _name, _loop in pool:                     # 池子里优先系统默认
        if i == dflt:
            return i
    return pool[0][0]


def _device_name(idx: int) -> str:
    sd = _require("sounddevice", "sounddevice")
    if sd is None or idx < 0:
        return "麦克风"
    try:
        return sd.query_devices(idx).get("name", "") or "麦克风"
    except Exception:
        return "麦克风"


def input_device_name() -> str:
    """返回将被使用的麦克风名称；没有可用设备返回空串。"""
    idx = pick_input_device(allow_loopback=False)
    return _device_name(idx) if idx >= 0 else ""


def has_input_device() -> bool:
    """是否真的有"能录人声"的设备（回环/立体声混音不算）。"""
    return pick_input_device(allow_loopback=False) >= 0


NO_MIC_TIP = ("未检测到可用的麦克风。请插入麦克风，或在「Windows 设置 → 系统 → 声音 → 输入」"
              "里选择一个设备；同时确认「隐私和安全性 → 麦克风」已允许桌面应用访问。")


def _resample(x, src: int, dst: int = SAMPLE_RATE):
    """单声道 float32 线性重采样（麦克风不支持 16k 时用）。"""
    if not len(x) or src == dst:
        return x
    import numpy as np
    n = max(1, int(round(len(x) * dst / float(src))))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)),
                     x).astype("float32")


def _open_stream(sd, sample_rate: int, callback=None):
    """打开输入流；设备不支持 16k 时自动退到设备默认采样率。返回 (stream, 实际采样率)。

    必须用 callback 模式：Windows 默认 host API 是 WASAPI，PortAudio 的
    Stream.read()（阻塞读）在 WASAPI 下会抛 paUnanticipatedHostError(-9999)
    "Blocking API not supported yet"，所以采集一律走回调。
    """
    idx = pick_input_device()
    if idx < 0:
        raise RuntimeError(NO_MIC_TIP)
    name = _device_name(idx)
    rate = sample_rate
    try:
        sd.check_input_settings(device=idx, samplerate=rate, channels=1)
    except Exception:
        try:
            rate = int(sd.query_devices(idx).get("default_samplerate") or sample_rate)
        except Exception:
            rate = sample_rate
    try:
        stream = sd.InputStream(device=idx, samplerate=rate, channels=1,
                                dtype="float32", callback=callback,
                                blocksize=0)
        stream.start()
        return stream, rate, name
    except Exception as e:
        # 说清楚是"哪个设备打不开"，而不是把 PortAudio 的 -1 直接甩给用户
        raise RuntimeError("无法打开录音设备「%s」：%s。可能被其他程序占用，"
                           "或系统「隐私和安全性 → 麦克风」未允许桌面应用访问；"
                           "请在「设置 → 系统 → 声音 → 输入」里换一个麦克风后重试。"
                           % (name, e))


def record(seconds: float = 10.0, sample_rate: int = SAMPLE_RATE):
    """阻塞录音整整 seconds 秒，返回 16k float32 一维数组。"""
    import numpy as np
    seconds = max(1.0, min(float(seconds), MAX_RECORD_SECONDS))
    return record_until(lambda: False, max_seconds=seconds, sample_rate=sample_rate)


def record_until(should_stop, max_seconds: float = MAX_RECORD_SECONDS,
                 sample_rate: int = SAMPLE_RATE):
    """边录边等停止信号：should_stop() 返回 True 或超时即结束。

    UI 的"再点一次结束"靠它实现——不能一次性录满 120 秒再处理。
    """
    sd = _require("sounddevice", "sounddevice")
    if sd is None:
        raise RuntimeError("未安装 sounddevice：%s" % _missing.get("sounddevice"))
    import numpy as np
    chunks = []
    lock = threading.Lock()

    def _cb(indata, frames, time_info, status):      # PortAudio 自己的线程回调
        try:
            with lock:
                chunks.append(np.asarray(indata, dtype="float32").reshape(-1).copy())
        except Exception:
            pass

    stream, rate, dev_name = _open_stream(sd, sample_rate, callback=_cb)
    t0 = time.time()
    try:
        while time.time() - t0 < max_seconds:
            if should_stop and should_stop():
                break
            time.sleep(0.03)
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
    elapsed = time.time() - t0
    with lock:
        got = list(chunks)
    if not got:
        if elapsed > 0.8:
            # 设备能打开却一帧都没来：多半是权限/占用/假设备，直接说清楚
            raise RuntimeError(
                "麦克风「%s」已打开但没收到任何音频（%.1f 秒）。可能原因：① 被其他程序独占；"
                "②「隐私和安全性 → 麦克风」未允许桌面应用访问；③ 该设备当前未真正接入。"
                "请在系统声音设置里确认后重试。" % (dev_name, elapsed))
        return np.zeros(0, dtype="float32")
    return _resample(np.concatenate(got), rate)


# ---------------- 转写 ----------------
_stt = None


def _stt_model():
    global _stt
    if _stt is None:
        fw = _require("faster_whisper", "faster_whisper")
        if fw is None:
            raise RuntimeError("未安装 faster_whisper：%s" % _missing.get("faster_whisper"))
        d = stt_dir()
        if not d.is_dir():
            raise RuntimeError("缺少语音模型目录：%s" % d)
        # 直接给目录：不经过 av.decode_audio，避免 PyAV 新版参数不兼容
        _stt = fw.WhisperModel(str(d), device="cpu", compute_type="int8")
    return _stt


def transcribe(audio, language: str = "zh") -> str:
    """把 float32 单声道音频转写成文字；失败抛异常。"""
    m = _stt_model()
    segments, _info = m.transcribe(audio, language=language, beam_size=1,
                                   vad_filter=True)
    return "".join(s.text for s in segments).strip()


# ---------------- 合成 / 播放 ----------------
_tts = None


def _tts_voice():
    global _tts
    if _tts is None:
        piper = _require("piper", "piper")
        if piper is None:
            raise RuntimeError("未安装 piper-tts：%s" % _missing.get("piper"))
        model = tts_model()
        if not model.is_file():
            raise RuntimeError("缺少语音合成模型：%s" % model)
        _tts = piper.PiperVoice.load(str(model))
    return _tts


def synthesize(text: str) -> bytes:
    """文本 → WAV 字节（朗读前会做一次轻量清洗）。"""
    v = _tts_voice()
    text = _clean_for_speech(text)[:MAX_TTS_CHARS]
    if not text:
        return b""
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        with wave.open(path, "wb") as wf:
            v.synthesize_wav(text, wf)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


_CODE_BLOCK = re.compile(r"```.*?```", re.S)


def _clean_for_speech(text: str) -> str:
    """去掉 Markdown 标记与代码块，免得把 # 和 * 一个个念出来。"""
    t = _CODE_BLOCK.sub("（此处有代码，已省略）", text or "")
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"^\s*[#>\-\*\+]+\s*", "", t, flags=re.M)
    t = t.replace("**", "").replace("*", "").replace("_", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


_speaking = False


def is_speaking() -> bool:
    return _speaking


def speak(text: str) -> bool:
    """后台线程朗读（winsound 同步播放 + 线程包装）；返回是否启动成功。"""
    global _speaking
    try:
        import winsound
    except Exception:
        return False
    try:
        data = synthesize(text)
    except Exception:
        return False
    if not data:
        return False
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="offline_tts_")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(data)

    def _play():
        global _speaking
        _speaking = True
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME)
        except Exception:
            pass
        finally:
            _speaking = False
            try:
                os.unlink(path)          # 播放结束才删，删早了会静音
            except OSError:
                pass

    threading.Thread(target=_play, daemon=True).start()
    return True


def stop_speaking():
    global _speaking
    try:
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass
    _speaking = False
