"""Thin wrappers around real device streams. Each wrapper owns one stream and
feeds/drains a mixer object. Callbacks never raise: any exception becomes
silence plus an error flag the engine can report.
"""
from __future__ import annotations

import logging
import time

import numpy as np
import sounddevice as sd

from backend.engine.devices import OS, DeviceInfo
from backend.engine.mixer import CH, SR, OutputMixer, SourceNode
from backend.engine.resampler import StreamResampler

log = logging.getLogger(__name__)


def _to_stereo(block: np.ndarray) -> np.ndarray:
    if block.shape[1] == CH:
        return block
    if block.shape[1] == 1:
        return np.repeat(block, CH, axis=1)
    return block[:, :CH]  # multichannel: keep front L/R


def _extra():
    # Let Windows convert to the device's mix format (Bluetooth, HDMI, etc.).
    return sd.WasapiSettings(auto_convert=True) if OS == "windows" else None


class OutputStream:
    def __init__(self, dev: DeviceInfo, mixer: OutputMixer, latency: str | float):
        self.dev = dev
        self.mixer = mixer
        self.error: str | None = None
        self.xruns = 0
        self.channels = max(1, min(CH, dev.channels or CH))
        self._stream = sd.OutputStream(
            device=dev.pa_index, samplerate=SR, channels=self.channels, dtype="float32",
            latency=latency, extra_settings=_extra(), callback=self._cb,
        )

    def _cb(self, outdata, frames, t, status):
        if status.output_underflow:
            self.xruns += 1
        try:
            # perf_counter time this buffer reaches the device (excludes any
            # Bluetooth delay after that, which calibration measures).
            ahead = t.outputBufferDacTime - t.currentTime if t.currentTime else 0.0
            mix = self.mixer.render(frames, time.perf_counter() + max(ahead, 0.0))
            if self.channels == 1:
                outdata[:, 0] = mix.mean(axis=1)
            else:
                outdata[:] = mix
        except Exception as e:  # pragma: no cover - defensive
            outdata.fill(0)
            self.error = repr(e)

    @property
    def latency_ms(self) -> float:
        return round(self._stream.latency * 1000, 1)

    def start(self):
        self._stream.start()

    def close(self):
        _close(self._stream)


class InputStream:
    def __init__(self, dev: DeviceInfo, node: SourceNode, latency: str | float):
        self.dev = dev
        self.node = node
        self.error: str | None = None
        self.xruns = 0
        self._stream = sd.InputStream(
            device=dev.pa_index, samplerate=SR, channels=max(1, min(CH, dev.channels or CH)),
            dtype="float32", latency=latency, extra_settings=_extra(), callback=self._cb,
        )

    def _cb(self, indata, frames, _time, status):
        if status.input_overflow:
            self.xruns += 1
        try:
            self.node.push(_to_stereo(indata))
        except Exception as e:  # pragma: no cover
            self.error = repr(e)

    def start(self):
        self._stream.start()

    def close(self):
        _close(self._stream)


class LoopbackStream:
    """Windows system-audio capture through PyAudioWPatch (sounddevice can't do
    WASAPI loopback). Note: WASAPI sends no packets while nothing is playing;
    the output side treats that as silence."""

    def __init__(self, dev: DeviceInfo, node: SourceNode, latency: str | float):
        import pyaudiowpatch as pyaudio

        self.dev = dev
        self.node = node
        self.error: str | None = None
        self.xruns = 0
        self._pa = pyaudio.PyAudio()
        try:
            info = self._pa.get_default_wasapi_loopback()
            self.channels = int(info["maxInputChannels"])
            rate = int(info["defaultSampleRate"])
            self._rs = StreamResampler(SR / rate, CH) if rate != SR else None
            self._stream = self._pa.open(
                format=pyaudio.paFloat32, channels=self.channels, rate=rate, input=True,
                input_device_index=info["index"], frames_per_buffer=int(rate * 0.01),
                stream_callback=self._cb, start=False,
            )
            self._continue = pyaudio.paContinue
        except Exception:
            self._pa.terminate()
            raise

    def _cb(self, in_data, frame_count, _time, status):
        try:
            block = np.frombuffer(in_data, dtype=np.float32).reshape(-1, self.channels)
            block = _to_stereo(block)
            if self._rs:
                block = self._rs.process(block)
            self.node.push(block)
        except Exception as e:  # pragma: no cover
            self.error = repr(e)
        return None, self._continue

    def start(self):
        self._stream.start_stream()

    def close(self):
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            log.debug("loopback close failed", exc_info=True)
        finally:
            self._pa.terminate()


def _close(stream) -> None:
    try:
        stream.abort()
    except Exception:
        pass
    try:
        stream.close()
    except Exception:
        log.debug("stream close failed", exc_info=True)


def open_source(dev: DeviceInfo, node: SourceNode, latency):
    if dev.kind == "loopback" and dev.extra.get("backend") == "pyaudiowpatch":
        return LoopbackStream(dev, node, latency)
    return InputStream(dev, node, latency)


def open_output(dev: DeviceInfo, mixer: OutputMixer, latency, bt_core=None):
    if dev.kind == "phone":  # the AudioPatch app on a phone, over Bluetooth
        from backend.bt.streams import BtOutputStream
        return BtOutputStream(dev, mixer, bt_core)
    return OutputStream(dev, mixer, latency)
