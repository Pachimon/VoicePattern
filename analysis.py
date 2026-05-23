import numpy as np
import parselmouth


def extract_pitch(
    audio_mono: np.ndarray,
    sample_rate: int,
    pitch_floor: float = 75.0,
    pitch_ceiling: float = 600.0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Extract pitch using Praat (via parselmouth).
    Returns (times_seconds, frequencies_hz). Unvoiced frames are NaN.
    """
    snd = parselmouth.Sound(audio_mono.astype(np.float64), sampling_frequency=sample_rate)
    pitch = snd.to_pitch(pitch_floor=pitch_floor, pitch_ceiling=pitch_ceiling)
    times = pitch.xs()
    freqs = pitch.selected_array["frequency"].copy()
    freqs[freqs == 0] = np.nan
    return times, freqs


def hz_to_semitones_relative(freqs: np.ndarray) -> np.ndarray:
    """
    Convert Hz to semitones relative to the voiced-frame mean.
    Makes pitch contours comparable across different voice registers.
    """
    valid = ~np.isnan(freqs) & (freqs > 0)
    if not np.any(valid):
        return np.full_like(freqs, np.nan)
    mean_hz = np.exp(np.mean(np.log(freqs[valid])))  # geometric mean
    result = np.full_like(freqs, np.nan)
    result[valid] = 12.0 * np.log2(freqs[valid] / mean_hz)
    return result


def compare_pitch(
    ref_times: np.ndarray,
    ref_freqs: np.ndarray,
    rec_times: np.ndarray,
    rec_freqs: np.ndarray,
    rec_offset: float = 0.0,
) -> dict:
    """
    Compare pitch contours accounting for temporal alignment.

    rec_offset shifts the recording start in seconds relative to the reference.
    Only frames that overlap in time are compared, so dragging the recording
    off-alignment lowers the score naturally.
    Score is semitone-normalised so male/female voice differences don't matter.
    """
    if len(ref_times) == 0 or len(rec_times) == 0:
        return {"score": 0.0, "rmse": 99.0, "correlation": 0.0}

    # Shift recording so its start sits at rec_offset
    rec_t = rec_times - rec_times[0] + rec_offset

    # Temporal overlap
    t0 = max(float(ref_times[0]), float(rec_t[0]))
    t1 = min(float(ref_times[-1]), float(rec_t[-1]))
    if t1 <= t0:
        return {"score": 0.0, "rmse": 99.0, "correlation": 0.0}

    # Voiced frames inside the overlap window
    ref_mask = (ref_times >= t0) & (ref_times <= t1) & ~np.isnan(ref_freqs) & (ref_freqs > 0)
    rec_mask = (rec_t >= t0) & (rec_t <= t1) & ~np.isnan(rec_freqs) & (rec_freqs > 0)

    ref_hz = ref_freqs[ref_mask]
    rec_hz = rec_freqs[rec_mask]

    if len(ref_hz) < 3 or len(rec_hz) < 3:
        return {"score": 0.0, "rmse": 99.0, "correlation": 0.0}

    # Normalize each to semitones relative to its own geometric mean
    ref_st = 12.0 * np.log2(ref_hz / np.exp(np.mean(np.log(ref_hz))))
    rec_st = 12.0 * np.log2(rec_hz / np.exp(np.mean(np.log(rec_hz))))

    # DTW alignment within overlap
    try:
        from dtw import dtw  # type: ignore
        alignment = dtw(ref_st, rec_st, keep_internals=True)
        r = ref_st[alignment.index1]
        s = rec_st[alignment.index2]
    except Exception:
        n = min(len(ref_st), len(rec_st))
        xs = np.linspace(0, 1, n)
        r = np.interp(xs, np.linspace(0, 1, len(ref_st)), ref_st)
        s = np.interp(xs, np.linspace(0, 1, len(rec_st)), rec_st)

    rmse = float(np.sqrt(np.mean((r - s) ** 2)))

    r_std = float(np.std(r))
    s_std = float(np.std(s))
    if r_std > 0 and s_std > 0:
        corr = float(np.corrcoef(r, s)[0, 1])
        corr = 0.0 if np.isnan(corr) else corr
    else:
        corr = 0.0

    # --- Scoring ---
    # corr_factor: Pearson correlation clipped to [0, 1].
    #   A flat/monotone/yelling recording is near-zero or negative correlation
    #   with any real pitch contour, so it scores near 0 here.
    #   An opposite accent pattern (e.g., LH vs HL) goes negative → 0.
    corr_factor = max(0.0, corr)

    # rmse_factor: RMSE normalized by the reference's own pitch range (std).
    #   Using a fixed 12-semitone ceiling grossly under-penalises errors in
    #   normal speech where the actual range is only 2–5 semitones.
    #   Two reference std-devs of error → 0; perfect → 1.
    ref_std = max(float(np.std(r)), 0.5)   # floor prevents div-by-zero on flat refs
    rmse_factor = max(0.0, 1.0 - rmse / (ref_std * 2.0))

    score = 100.0 * corr_factor * rmse_factor
    return {"score": round(score, 1), "rmse": round(rmse, 2), "correlation": round(corr, 3)}
