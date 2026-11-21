"""Audio features for engine-sound checks.

MVP: a log-mel spectrum summary computed with numpy only (mean + std per mel
band). It is small enough to compute on the phone too.
Later: swap `embed()` for a pretrained audio embedding model (e.g. YAMNet / PANNs
style) without changing the anomaly service.
"""
from __future__ import annotations

import io
import wave

import numpy as np

SAMPLE_RATE = 16_000
N_FFT = 1024
HOP = 512
N_MELS = 40


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Read 16-bit PCM WAV bytes -> mono float32 in [-1, 1] and the sample rate."""
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2:
            raise ValueError("Only 16-bit PCM WAV is supported in the MVP")
        frames = w.readframes(w.getnframes())
        rate, channels = w.getframerate(), w.getnchannels()
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def resample_linear(audio: np.ndarray, rate: int, target: int = SAMPLE_RATE) -> np.ndarray:
    if rate == target or len(audio) == 0:
        return audio
    n = int(round(len(audio) * target / rate))
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


def _mel_filterbank(sr: int = SAMPLE_RATE, n_fft: int = N_FFT, n_mels: int = N_MELS) -> np.ndarray:
    def hz_to_mel(f):
        return 2595 * np.log10(1 + f / 700)

    def mel_to_hz(m):
        return 700 * (10 ** (m / 2595) - 1)

    mels = np.linspace(hz_to_mel(20), hz_to_mel(sr / 2), n_mels + 2)
    bins = np.floor((n_fft + 1) * mel_to_hz(mels) / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for m in range(1, n_mels + 1):
        left, centre, right = bins[m - 1], bins[m], bins[m + 1]
        for k in range(left, centre):
            fb[m - 1, k] = (k - left) / max(centre - left, 1)
        for k in range(centre, right):
            fb[m - 1, k] = (right - k) / max(right - centre, 1)
    return fb


_FB = _mel_filterbank()


def log_mel(audio: np.ndarray) -> np.ndarray:
    """(frames, N_MELS) log-mel spectrogram."""
    if len(audio) < N_FFT:
        audio = np.pad(audio, (0, N_FFT - len(audio)))
    window = np.hanning(N_FFT).astype(np.float32)
    n_frames = 1 + (len(audio) - N_FFT) // HOP
    frames = np.stack([audio[i * HOP : i * HOP + N_FFT] * window for i in range(n_frames)])
    power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    return np.log(power @ _FB.T + 1e-10)


def embed(audio: np.ndarray, rate: int) -> np.ndarray:
    """Fixed-length vector for one recording: mean and std of each mel band."""
    spec = log_mel(resample_linear(audio, rate))
    return np.concatenate([spec.mean(axis=0), spec.std(axis=0)]).astype(np.float32)
