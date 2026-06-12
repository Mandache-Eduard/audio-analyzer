# audio_frame_analysis.py
import numpy as np
from dataclasses import dataclass
from functools import lru_cache

from scipy.fft import rfft, rfftfreq
from scipy.signal.windows import hann

CUTOFF_HZ: float = 20_500.0
NYQUIST_SAFETY_BAND_HZ: float = 100.0


@dataclass
class FrameFFT:
    freqs_hz: np.ndarray
    spectrum_abs: np.ndarray
    total_energy: float


def divide_into_frames(data, frame_size=32768, step=16384):
    frames = []
    for start in range(0, len(data) - frame_size + 1, step):
        frames.append(data[start:start + frame_size])
    return frames


def calculate_effective_cutoff(samplerate):
    nyquist_frequency = samplerate / 2.0
    effective_cutoff = min(CUTOFF_HZ, max(0.0, nyquist_frequency - NYQUIST_SAFETY_BAND_HZ))
    return effective_cutoff


@lru_cache(maxsize=64)
def _hann_window(n: int, dtype_str: str) -> np.ndarray:
    # np.hanning(n) == symmetric Hann -> scipy hann(n, sym=True)
    return hann(n, sym=True).astype(np.dtype(dtype_str), copy=False)


@lru_cache(maxsize=256)
def _rfftfreq_cached(n: int, samplerate: float) -> np.ndarray:
    return rfftfreq(n, d=1.0 / samplerate)


@lru_cache(maxsize=512)
def _cutoff_bin(n: int, samplerate: float, cutoff_hz: float) -> int:
    # rfft bins at k * (samplerate / n), k=0..n//2
    df = samplerate / n
    k = int(np.floor(cutoff_hz / df))
    return max(0, min(k, n // 2))


def analyze_frame(single_frame, samplerate, effective_cutoff, fft_cache_list=None):
    if single_frame.ndim > 1:
        single_frame = single_frame[:, 0]

    # If you want to keep float64, remove the astype; float32 is usually faster.
    x = np.asarray(single_frame, dtype=np.float32)

    if np.max(np.abs(x)) < 1e-4:
        if fft_cache_list is not None:
            fft_cache_list.append(FrameFFT(np.array([]), np.array([]), 0.0))
        return 0.0

    n = x.shape[0]

    # Cached window (no per-frame allocation)
    w = _hann_window(n, x.dtype.str)

    # Reuse a buffer for the windowed samples (avoid allocating windowed each call)
    windowed = np.empty(n, dtype=x.dtype)
    np.multiply(x, w, out=windowed)

    # FFT + magnitude
    spectrum = np.abs(rfft(windowed))

    total_energy = float(spectrum.sum())
    if total_energy <= 0.0 or not np.isfinite(total_energy):
        if fft_cache_list is not None:
            # only compute freqs if you really store them
            freqs = _rfftfreq_cached(n, float(samplerate))
            fft_cache_list.append(FrameFFT(freqs, spectrum, 0.0))
        return 0.0

    # High-band sum without boolean mask: use cutoff bin
    k = _cutoff_bin(n, float(samplerate), float(effective_cutoff))
    high_band_energy = float(spectrum[k + 1 :].sum())
    ratio = high_band_energy / total_energy

    if fft_cache_list is not None:
        freqs = _rfftfreq_cached(n, float(samplerate))
        fft_cache_list.append(FrameFFT(freqs_hz=freqs, spectrum_abs=spectrum, total_energy=total_energy))

    if __debug__:
        assert np.isfinite(ratio), "Non-finite ratio produced in analyze_frame()"

    return ratio
