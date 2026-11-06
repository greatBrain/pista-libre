# 🎸 Pista Libre

**Pista Libre** is your personal, at-home rehearsal space.

It is a local web app built for musicians, vocalists, and students who want to practice along with their favorite songs as if playing with a full band. Simply upload any track from your computer, and the AI will automatically split it into **4 separate stems** (**Vocals, Drums, Bass, and Other**). From there, step into an interactive practice room where you can mute your own instrument to play along, solo a specific track to catch every detail, or export the separated stems directly to your machine.

---

## ✨ Features

- **Split Any Song:** Upload an audio file (`.mp3`, `.wav`, `.flac`, `.m4a`, `.ogg`, `.aac`) and get 4 clean stems in just a few minutes.
- **Practice Your Way:** Mute your instrument's track to sit in with the band, or solo a specific bassline, vocal run, or drum groove to study it up close.
- **Real-Time Mixing:** Fine-tune the volume of each individual stem with seamless playback sync right in your browser.
- **Export Stems:** Save any separated track directly to your computer as an MP3.
- **Instant Cache:** Re-uploading a song you've processed before? The app recognizes it instantly, skipping the wait.

---

## 🚀 Local Setup & Installation

### 1. Prerequisites
- **Python 3.10 or higher** installed on your system.
- **FFmpeg** installed and accessible via your system PATH (required by Demucs for audio processing).

### 2. Clone & Install Dependencies

Open a terminal in the project directory and run:

```powershell
# 1. Create a virtual environment
python -m venv .venv

# 2. Activate the virtual environment (Windows PowerShell)
.\.venv\Scripts\Activate.ps1
# (Linux / macOS: source .venv/bin/activate)

# 3. Install required packages
pip install -r requirements.txt
