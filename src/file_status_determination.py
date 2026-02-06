# file_status_determination.py
import numpy as np

# --- Classifier configuration (tunable) ---
ENERGY_RATIO_THRESHOLD: float = 1e-3        # 0.001% energy above cutoff => frame has HF content
MIN_ACTIVE_FRACTION: float   = 0.05         # >=5% frames with HF content => Original
RATIO_DROP_THRESHOLD: float  = 1e-5         # drop near-silent/invalid frames

# Confidence shaping for "Likely ORIGINAL"
ORIGINAL_CONFIDENCE_GAMMA: float = 1.0      # >1 = confidence rises slower near threshold, faster only when evidence is strong

MAX_HF_ACTIVE_FRACTION_FOR_CUTOFF = 0.02    # ≤2% frames with energy above cutoff → consistent with that cutoff
MIN_PREV_CUTOFF_ACTIVE_FRACTION = 0.2       # previous cutoff should be clearly active (>=20% frames) to confirm the elbow

LOSSY_CUTOFF_PROFILES = {                   # key stands for cutoff frequency in HZ; value stands for expected upscaled kbps rate
    13000: 96,
    16000: 128,
    19000: 192,
    20000: 256,
    20500: 320,
}

PROBE_CUTOFFS_HZ = sorted(LOSSY_CUTOFF_PROFILES.keys())

def debug_energy_ratios(ratios):
    """
    Compute summary statistics over per-frame high-frequency (HF) energy ratios.

    Definitions
    ----------
    For each audio frame we compute:

        ratio = (energy_above_cutoff_hz) / (total_frame_energy)

    where:
      - energy_above_cutoff_hz: the sum of spectral magnitudes in FFT bins
        strictly above the chosen cutoff frequency.
      - total_frame_energy: the sum of spectral magnitudes (or power) across all bins.

    Therefore:
      - ratio is dimensionless and lies in [0, 1] for valid frames.
      - ratio ≈ 0 means "no meaningful content above cutoff for that frame".
      - ratio closer to 1 means "most of the frame's spectral energy is above cutoff"
        (uncommon for music; usually indicates noise/artifacts or very HF-heavy content).

    Returned dictionary keys
    ------------------------
    hf_ratio_min:
        Minimum ratio across all frames.

    hf_ratio_p90 / hf_ratio_p95 / hf_ratio_p99:
        The 90th / 95th / 99th percentile of ratios. These describe how "spiky" HF content is.
        Example: hf_ratio_p99 is the value such that only 1% of frames exceed it.

    hf_ratio_max:
        Maximum ratio across all frames (useful to spot a few noisy frames).

    hf_ratio_mean:
        Arithmetic mean of ratios across all frames (overall HF presence proxy).

    Notes
    -----
    - This function assumes `ratios` already correspond to a specific cutoff frequency.
    - If you drop silent frames elsewhere, keep that consistent before calling this.
    """
    x = np.asarray(ratios, dtype=float)
    p0, p90, p95, p99, p100 = np.percentile(x, [0, 90, 95, 99, 100])
    return {
        "hf_ratio_min": float(p0),
        "hf_ratio_p90": float(p90),
        "hf_ratio_p95": float(p95),
        "hf_ratio_p99": float(p99),
        "hf_ratio_max": float(p100),
        "hf_ratio_mean": float(np.mean(x)),
    }

def _active_fraction_from_cache(frame_ffts, cutoff_hz, energy_ratio_threshold, ratio_drop_threshold):
    """Compute active_fraction at a given cutoff using cached FFTs (no re-FFT)."""
    if not frame_ffts:
        return 0.0
    active = 0
    total = 0
    for f in frame_ffts:
        # skip silent or invalid frames
        if getattr(f, "total_energy", 0.0) <= 0.0:
            continue
        if getattr(f, "spectrum_abs", None) is None or getattr(f, "freqs_hz", None) is None:
            continue
        if f.spectrum_abs.size == 0 or f.freqs_hz.size == 0:
            continue

        # first bin strictly above cutoff
        idx = np.searchsorted(f.freqs_hz, cutoff_hz, side="right")
        if idx >= f.spectrum_abs.size:
            ratio = 0.0
        else:
            ratio = float(np.sum(f.spectrum_abs[idx:]) / f.total_energy)

        if ratio > float(ratio_drop_threshold):
            total += 1
            if ratio > float(energy_ratio_threshold):
                active += 1
    if total == 0:
        return 0.0
    return active / total

def _estimate_bitrate_from_cache(frame_ffts, effective_cutoff, energy_ratio_threshold, ratio_drop_threshold, probe_cutoffs_hz=None):
    """
    Probe multiple cutoffs (ascending). Return (label:str, confidence:float, per_cutoff_fractions:dict)
    Select the first cutoff (ascending) that becomes quiet while the previous cutoff is loud.
    """
    if probe_cutoffs_hz is None:
        probe_cutoffs_hz = PROBE_CUTOFFS_HZ

    # Only evaluate physically valid cutoffs for this file
    probe_list = [c for c in probe_cutoffs_hz if c <= effective_cutoff]
    if not probe_list:
        return None, None, {}

    per_cutoff_fractions = {}
    # 1) Compute fractions for ALL candidate cutoffs (NO early break)
    for c in probe_list:
        frac = _active_fraction_from_cache(frame_ffts, c, energy_ratio_threshold, ratio_drop_threshold)
        per_cutoff_fractions[c] = frac

    # 2) Select the FIRST cutoff where activity becomes "quiet" (ascending),
    #    and the previous cutoff (if any) was "loud".
    selected_cutoff = None
    selected_frac = None

    for idx, c in enumerate(probe_list):  # ascending
        frac = per_cutoff_fractions[c]
        if frac <= MAX_HF_ACTIVE_FRACTION_FOR_CUTOFF:
            if idx == 0:
                # No previous cutoff to compare; accept if quiet at the very first cutoff
                selected_cutoff = c
                selected_frac = frac
                break
            prev_c = probe_list[idx - 1]
            prev_frac = per_cutoff_fractions[prev_c]
            if prev_frac >= MIN_PREV_CUTOFF_ACTIVE_FRACTION:
                selected_cutoff = c
                selected_frac = frac
                break

    if selected_cutoff is None:
        # fallback: if any quiet cutoffs exist, take the **lowest** quiet cutoff (safest upper bound)
        quiet_cutoffs = [c for c in probe_list if per_cutoff_fractions[c] <= MAX_HF_ACTIVE_FRACTION_FOR_CUTOFF]
        if quiet_cutoffs:
            selected_cutoff = quiet_cutoffs[0]
            selected_frac = per_cutoff_fractions[selected_cutoff]
        else:
            return None, None, per_cutoff_fractions

    kbps = LOSSY_CUTOFF_PROFILES.get(selected_cutoff)
    if kbps is None:
        return None, None, per_cutoff_fractions

    label = f"Likely UPSCALED from <={kbps} kbps"
    confidence = float(np.clip(1.0 - (selected_frac or 0.0), 0.0, 1.0))
    return label, confidence, per_cutoff_fractions

def determine_file_status(ratios, effective_cutoff, frame_ffts=None, probe_cutoffs_hz=None):
    """
    Decide ORIGINAL vs UPSCALED using per-frame energy-above-cutoff ratios.

    Expected:
      ratios: array-like of float in [0..1], each = fraction of frame energy above 'effective_cutoff'
      effective_cutoff: float (Hz) — the probe frequency for "energy above cutoff"
      frame_ffts: optional list of cached per-frame FFT artifacts (for later bitrate estimation)
      probe_cutoffs_hz: optional list of cutoffs to consider during bitrate estimation
                        (not used unless you integrate the estimation branch)
    Returns:
      (status: str, confidence: float in [0,1])
    """
    frame_energy_above_cutoff_ratios = np.asarray(ratios, dtype=float)
    if frame_energy_above_cutoff_ratios.size == 0:
        return "No audio data.", 0.0

    # Drop frames that are effectively silence / numerical dust
    frame_energy_above_cutoff_ratios = frame_energy_above_cutoff_ratios[frame_energy_above_cutoff_ratios > RATIO_DROP_THRESHOLD]
    if frame_energy_above_cutoff_ratios.size == 0:
        return "Likely UPSCALED (no significant frames)", 0.0

    # A frame is "active" if it has non-trivial energy above the cutoff
    active_fraction = float(np.mean(frame_energy_above_cutoff_ratios > float(ENERGY_RATIO_THRESHOLD)))

    if active_fraction >= float(MIN_ACTIVE_FRACTION):
        x = float(active_fraction)
        t = float(MIN_ACTIVE_FRACTION)

        if x >= 1.0:
            confidence = 1.0
        else:
            z = (x - t) / (1.0 - t)  # x in [t..1] -> z in [0..1]
            z = max(0.0, min(1.0, z))
            confidence = float(z ** float(ORIGINAL_CONFIDENCE_GAMMA))
        return "Likely ORIGINAL", confidence, None

    # Otherwise: little to no HF energy above the cutoff → try bitrate estimation if cache is available
    if frame_ffts:
        label, conf2, per_cutoff_fractions = _estimate_bitrate_from_cache(frame_ffts, effective_cutoff, ENERGY_RATIO_THRESHOLD, RATIO_DROP_THRESHOLD, probe_cutoffs_hz)
        if label is not None:
            return label, conf2, per_cutoff_fractions
         # estimation ran but didn't produce a label; still pass fractions upward
        return "Inconclusive (no cutoff match)", 0.0, per_cutoff_fractions

    # no cache available → cannot estimate bitrate profile
    return "Inconclusive (no FFT cache)", 0.0, {}