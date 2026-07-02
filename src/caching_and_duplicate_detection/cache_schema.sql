CREATE TABLE IF NOT EXISTS files(
  file_id INTEGER PRIMARY KEY,
  path TEXT NOT NULL,
  normalized_path TEXT NOT NULL UNIQUE,
  size_bytes INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  content_hash TEXT,
  quick_key TEXT NOT NULL,
  extension TEXT,
  duration_seconds REAL,
  codec TEXT,
  bitrate_bps INTEGER,
  sample_rate_hz INTEGER,
  channels INTEGER,
  bits_per_sample INTEGER,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fingerprints(
  file_id INTEGER PRIMARY KEY,
  chromaprint TEXT NOT NULL,
  duration_seconds REAL NOT NULL,
  acoustid_id TEXT,
  acoustid_score REAL,
  lookup_json TEXT,
  fpcalc_version TEXT,
  fingerprint_settings TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analysis_results(
  file_id INTEGER PRIMARY KEY,
  analyzer_version TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence REAL,
  samplerate_hz INTEGER,
  num_samples INTEGER,
  num_total_frames INTEGER,
  num_non_silent_frames INTEGER,
  effective_cutoff_hz REAL,
  per_cutoff_active_fraction TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS metadata_resolution(
  file_id INTEGER PRIMARY KEY,
  resolver_version TEXT NOT NULL,
  source TEXT,
  status TEXT,
  recording_mbid TEXT,
  acoustid_id TEXT,
  result_json TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_files_quick_key
ON files(quick_key);

CREATE INDEX IF NOT EXISTS idx_files_content_hash
ON files(content_hash);

CREATE INDEX IF NOT EXISTS idx_fingerprints_acoustid_id
ON fingerprints(acoustid_id);

CREATE INDEX IF NOT EXISTS idx_metadata_recording_mbid
ON metadata_resolution(recording_mbid);

CREATE INDEX IF NOT EXISTS idx_metadata_acoustid_id
ON metadata_resolution(acoustid_id);

PRAGMA user_version = 1;
