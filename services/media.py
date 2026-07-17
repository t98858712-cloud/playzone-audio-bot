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
