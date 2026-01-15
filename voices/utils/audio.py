
# import subprocess
# from pathlib import Path

# # def to_mp3(input_path: str) -> str:
# #     """
# #     Convert any browser-recorded webm/ogg/m4a → mp3 with ffmpeg.
# #     Returns output path.
# #     """
# #     inp = Path(input_path)
# #     outp = inp.with_suffix(".mp3")
# #     cmd = ["ffmpeg", "-y", "-i", str(inp), "-ar", "44100", "-ac", "1", "-b:a", "192k", str(outp)]
# #     subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
# #     return str(outp)

# import subprocess, os, tempfile

def to_mp3(src_path: str) -> str:
    dst_path = os.path.splitext(src_path)[0] + ".mp3"
    cmd = ["ffmpeg", "-y", "-i", src_path, "-ac", "1", "-ar", "16000", dst_path]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst_path
def to_wav(src_path: str) -> str:
    dst_path = os.path.splitext(src_path)[0] + ".wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", src_path, "-ac", "1", "-ar", "44100", "-sample_fmt", "s16", dst_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return dst_path
import subprocess, os

# def to_wav_16k_mono(src_path: str) -> str:
#     """Convert any audio to 16kHz mono WAV (PCM) using ffmpeg; returns dest path. Raises with full stderr if it fails."""
#     dst_path = os.path.splitext(src_path)[0] + ".wav"
#     cmd = ["ffmpeg", "-y", "-i", src_path, "-ac", "1", "-ar", "16000", "-vn", "-f", "wav", dst_path]
#     # capture stderr for diagnostics
#     proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
#     if proc.returncode != 0:
#         raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()}")
#     return dst_path

def to_wav_16k_mono(src_path: str) -> str:
    """Convert any audio to 16kHz mono WAV (PCM) using ffmpeg; returns dest path or raises with stderr."""
    dst_path = os.path.splitext(src_path)[0] + ".wav"
    cmd = ["ffmpeg", "-y", "-i", src_path, "-ac", "1", "-ar", "16000", "-vn", "-f", "wav", dst_path]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()}")
    return dst_path