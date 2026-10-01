import subprocess

with open('bad.ass', 'w', encoding='utf-8') as f:
    f.write('This is a completely broken ASS file\n')

cmd = [
    'ffmpeg', '-y',
    '-f', 'lavfi',
    '-i', 'color=c=black:s=64x64:d=1',
    '-filter_complex', "[0:v]subtitles='bad.ass'[v_sub]",
    '-map', '[v_sub]',
    '-c:v', 'libx264',
    'test_bad_ass.mp4'
]
res = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
print('Return code:', res.returncode)
for line in res.stderr.split('\n')[-10:]:
    print(line)
