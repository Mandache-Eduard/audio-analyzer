## External Dependencies
### Imports
- `numpy` — numeric array operations and vectorized sanitization utilities.
- `soundfile` — FLAC decoding via libsndfile (`sf.read`).

## Module-level Constants and Variables
### Key runtime variables (created/used by the module’s functions)

- `data: np.ndarray`  
  Audio samples loaded from the FLAC file. Shape depends on the file:
  - mono: `(num_samples,)`
  - multichannel: `(num_samples, num_channels)` when returned by `sf.read` (implementation-dependent).  
  Normalized to `np.float32` and sanitized so it contains only finite values.

- `samplerate: int`  
  Sampling rate (Hz) returned by the decoder (e.g.: 44100, 48000, 96000). Used by downstream analysis to compute frequencies and frame sizes.

## Additional Information

### Data Normalization (`np.float32`)
Audio decoded via `soundfile` can arrive as different numeric types depending on how the file was encoded and decoded (commonly float64, sometimes integer types). This module converts the sample array to `np.float32` to standardize downstream processing. Using `float32` somewhat improves performance and reduces memory usage compared to `float64`, while maintaining sufficient precision for the analysis later in the pipeline.

### Non-finite Sample Sanitization (finite-only data)
To ensure numerical stability, the loader checks whether the sample array contains any non-finite values (`NaN`, `+Inf`, `-Inf`). These values can arise from corruption, decoding edge cases, or unusual source pipelines, and they can propagate through computations (sums/means become `NaN`, infinities dominate scaling), breaking FFT-based analysis. Any non-finite samples are replaced with `0.0` (silence) using `np.nan_to_num`, producing a deterministic, robust output array suitable for further processing.


## Module Workflow (call graph)
```mermaid
flowchart TD
    classDef ok fill:#20462d,stroke:#2e7d32;
    classDef err fill:#a1362a,stroke:#c62828;

    Start["load_flac(file_path)"]:::ok
    Read["sf.read(file_path) → (data, samplerate)"]:::ok
    EnsureArr["data = np.asarray(data)"]:::ok
    Cast["Cast/convert data → np.float32"]:::ok
    CheckFinite{"All samples finite?"}:::ok
    Sanitize["np.nan_to_num(... → 0.0)"]:::err
    ReturnOK["return (data, samplerate)"]:::ok
    Fail["print error; return (None, None)"]:::err

    Start --> Read --> EnsureArr --> Cast --> CheckFinite
    CheckFinite -->|Yes| ReturnOK
    CheckFinite -->|No| Sanitize --> ReturnOK
    Start -->|Exception| Fail
```

## Function Inventory
* `load_flac(file_path)`