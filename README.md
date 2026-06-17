<!-- PROJECT LOGO -->

<br />
<div align="center">
  <a href="https://github.com/Mandache-Eduard/flac-authenticator">
    <img src="images/audio\\\_analyzer\\\_project\\\_picture.jpg" alt="Logo" width="450" height="450">
  </a>

<h3 align="center">Audio Analyzer</h3>

---

<p align="center">
  <a href="https://github.com/Mandache-Eduard/flac-authenticator/blob/main/docs/OVERVIEW.md"><strong>Explore the docs</strong></a>
  &middot;
  <a href="https://github.com/github_username/repo_name"><strong>View Demo (To be added)</strong></a>
  &middot;
  <a href="https://github.com/Mandache-Eduard/flac-authenticator/issues/new?labels=bug&template=bug_report.md"><strong>Report Bug</strong></a>
  &middot;
  <a href="https://github.com/Mandache-Eduard/flac-authenticator/issues/new?labels=enhancement&template=feature_or_request.md"><strong>Request Feature</strong></a>
</p>
</div>

<!-- TABLE OF CONTENTS -->

<details>
  <summary>Table of Contents</summary>
  <ol>
    <li><a href="#about-the-project">About The Project</a></li>
    <li><a href="#disclaimer">Disclaimer</a></li>
    <li><a href="#requirements">Requirements</a></li>
    <li><a href="#installation">Installation</a></li>
    <li><a href="#usage">Usage</a></li>
    <li><a href="#roadmap">Roadmap</a></li>
    <ul>
        <li><a href="#future-features">Future features</a></li>
        <li><a href="#features-in-development">Features in developments</a></li>
        <li><a href="#completed-features">Completed features</a></li>
      </ul>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#contact">Contact</a></li>
    <li><a href="#acknowledgments">Acknowledgments</a></li>
  </ol>
</details>

<!-- ABOUT THE PROJECT -->

## About The Project

Audio Analyzer is a Python-based tool for working with local audio libraries. It can analyse audio files to estimate whether tracks are genuinely lossless or upscaled, group tracks into album releases, enrich files with metadata through MusicBrainz and AcoustID-based matching, optionally fetch and embed lyrics, and run local machine learning audio processing such as stem separation and lyrics transcription.

It's my first big project written in [![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)](https://www.python.org/) and serves as a hands-on way to explore concepts related to audio analysis, metadata workflows, and data-oriented tooling. Built as a personal hobby project, it is intended to remain free and open-source, and to provide a simple, transparent alternative to existing tools that are either paid or limited in functionality.

---

<!-- DISCLAIMER -->

## Disclaimer

This project is currently in **an early, experimental stage** and should be considered a prototype rather than a definitive all-in-one audio tool.

For lossless verification, determining whether a FLAC file is truly lossless is inherently complex and depends on multiple factors, such as spectral analysis, encoding characteristics, frequency cutoffs, metadata consistency, checksums (e.g. MD5), and knowledge of the original source and production chain. This tool, in its current stage, only covers a subset of these aspects and **does not guarantee** correct results in all cases; in particular, it currently focuses more on analyzing audio quality characteristics than on conclusively establishing the true origin of a file. As a result, it may produce **false positives (upscaled files identified as genuine)** or **false negatives (genuine files flagged as upscaled)**. Users are **strongly encouraged** to manually review and double-check files reported with low confidence, using additional tools and their own judgment.

For metadata tagging and folder grouping, results are limited by the public datasets and identifiers currently available to the workflow. Some releases may not appear or may be matched incompletely, especially very recent releases, unofficial releases, bootlegs, and fan-made compilations.

For audio splitting and lyrics transcription, processing happens locally on the user's machine and is therefore dependent on the available hardware and system performance. On some systems, these workflows can take a significant amount of time to complete, but the tradeoff is that the processing remains entirely local and can be used offline.

---

<!-- REQUIREMENTS -->

## Requirements

Python versions:

* [![Python 3.14t](https://img.shields.io/badge/Python-3.14t-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/release/python-3140/) - primary runtime used for the main CLI workflows, including audio analysis and metadata grouping/tagging
* [![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/release/python-3110/) - runtime used for Demucs-based audio splitting and related local ML workflows

External utilities:

* [![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?style=for-the-badge&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/) - required for audio decoding, conversion support, and spectrogram generation. Place it inside the project's `tools` folder (<a href="https://github.com/oop7/ffmpeg-install-guide"><strong>installation guide here»</strong></a>)
* [![Chromaprint / fpcalc](https://img.shields.io/badge/Chromaprint%20%2F%20fpcalc-4B5563?style=for-the-badge)](https://acoustid.org/chromaprint) - required for AcoustID fingerprint generation during metadata identification and grouping. Place it inside the project's `tools` folder (<a href="https://acoustid.org/chromaprint"><strong>installation guide here»</strong></a>)


---

<!-- INSTALLATION -->

## Installation

Before running the project, install everything listed in the `Requirements` section first, including both Python runtimes, `FFmpeg`, and `fpcalc.exe`.

To run it locally:

1. Clone the repository

```sh
git clone https://github.com/Mandache-Eduard/flac-authenticator.git
```

2. Create your local `.env` file for metadata tagging and lyrics-related workflows

Use `.env.example` as the template and create a `.env` file in the project root with the same variables:

```env
ACOUSTID_API_KEY=
GENIUS_ACCESS_TOKEN=
MUSICBRAINZ_APP_NAME=music-sorter-prototype
MUSICBRAINZ_APP_VERSION=0.1.0
MUSICBRAINZ_CONTACT_EMAIL=
```

3. Fill in the `.env` values

`ACOUSTID_API_KEY`
Use the **Application API key** from [AcoustID My Applications](https://acoustid.org/my-applications) for this program.

Do not use the key from [AcoustID API Key](https://acoustid.org/api-key) for `ACOUSTID_API_KEY`. That page gives you your **user API key**, which is intended for account-level actions such as submissions, not normal lookup requests. Using the user key here can cause lookup failures such as `code=4 message=invalid API key`.

`GENIUS_ACCESS_TOKEN`
Use your Genius access token for lyrics lookup fallback. Create it through the [Genius API documentation](https://docs.genius.com/). If you do not plan to use Genius-backed lyrics fallback, you can leave this empty.

`MUSICBRAINZ_APP_NAME`
Set this to the application name you want to send to MusicBrainz.

`MUSICBRAINZ_APP_VERSION`
Set this to your local version string for the application.

`MUSICBRAINZ_CONTACT_EMAIL`
Set this to a valid contact email for your MusicBrainz client identification. MusicBrainz does not require an API key for normal lookups, but it does require meaningful client identification.

The metadata tagging workflow depends on the `.env` configuration above. The other local workflows still require the installed runtime dependencies from `Requirements`.

---

<!-- USAGE EXAMPLES -->

## Usage

The CLI currently exposes three main workflows: `analyse`, `group`, and `split`.

### General command format

```sh
py src/main.py <command> [options]
```

Use this to show the built-in help menu:

```sh
py src/main.py help
```

### Analyse audio quality

Use `analyse` to scan a single FLAC file or an entire folder and estimate whether the audio appears genuinely lossless or upscaled.

```sh
py src/main.py analyse "<file-or-folder-path>"
```

Examples:

```sh
py src/main.py analyse "X:\path\to\file.flac"
py src/main.py analyse "X:\path\to\folder"
```

### Group releases and write metadata

Use `group` to scan a folder, resolve identifiers, group tracks into releases, enrich metadata, and optionally write lyrics into the output files.

```sh
py src/main.py group [lyrics-mode] "<folder-path>"
```

Supported lyrics modes:

* `lyrics-none` - group tracks without inserting lyrics
* `lyrics-unsynced` - group tracks with plain or unsynced lyrics
* `lyrics-synced` - group tracks with synced lyrics when available

Examples:

```sh
py src/main.py group "X:\path\to\folder"
py src/main.py group lyrics-none "X:\path\to\folder"
py src/main.py group lyrics-unsynced "X:\path\to\folder"
py src/main.py group lyrics-synced "X:\path\to\folder"
```

### Split audio and transcribe lyrics

Use `split` to run local machine learning audio processing, including vocal separation, bass separation, drum separation, instrumental generation, and lyrics extraction workflows.

```sh
py src/main.py split [options] "<file-path>"
```

Common options:

* `--outputs <items>` - comma-separated list of outputs to generate, such as `vocals,bass,drums,instrumental`
* `--lyrics-mode <mode>` - lyrics output mode: `none`, `plain`, or `timestamped`
* `--device <mode>` - processing device: `auto`, `cpu`, or `cuda`
* `--language <code>` - transcription language, such as `en`, `ro`, or `auto`
* `--overwrite` - replace existing output files

Examples:

```sh
py src/main.py split --outputs vocals,bass,drums,lyrics --lyrics-mode plain --language en "X:\path\to\file.mp3"
py src/main.py split --outputs lyrics --lyrics-mode timestamped --language ro "X:\path\to\file.flac"
py src/main.py split --outputs vocals,instrumental "X:\path\to\file.flac"
```

Audio ML results are saved in the configured audio workflow output directory.

### Recommended local models

My current personal model recommendations for the split workflow are:

* Vocals - `MDX-Net: Kim Vocal 2`
* Instrumental - `MDX23C InstVoc HQ`
* Bass - `htdemucs_ft`
* Drums - `htdemucs_ft`
* Lyrics transcription - `ggml-large-v2`, `ggml-large-v3`, `ggml-large-v3-turbo`

### Path handling

Paths that contain spaces must be wrapped in quotes.

Valid:

```sh
py src/main.py analyse "X:\Music Folder\song.flac"
```

Invalid:

```sh
py src/main.py analyse X:\Music Folder\song.flac
```

---

<!-- ROADMAP -->

## Roadmap

### Future features

* \[ ] Expand the program to do AI-generated audio detection.
* \[ ] Implement metadata caching so already enriched audio files do not need to be looked up again unless the user explicitly requests a refresh
* \[ ] Implement different checks for audio files origin (audio artifacts, checksums, metadata etc.)

### Features in development

* \[ ] Improve the FLAC upscaling and lossless detection algorithm for more accurate verification results
* \[ ] Integrate the Discogs API for album and track lookup as a fallback when MusicBrainz does not return a result
* \[ ] Create a UI for ease of use
  * \[ ] Rewrite program for multithreading (1 thread for UI, 1 thread for file processing)
* \[ ] Provide both dark and light themes
* \[ ] Create a better installation experience

  * \[ ] Automatically install and configure FFmpeg, `fpcalc.exe`, and local models
  * \[ ] Automatically create virtual environments and install the matching libraries and scripts

### Completed features

* \[X] Implement FFmpeg spectrogram creation for low-confidence files
* \[X] Save verification results in a more detailed log file (.CSV)
* \[X] Add file recognition using:
  * \[X] metadata lookup based on MBID
  * \[X] AcoustID lookup and fingerprint generation
* \[X] Add grouping into albums based on the graph algorithm edge-weight workflow
* \[X] Add lyrics search and embedding with support for synced and unsynced lyrics
* \[X] Add local audio track separation into stems (vocals, instrumental, bass, and drums)
* \[X] Add local language-model-powered lyric transcription
* \[X] Add support for each feature, for:
  * \[X] folder structures
  * \[X] individual audio files
* \[X] Add multithreading support for:
  * \[X] spectrogram generation
  * \[X] metadata scanning and writing
  * \[X] file copying
  * \[X] audio fingerprint generation

---

<!-- CONTRIBUTING -->

## Contributing

Contributions and feedback are a big part of what makes open-source projects such a great place to learn and grow, and they are always appreciated.

If you notice a bug, have a suggestion, or think something could be improved, feel free to open an issue and describe it in detail. I value constructive criticism highly and will review reported issues whenever I have the time. I prefer to implement new features and fixes myself, as the primary goal is learning through hands-on development. That said, your insights and observations are very welcome and genuinely helpful.

If you’d like to support the project, giving it a star is always appreciated. Thank you for taking the time to check it out!

---

<!-- LICENSE -->

## License

Distributed under the GNU General Public License v3.0-only. See `LICENSE` or click the link below for more information.
<br>
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-0E7C3A?style=for-the-badge)](https://www.gnu.org/licenses/gpl-3.0)

---

<!-- CONTACT -->

## Contact

Mandache Eduard
<br>
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/eduard-mandache-89588035b/)
<br>
[![Outlook](https://img.shields.io/badge/Email-Outlook-0078D4?style=for-the-badge&logo=microsoft-outlook&logoColor=white)](mailto:mandache.eduard@outlook.com)
<br>
Project Link: [https://github.com/Mandache-Eduard/audio-analyzer](https://github.com/Mandache-Eduard/audio-analyzer)

---

<!-- ACKNOWLEDGMENTS -->

## Acknowledgments

* [![Best README Template](https://img.shields.io/badge/Best_README_Template-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/othneildrew/Best-README-Template)
* [![Choose a License](https://img.shields.io/badge/Choose_a_License-0E7C3A?style=for-the-badge)](https://choosealicense.com)
* [![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?style=for-the-badge&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/) - used for audio decoding, conversion, and spectrogram generation
* [![Chromaprint / fpcalc](https://img.shields.io/badge/Chromaprint%20%2F%20fpcalc-4B5563?style=for-the-badge)](https://acoustid.org/chromaprint) - used for local audio fingerprint generation
* [![MusicBrainz](https://img.shields.io/badge/MusicBrainz-BA478F?style=for-the-badge)](https://musicbrainz.org/) - metadata source used for release and recording lookup
* [![AcoustID](https://img.shields.io/badge/AcoustID-4B5563?style=for-the-badge)](https://acoustid.org/) - audio fingerprint lookup service used for identification fallback
* [![Mutagen](https://img.shields.io/badge/Mutagen-4A5568?style=for-the-badge)](https://mutagen.readthedocs.io/) - used for reading and writing audio metadata tags
* [![LRCLIB](https://img.shields.io/badge/LRCLIB-2563EB?style=for-the-badge)](https://lrclib.net/) - used for primary lyrics lookup
* [![lyricsgenius](https://img.shields.io/badge/lyricsgenius-EAB308?style=for-the-badge)](https://github.com/johnwmillr/LyricsGenius) - used for Genius-backed lyrics lookup fallback
* [![Demucs](https://img.shields.io/badge/Demucs-111827?style=for-the-badge)](https://github.com/facebookresearch/demucs) - source project for music source separation
* [![audio-separator](https://img.shields.io/badge/audio--separator-111827?style=for-the-badge)](https://github.com/nomadkaraoke/python-audio-separator) - used in the local stem separation workflow
* [![PyTorch](https://img.shields.io/badge/PyTorch-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org/) - runtime used by local machine learning workflows
* [![whisper.cpp](https://img.shields.io/badge/whisper.cpp-1F2937?style=for-the-badge)](https://github.com/ggml-org/whisper.cpp) - used for local lyrics transcription
* [![Hugging Face](https://img.shields.io/badge/Hugging_Face-FFD21E?style=for-the-badge)](https://huggingface.co/) - used for downloading local models
* [![tqdm](https://img.shields.io/badge/tqdm-0F172A?style=for-the-badge)](https://github.com/tqdm/tqdm) - used for progress reporting across workflows
* [![Mermaid](https://img.shields.io/badge/Mermaid-FF3670?style=for-the-badge)](https://mermaid.js.org/) - used for diagrams and workflow visualization

