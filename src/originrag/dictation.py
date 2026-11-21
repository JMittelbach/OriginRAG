"""Local Whisper dictation. Browser input is bounded, mono 16 kHz signed PCM."""

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
NAME = "ggml-large-v3-turbo-q5_0.bin"
MODEL = ROOT / "runtime" / "models" / "dictation" / NAME
MODEL_BYTES = 574041195
SHA256 = "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"
URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/98aa99a0a9db05ae2342309f5096248665f7cba3/" + NAME
MAX_BYTES = 90 * 16000 * 2
LANGUAGES = {"de", "en", "auto"}


def ready():
    return (MODEL.is_file() and MODEL.stat().st_size == MODEL_BYTES
            and importlib.util.find_spec("pywhispercpp") is not None)


def prepare():
    if MODEL.is_file() and MODEL.stat().st_size == MODEL_BYTES:
        with MODEL.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() == SHA256:
                return
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    print("Downloading multilingual dictation model (574 MB)…", flush=True)
    with tempfile.TemporaryDirectory(dir=MODEL.parent) as directory:
        tmp = Path(directory) / NAME
        digest = hashlib.sha256()
        with urlopen(URL, timeout=90) as response, tmp.open("wb") as out:
            while block := response.read(1024 * 1024):
                if out.tell() + len(block) > MODEL_BYTES:
                    raise ValueError("Unexpected dictation download size.")
                digest.update(block)
                out.write(block)
        if tmp.stat().st_size != MODEL_BYTES or digest.hexdigest() != SHA256:
            raise ValueError("Dictation download failed verification. Retry setup.")
        tmp.replace(MODEL)


def transcribe(raw, language):
    if language not in LANGUAGES or not 8000 <= len(raw) <= MAX_BYTES or len(raw) % 2:
        raise ValueError("Record between a quarter second and 90 seconds of audio.")
    if not ready():
        raise RuntimeError("Dictation is not ready. Run ./originrag setup-dictation on this host.")
    # A short-lived worker isolates native model state between users and releases RAM on exit.
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), language], input=raw,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=150, check=True)
    return result.stdout.decode("utf-8").strip()


def decode(raw, language):
    import numpy as np
    from pywhispercpp.model import Model

    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    frames = audio[:len(audio) // 320 * 320].reshape(-1, 320)
    if (np.mean(frames * frames, axis=1) > 0.000004).sum() < 5:
        return ""
    model = Model(str(MODEL), n_threads=min(4, os.cpu_count() or 1), print_realtime=False,
                  print_progress=False, print_timestamps=False, no_context=True, translate=False,
                  language=language, suppress_nst=True)
    return " ".join(s.text.strip() for s in model.transcribe(audio) if s.text.strip())


if __name__ == "__main__":
    if sys.argv[1:] == ["--prepare"]:
        prepare()
    elif len(sys.argv) == 2 and sys.argv[1] in LANGUAGES and ready():
        sys.stdout.buffer.write(decode(sys.stdin.buffer.read(MAX_BYTES), sys.argv[1]).encode("utf-8"))
    else:
        sys.exit(2)
