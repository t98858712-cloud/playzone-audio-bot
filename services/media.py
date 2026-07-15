import subprocess
from pathlib import Path
import logging

logger = logging.getLogger("PlayZoneEnterpriseBot")

def convert_to_mp3_local(input_file: Path, output_file: Path, local_thumb: Path = None, pro_mode: bool = False) -> bool:
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_file)]
        if local_thumb and local_thumb.exists():
            cmd.extend(["-i", str(local_thumb), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-id3v2_version", "3", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])
        else:
            cmd.extend(["-vn"])
        
        # 🎛️ حقن فلاتر هندسة صوت الاستوديو باحترافية تامة عند تفعيل الوضع المتقدم
        if pro_mode:
            cmd.extend([
                "-af", 
                "bass=g=4:f=80,treble=g=2:f=10000,acompressor=threshold=-14dB:ratio=2.5:attack=100:release=500,loudnorm=I=-15:TP=-1.5:LRA=10"
            ])
            
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", "-ac", "2", "-threads", "0", str(output_file)])
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=180)
        return output_file.exists() and output_file.stat().st_size > 0
    except Exception as e:
        logger.error(f"فشل التحويل المحلي لـ MP3: {e}")
        return False
