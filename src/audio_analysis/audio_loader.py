# audio_loader.py
import numpy as np
import soundfile as sf

def load_flac(file_path):
    try:
        data, samplerate = sf.read(file_path, always_2d=False)
        data = np.asarray(data)                    # Ensure numpy array is used

        if data.dtype.kind != "f":
            data = data.astype(np.float32)         # Convert to float32
        else:
            data = data.astype(np.float32, copy=False)
        if not np.all(np.isfinite(data)):          # If any non-finite samples exist, replace them with 0.0
            data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
        return data, samplerate

    except Exception as e:
        print(f"Error loading file: {e}")
        return None, None
