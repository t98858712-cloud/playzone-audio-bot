import subprocess
from pathlib import Path
import logging

logger = logging.getLogger("PlayZoneEnterpriseBot")

def convert_to_mp3_local(input_file: Path, output_file: Path, local_thumb: Path = None) -> bool:
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file)]
        if local_thumb and local_thumb.exists():
            cmd.extend(["-i", str(local_thumb), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-id3v2_version", "3", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])
        else:
            cmd.extend(["-vn"])
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", "-ac", "2", "-threads", "0", str(output_file)])
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=180)
        return output_file.exists() and output_file.stat().st_size > 0
    except Exception as e:
        logger.error(f"فشل التحويل المحلي لـ MP3: {e}")
        return False

def normalize_audio_local(input_file: Path, output_file: Path) -> bool:
    """تطبيق الهندسة الصوتية والرفع المعياري الفاخر للمقطع R128"""
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", str(output_file)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120)
        return output_file.exists() and output_file.stat().st_size > 0
    except Exception as e:
        logger.error(f"فشل هندسة وموازنة الصوت لـ HQ: {e}")
        return False

def split_audio_vocals_local(input_file: Path, output_vocals: Path, output_instrumental: Path) -> bool:
    """عزل الصوت البشري عن الموسيقى عبر استخلاص قنوات الستيريو"""
    try:
        cmd_inst = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file),
            "-af", "pan=stereo|c0=c0-c1|c1=c1-c0", "-c:a", "libmp3lame", "-b:a", "320k", str(output_instrumental)
        ]
        subprocess.run(cmd_inst, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=90)
        
        cmd_voc = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file),
            "-af", "highpass=f=200, lowpass=f=3000, volume=1.4", "-c:a", "libmp3lame", "-b:a", "320k", str(output_vocals)
        ]
        subprocess.run(cmd_voc, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=90)
        
        return output_vocals.exists() and output_instrumental.exists()
    except Exception as e:
        logger.error(f"فشل معالج العزل الذكي للمقطع: {e}")
        return False
