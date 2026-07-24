NEW_IMAGE="$1"

if [ "$NEW_IMAGE" == "true" ]; then
    rm -f text.txt
    echo "Keep camera still for a moment." | RHVoice-test -p alan -o - | ffmpeg -loglevel error -i pipe:0 -af "alimiter,volume=10dB" -f wav - 2>/dev/null | aplay &
    rpicam-still -n --immediate --width 2592 --height 1944 -o img.jpg
    echo "Photo taken, processing." | RHVoice-test -p alan -o - | ffmpeg -loglevel error -i pipe:0 -af "alimiter,volume=10dB" -f wav - 2>/dev/null | aplay &&
    convert img.jpg -crop 1296x1944+1296+0 -rotate 90 -despeckle -normalize -deskew 40% gray.jpg &&
    tesseract gray.jpg stdout -l eng --psm 3 2>/dev/null | tee text.txt
fi

if [ ! -s text.txt ]; then
    echo "Could not generate text." | RHVoice-test -p alan -o - | ffmpeg -loglevel error -i pipe:0 -af "alimiter,volume=10dB" -f wav - | aplay
else
    cat text.txt | RHVoice-test -p alan -o - | ffmpeg -loglevel error -i pipe:0 -af "alimiter,volume=10dB" -f wav - 2>/dev/null | aplay &
fi