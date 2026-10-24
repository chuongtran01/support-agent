#!/bin/sh
# Join the clips of the six-step demo (recorded by `DEMO_VIDEO=1 make frontend-demo`) into one mp4.
# Each clip holds its last frame for two seconds so the answer can be read. Needs ffmpeg.
set -eu
cd "$(dirname "$0")/.."
out=${1:-docs/media/demo.mp4}
inputs=""
filters=""
for n in 1 2 3 4 5 6; do
  clip=$(ls -d frontend/test-results/demo-"$n"-*/ | head -1)video.webm
  inputs="$inputs -i $clip"
  filters="$filters[$((n - 1)):v]scale=1280:720,fps=25,tpad=stop_mode=clone:stop_duration=2,setsar=1[v$((n - 1))];"
done
joined="[v0][v1][v2][v3][v4][v5]concat=n=6:v=1:a=0[out]"
# shellcheck disable=SC2086
ffmpeg -y -loglevel error $inputs -filter_complex "$filters$joined" -map "[out]" \
  -c:v libx264 -pix_fmt yuv420p -crf 26 -movflags +faststart "$out"
echo "wrote $out"
