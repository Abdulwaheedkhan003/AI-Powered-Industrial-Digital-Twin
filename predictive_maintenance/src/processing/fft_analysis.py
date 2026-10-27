"""Frequency analysis of one acceleration burst.

Limits to keep in mind:
  * Highest visible frequency (Nyquist) = sample_rate / 2. Anything faster
    folds back ("aliases") to a wrong, lower frequency.
  * Frequency resolution = sample_rate / number_of_samples. With 256 samples
    at 500 Hz that is about 2 Hz: two tones closer than that look like one.
  * The FFT assumes evenly spaced samples. sampling_info() checks this from
    the ESP32's own microsecond timestamps.
  * A peak says "there is energy at this frequency". It does not identify a
    bearing, gear or any other component.
"""
import numpy as np

MIN_FFT_SAMPLES = 16
MAX_IRREGULAR_FRACTION = 0.02   # more than 2 % badly timed samples -> warn
PEAK_TO_FLOOR_RATIO = 6.0       # a dominant peak must stand this far above the floor


def sampling_info(t_us):
    """Work out the real sample rate of a burst from its timestamps (µs)."""
    t = np.asarray(t_us, dtype=float)
    if len(t) < MIN_FFT_SAMPLES:
        return {"valid": False, "reason": f"only {len(t)} samples"}
    dt = np.diff(t) / 1e6
    if not np.isfinite(dt).all() or (dt <= 0).any():
        return {"valid": False, "reason": "timestamps are not strictly increasing"}
    median = float(np.median(dt))
    irregular = float(np.mean(np.abs(dt - median) > 0.10 * median))
    return {
        "valid": True,
        "sample_rate_hz": 1.0 / median,
        "n_samples": len(t),
        "duration_s": float(t[-1] - t[0]) / 1e6,
        "irregular_fraction": irregular,
        "regular": irregular <= MAX_IRREGULAR_FRACTION,
        "resolution_hz": (1.0 / median) / len(t),
        "nyquist_hz": 0.5 / median,
    }


def spectrum(samples, sample_rate_hz):
    """Single-sided amplitude spectrum. Returns (frequencies_hz, amplitude).

    The mean is removed first (this takes out gravity and any constant sensor
    offset), then a Hann window is applied to reduce leakage. Amplitudes are
    scaled so a pure sine of amplitude A shows a peak of about A, in the same
    unit as the input.
    """
    x = np.asarray(samples, dtype=float)
    if len(x) < MIN_FFT_SAMPLES:
        raise ValueError(f"Need at least {MIN_FFT_SAMPLES} samples, got {len(x)}")
    if not np.isfinite(x).all():
        raise ValueError("Signal contains missing or non-finite samples")
    centred = x - x.mean()
    frequencies = np.fft.rfftfreq(len(x), d=1.0 / sample_rate_hz)
    if is_flat(x):
        return frequencies, np.zeros(len(frequencies))
    window = np.hanning(len(x))
    amplitude = 2.0 * np.abs(np.fft.rfft(centred * window)) / window.sum()
    return frequencies, amplitude


def is_flat(samples):
    """True if the signal does not vary beyond floating-point rounding."""
    x = np.asarray(samples, dtype=float)
    return np.ptp(x) <= 1e-12 * max(1.0, float(np.abs(x).max()))


def dominant_frequency(frequencies, amplitude):
    """Frequency of the largest peak, or None if no peak clearly stands out."""
    amp = np.asarray(amplitude)[1:]          # ignore the 0 Hz bin
    if len(amp) == 0 or amp.max() <= 0:
        return None
    k = int(np.argmax(amp))
    floor = float(np.median(amp))
    if amp[k] < PEAK_TO_FLOOR_RATIO * floor:
        return None
    return float(np.asarray(frequencies)[k + 1])
