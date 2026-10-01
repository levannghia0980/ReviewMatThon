import subprocess
import os

# Create dummy ass file
ass_content = """[Script Info]
ScriptType: v4.00+
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,1,2,10,10,10,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Hello
"""
with open('test.ass', 'w', encoding='utf-8') as f:
    f.write(ass_content)

ass_rel = os.path.relpath('test.ass').replace('\\\\', '/')
ass_escaped = ass_rel.replace("'", "\\\\'")

filter_complex = f"[0:v]subtitles='{ass_escaped}':force_style='Encoding=UTF-8'[v_sub];[v_sub]scale=w='trunc(iw/2)*2':h='trunc(ih/2)*2'[v_out]"

# Create dummy video input using lavfi instead of file
cmd = [
    'ffmpeg', '-y',
    '-f', 'lavfi',
    '-i', 'color=c=black:s=1280x720:d=1',
    '-filter_complex', filter_complex,
    '-map', '[v_out]',
    '-c:v', 'libx264',
    '-preset', 'ultrafast',
    '-crf', '22',
    '-pix_fmt', 'yuv420p',
    'test_fallback.mp4'
]
print('Running:', ' '.join(cmd))
res = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
if res.returncode != 0:
    print('FAILED!')
    print(res.stderr)
else:
    print('SUCCESS')
