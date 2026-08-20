# utils/subtitles.py
import re
from pathlib import Path

def generate_ass_subtitle_file(transcript_text: str, output_ass_path: str | Path) -> Path:
    """
    Parses timestamped transcript lines and generates an ASS subtitle file
    with dynamic kinetic styling for vertical (9:16) video overlays.
    """
    output_ass_path = Path(output_ass_path)
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    
    # ASS Header with clean bold styling designed for mobile/vertical screens
    ass_content = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: KineticViral,Inter,54,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,2,2,50,50,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Simple regex parser to extract [MM:SS - MM:SS] text blocks
    pattern = re.compile(r"\[(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})\]\s*(?:\(([^)]+)\))?:?\s*(.*)")
    
    lines_added = 0
    for line in transcript_text.strip().split("\n"):
        match = pattern.search(line)
        if match:
            start_str, end_str, speaker, text = match.groups()
            
            # Convert MM:SS to ASS timestamp format (H:MM:SS.cs)
            start_ass = convert_to_ass_time(start_str)
            end_ass = convert_to_ass_time(end_str)
            
            # Clean text and split into punchy chunks if too long
            clean_text = text.strip()
            if speaker:
                clean_text = f"\\N{{\\c&H38BDF8&}}({speaker}):{{\\c&HFFFFFF&}} {clean_text}"
                
            ass_content += f"Dialogue: 0,{start_ass},{end_ass},KineticViral,,0,0,0,,{clean_text}\n"
            lines_added += 1

    with open(output_ass_path, "w", encoding="utf-8-sig") as f:
        f.write(ass_content)
        
    return output_ass_path

def convert_to_ass_time(mm_ss: str) -> str:
    """Converts 'MM:SS' string to ASS time format '0:00:MM:SS.00'."""
    parts = mm_ss.split(":")
    m = int(parts[0])
    s = int(parts[1])
    h = m // 60
    m = m % 60
    return f"{h}:{m:02d}:{s:02d}.00"