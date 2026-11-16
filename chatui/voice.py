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
        # 让 espeak-ng 找到打包后的数据目录（含中文音素化所需的 phontab / cmn_dict 等）。
        # 不设置且数据未被打包时，espeak-ng 会回退到编译时硬编码的构建机路径而报错/出静音。
        try:
            _ed = os.path.join(os.path.dirname(os.path.abspath(piper.__file__)),
                               "espeak-ng-data")
            if os.path.isdir(_ed):
                os.environ.setdefault("ESPEAK_DATA_PATH", _ed)
        except Exception:
            pass
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


# ---------------- 合成 / 播放（后台线程，绝不阻塞 GUI） ----------------
# 设计权衡：早期版本用「每点一次朗读就开一个子进程」做崩溃隔离，但本程序是
# PyInstaller onefile 单文件打包——每次子进程启动都会把 460MB+ 的归档重新解包到
# %TEMP%/_MEIxxxx（该目录每次新建、不复用），既造成每次点击卡顿十几秒，又持续
# 往磁盘写 460MB 临时目录直到占满。而实测冻结态 piper 合成不会原生崩溃（退出码 0），
# 因此改用后台线程：零额外解包、即时响应，且通过 winsound.SND_PURGE 能真正中断播放
# （子进程方案因 SND_PURGE 只在进程内生效，反而停不掉另一进程的播放，是个隐藏 bug）。
_current_thread = None
_current_stop = None


def is_speaking() -> bool:
    return _current_thread is not None and _current_thread.is_alive()


def stop_speaking():
    """停止朗读：置停止标志 + 清掉正在播放的声音。

    播放是在后台线程里同步调用的 winsound.PlaySound；SND_PURGE 在进程内生效，
    因此从 GUI 线程发起即可中断后台线程的播放，按钮随后由 on_done / on_error 复位。
    """
    global _current_thread, _current_stop
    if _current_stop is not None:
        _current_stop.set()
    try:
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def speak(text: str, on_done=None, on_error=None) -> bool:
    """在后台线程里合成并朗读，GUI 线程绝不阻塞、也绝不被合成耗时拖卡。

    返回 True 表示朗读线程已启动；返回 False 表示预检失败（触发 on_error）。
    on_done：播放完成；on_error：预检失败或合成/播放异常。
    调用方请保证 on_done / on_error 线程安全（GUI 侧用 QTimer.singleShot 抛回主线程）。
    """
    global _current_thread, _current_stop
    stop_speaking()                      # 先停下上一段，避免叠加

    try:
        import winsound
    except Exception:
        if on_error:
            on_error("当前系统不支持语音朗读（缺少 winsound 模块）")
        return False

    if not tts_available():
        if on_error:
            on_error("朗读组件不可用（缺少语音模型或 piper 依赖）")
        return False

    cleaned = _clean_for_speech(text or "")[:MAX_TTS_CHARS]
    if not cleaned:
        if on_error:
            on_error("没有可朗读的内容")
        return False

    stop = threading.Event()
    _current_stop = stop

    def _run():
        global _current_thread
        try:
            data = synthesize(cleaned)
            if stop.is_set() or not data:
                return
            fd, wav = tempfile.mkstemp(suffix=".wav", prefix="offline_tts_")
            os.close(fd)
            try:
                with open(wav, "wb") as f:
                    f.write(data)
                import winsound
                # 后台线程内同步播放：不阻塞 GUI；用户点"停止"会触发 SND_PURGE 中断。
                try:
                    winsound.PlaySound(wav, winsound.SND_FILENAME)
                except Exception:
                    pass
            finally:
                try:
                    os.unlink(wav)
                except OSError:
                    pass
            if stop.is_set():
                return                      # 用户中途停止，不算完成
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass
        except Exception as e:
            if not stop.is_set() and on_error:
                try:
                    on_error("朗读出错：%s" % e)
                except Exception:
                    pass
        finally:
            if _current_thread is threading.current_thread():
                _current_thread = None

    t = threading.Thread(target=_run, daemon=True)
    _current_thread = t
    t.start()
    return True


# ---------------- 诊断入口（仅供测试冻结态 TTS，不参与正常朗读） ----------------
def run_worker_from_file(path: str, dump_path: str | None = None):
    """独立合成 + 播放（或导出 WAV），用于在不启动 GUI 的情况下验证打包后的 TTS。

    设了 OFFLINE_TTS_DUMP 环境变量（或传 --dump）时，把 WAV 写到该路径并额外写
    .diag 日志，方便在 console=False 的冻结包里确认"是否真的合成了有声音频"。
    """
    text = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        _diag("READ_ERROR", str(e))
        return
    if not text:
        return
    try:
        data = synthesize(text)
        if not data:
            _diag("EMPTY", "synthesize returned empty bytes")
            return
        if dump_path:
            with open(dump_path, "wb") as f:
                f.write(data)
            _diag("OK", "bytes=%d" % len(data))
        else:
            fd, wav = tempfile.mkstemp(suffix=".wav", prefix="offline_tts_")
            os.close(fd)
            try:
                with open(wav, "wb") as f:
                    f.write(data)
                import winsound
                winsound.PlaySound(wav, winsound.SND_FILENAME)
            finally:
                try:
                    os.unlink(wav)
                except OSError:
                    pass
    except Exception as e:
        _diag("ERROR", repr(e))


def _diag(status: str, detail: str = ""):
    """把诊断结果写到 dump 同目录的 .diag 文件（console=False 时也能取证）。"""
    import datetime
    dump = os.environ.get("OFFLINE_TTS_DUMP")
    base = (dump + ".diag") if dump else os.path.join(
        tempfile.gettempdir(), "offline_tts_diag.txt")
    try:
        with open(base, "a", encoding="utf-8") as f:
            f.write("[%s] %s %s\n" % (
                datetime.datetime.now().isoformat(timespec="seconds"),
                status, detail))
    except Exception:
        pass
    try:
        sys.stderr.write("%s %s\n" % (status, detail))
    except Exception:
        pass


if __name__ == "__main__":
    # 诊断入口：python -m voice --speak-file <path> [--dump <wav>]
    # 仅用于开发/打包自检；正常朗读走 speak() 的后台线程，不走这里。
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--speak-file", dest="speak_file")
    ap.add_argument("--dump", dest="dump",
                    default=os.environ.get("OFFLINE_TTS_DUMP"))
    ns = ap.parse_args()
    if ns.speak_file:
        run_worker_from_file(ns.speak_file, ns.dump)
