import subprocess
from pathlib import Path
import logging

logger = logging.getLogger("PlayZoneEnterpriseBot")

def convert_to_mp3_local(input_file: Path, output_file: Path, local_thumb: Path = None) -> bool:
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file)][span_167](start_span)[span_167](end_span)
        if local_thumb and local_thumb.exists():[span_168](start_span)[span_168](end_span)
            cmd.extend(["-i", str(local_thumb), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-id3v2_version", "3", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])[span_169](start_span)[span_169](end_span)
        else:
            cmd.extend(["-vn"])[span_170](start_span)[span_170](end_span)
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", "-ac", "2", "-threads", "0", str(output_file)])[span_171](start_span)[span_171](end_span)
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=180)[span_172](start_span)[span_172](end_span)
        return output_file.exists() and output_file.stat().st_size > 0[span_173](start_span)[span_173](end_span)
    except Exception as e:
        logger.error(f"فشل التحويل المحلي لـ MP3: {e}")[span_174](start_span)[span_174](end_span)
        return False[span_175](start_span)[span_175](end_span)

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
