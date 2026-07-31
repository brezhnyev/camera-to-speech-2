from email.mime import text
from mpu6050 import mpu6050
from gpiozero import Button
from piper.voice import PiperVoice
from enum import Enum
import subprocess
import threading
import time
import cv2
import csv
import io
import math
import re
import wave
import numpy as np
import subprocess
import os

# os.setpriority(os.PRIO_PROCESS, 0, -10) # this causes interrupts (counter effect)

# reduce image length & width by this factor before running OCR (speeds
# things up on constrained hardware, at the cost of recognition accuracy on
# small text - tesseract works best with ~20-30px tall characters, and that
# shrinks along with the image). MIN_WORD_COUNT/MIN_MEAN_CONF below don't need
# to change with FACTOR: word counts and confidence percentages are both
# resolution-independent.
FACTOR = 2

# a paragraph must have at least this many recognized words, with at least
# this mean confidence, to be treated as a real text block (vs. photo/logo
# noise that Tesseract stumbles into on --psm 3 page segmentation)
MIN_WORD_COUNT = 2
MIN_MEAN_CONF = 60

# Tesseract's own block_num/par_num grouping is unreliable - it estimates
# paragraph/block boundaries from line spacing, and gets it wrong on both
# very tight and very generous spacing. Instead, words are grouped into
# blocks ourselves via dilation + connected components, based on how many
# word-heights apart they are, which is far more predictable.
# LINE_MERGE_FACTOR: bridge gaps between lines up to this many word-heights
# apart (increase if a block with generous line spacing is still getting
# split into multiple blocks).
# WORD_MERGE_FACTOR: bridge gaps between words on the same line, and columns,
# up to this many word-heights apart (increase if words on the same line
# aren't merging; decrease if separate columns are merging together).
LINE_MERGE_FACTOR = 3.0
WORD_MERGE_FACTOR = 1.5

# -------- deskew: correct slight camera rotation before running OCR --------
# find near-horizontal line segments (text baselines, edges, etc.) via Hough
# transform, and rotate by their median angle. Only lines within +-MAX_SKEW
# degrees of horizontal are considered, so vertical lines (e.g. photo/logo
# edges) don't throw off the estimate. This only corrects slight skew, not
# 90/180-degree rotations (use tesseract's own OSD --psm 0 for that).
MAX_SKEW = 20

# deskew_angle only needs to find line directions, not read text, so it can
# run on an even smaller image than the one used for OCR - faster, and the
# detected angle is scale-independent so it still applies directly to img.
DESKEW_FACTOR = 2

# -------- contrast enhancement (CLAHE) before OCR --------
# photographed pages usually have uneven lighting (shadows, flash falloff
# across the page), so a single global equalize/normalize either does
# nothing useful locally or blows out noise in already-bright regions.
# CLAHE equalizes contrast within small tiles instead, which handles uneven
# lighting much better.
CLAHE_CLIP_LIMIT = 2.0
CLAHE_TILE_SIZE = (8, 8)

# Piper (onnx) text-to-speech model used for reading recognized text blocks.
PIPER_MODEL_PATH = "en_US-amy-low.onnx"

# ----------------------------------------------------------
# Hardware
# ----------------------------------------------------------

touch = Button(22, pull_up=False)
mpu = mpu6050(0x68)
piper_voice = PiperVoice.load(PIPER_MODEL_PATH)

# PiperVoice.load() already builds the onnxruntime session eagerly, but the
# *first* synthesize() call still pays extra one-time costs on top of that:
# onnxruntime's first Run() (thread pool spin-up, memory arena allocation,
# kernel selection) and espeak-ng's lazy phonemizer data init. Both are much
# slower than every call after. Pay that cost now, during startup, instead of
# during the user's first button press.
print("Warming up TTS model...")
for _ in piper_voice.synthesize("Warming up."):
    pass
print("TTS model ready.")

def wait_for_touch():
    touch.wait_for_press()


def wait_for_yes_no(timeout=5):

    start = time.time()

    while time.time() - start < timeout:

        g = mpu.get_gyro_data()

        # --------- tune these thresholds ----------
        if abs(g["z"]) > 90:
            return True      # nod

        if abs(g["x"]) > 90:
            return False     # shake
        # ------------------------------------------

    return None

# -------- Piper (onnx) streaming TTS for reading recognized text blocks --------
# reading one large block of text in a single pass would mean waiting for the
# whole thing to synthesize before any audio starts. Instead, split the text
# into small word chunks and stream each chunk's raw PCM straight into a
# single persistent pw-play process as soon as it's synthesized - no wav
# files, and the next chunk gets synthesized while pw-play is still playing
# the previous one (pw-play drains from its stdin pipe/buffer concurrently,
# so this overlap happens for free without any extra threads inside here).

MAX_WORDS_PER_CHUNK = 10

# trailing punctuation that marks a natural pause in speech
BREAK_PUNCT_RE = re.compile(r"[.,:;!?…\-–—()\[\]\"'“”‘’]$")

# short conjunctions/connectors that also tend to introduce a brief pause
BREAK_WORDS = {
    "and", "but", "or", "so", "yet", "nor",
    "because", "however", "though", "although",
    "while", "since", "then", "when", "where",
    "whereas", "wherever", "whenever", "whether",
    "after", "before", "once",
    "what", "which", "who", "whom", "whose",
    "if", "unless", "until",
    "also", "therefore", "thus", "hence",
    "meanwhile", "otherwise", "instead",
    "besides", "moreover", "furthermore",
    "nevertheless", "nonetheless",
    "indeed", "still", "rather",
    "despite", "through", "throughout", "via", "to"
}


def split_into_chunks(text, max_words=10):
    words = text.split()
    chunks = []

    start = 0
    last_break = -1

    for i, word in enumerate(words):
        if BREAK_PUNCT_RE.search(word):
            last_break = i + 1
        elif word.lower() in BREAK_WORDS:
            last_break = i

        if i - start + 1 == max_words:
            end = last_break if last_break >= start else i + 1
            chunks.append(" ".join(words[start:end]))
            start = end
            last_break = -1

    if start < len(words):
        chunks.append(" ".join(words[start:]))

    return chunks


def speak_text_streaming(text):
    chunks = split_into_chunks(text)
    if not chunks:
        return

    player = subprocess.Popen(
        [
            "pw-play",
            "--rate", str(piper_voice.config.sample_rate),
            "--channels", "1",
            "--format", "s16",
            "-",
        ],
        stdin=subprocess.PIPE,
    )

    try:
        for chunk in chunks:
            for audio_chunk in piper_voice.synthesize(chunk):
                player.stdin.write(audio_chunk.audio_int16_bytes)
    except (BrokenPipeError, OSError):
        pass  # player was killed (e.g. via stop_reading()) - stop synthesizing
    finally:
        try:
            player.stdin.close()
        except OSError:
            pass
        player.wait()


def read_text_file_aloud():
    try:
        with open("text.txt") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""

    if not text.strip():
        text = "Could not generate text."

    # run in the background so the main loop stays responsive (e.g. to
    # interrupt reading via stop_reading()), same as the previous `&`
    # backgrounded shell pipeline in readENG.sh.
    threading.Thread(target=speak_text_streaming, args=(text,)).start()


def next_main_menu(menu):
    items = list(MainMenu)
    return items[(items.index(menu) + 1) % len(items)]

def next_settings_menu(menu):
    items = list(SettingsMenu)
    return items[(items.index(menu) + 1) % len(items)]

def speak_menu(menu):
    text = {
        MainMenu.READ_NEW_TEXT: "READ_NEW_TEXT",
        MainMenu.REPEAT_LAST_TEXT: "REPEAT_LAST_TEXT",
        MainMenu.SETTINGS: "SETTINGS",
        MainMenu.LEAVE: "LEAVE",
        SettingsMenu.CHANGE_LANGUAGE: "CHANGE_LANGUAGE",
        SettingsMenu.CHANGE_SOUND_LEVEL: "CHANGE_SOUND_LEVEL",
        SettingsMenu.LEAVE: "LEAVE",
    }[menu]
    subprocess.run(["pw-play", "sounds/" + text + ".wav"], check=True)

def speak_instruction(instruction):
    text = {
        Instructions.KEEP_CAMERA: "KEEP_CAMERA",
        Instructions.PHOTO_TAKEN: "PHOTO_TAKEN",
    }[instruction]
    subprocess.run(["pw-play", "sounds/" + text + ".wav"], check=True)
            

# ----------------------------------------------------------
# Actions
# ----------------------------------------------------------

# main loop actions

def process_new_image():
    # -------- profiling: checkpoints around each stage --------
    t_prev = time.perf_counter()

    def checkpoint(label):
        nonlocal t_prev
        now = time.perf_counter()
        print(f"[profile] {label}: {now - t_prev:.3f}s")
        t_prev = now

    def deskew_angle(gray_img):
        edges = cv2.Canny(gray_img, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 180, threshold=100,
            minLineLength=gray_img.shape[1] // 4, maxLineGap=20,
        )
        if lines is None:
            return 0.0

        angles = [
            math.degrees(math.atan2(y2 - y1, x2 - x1))
            for x1, y1, x2, y2 in lines[:, 0]
            if -MAX_SKEW <= math.degrees(math.atan2(y2 - y1, x2 - x1)) <= MAX_SKEW
        ]
        return float(np.median(angles)) if angles else 0.0


    def rotate_image(gray_img, angle):
        h, w = gray_img.shape
        center = (w / 2, h / 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(
            gray_img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
        )

    print("Capture image")
    threading.Thread(target=speak_instruction, args=(Instructions.KEEP_CAMERA,)).start()
    subprocess.run(["rm", "-f", "text.txt", "img.png", "gray.png"], check=True)
    subprocess.run(["rpicam-still", "-t", "2000", "--width", "4608", "--height", "2592", "-o", "img.jpg"], check=True)
    threading.Thread(target=speak_instruction, args=(Instructions.PHOTO_TAKEN,)).start()
    checkpoint("capture image")

    img = cv2.resize(
        cv2.rotate(
            cv2.cvtColor(cv2.imread("img.jpg")[:, 2304:], cv2.COLOR_BGR2GRAY),
            cv2.ROTATE_90_CLOCKWISE,
        ),
        None,
        fx=1/FACTOR,
        fy=1/FACTOR,
        interpolation=cv2.INTER_AREA
    )
    checkpoint("load + resize")

    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILE_SIZE)
    img = clahe.apply(img)
    checkpoint("contrast enhancement (CLAHE)")

    deskew_img = cv2.resize(
        img, (img.shape[1] // DESKEW_FACTOR, img.shape[0] // DESKEW_FACTOR)
    )
    angle = deskew_angle(deskew_img)
    if abs(angle) > 0.1:
        img = rotate_image(img, angle)
    checkpoint("deskew")

    out = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    # -------- single tesseract call does layout analysis + OCR in one pass --------
    # --psm 3 (fully automatic page segmentation) finds paragraph/block boxes
    # itself, so there's no need for MSER, dilation, or a separate OCR filter step.
    ok, png = cv2.imencode(".png", img)
    checkpoint("encode png")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "3", "tsv"],
        input=png.tobytes(),
        capture_output=True,
    )
    checkpoint("tesseract OCR")

    reader = csv.DictReader(
        io.StringIO(result.stdout.decode("utf-8", errors="ignore")), delimiter="\t"
    )

    # collect all recognized words directly (level 5), ignoring Tesseract's own
    # block_num/par_num grouping entirely - see LINE_MERGE_FACTOR comment above.
    words_all = [
        row for row in reader if int(row["level"]) == 5 and row["text"].strip()
    ]
    checkpoint("parse tsv")

    # -------- group words into blocks via dilation + connected components --------
    mask = np.zeros(img.shape, dtype=np.uint8)
    heights = []
    for w in words_all:
        x, y = int(w["left"]), int(w["top"])
        ww, hh = int(w["width"]), int(w["height"])
        cv2.rectangle(mask, (x, y), (x + ww, y + hh), 255, -1)
        heights.append(hh)

    blocks = []
    if words_all:
        median_h = float(np.median(heights))
        kw = max(1, int(round(median_h * WORD_MERGE_FACTOR)))
        kh = max(1, int(round(median_h * LINE_MERGE_FACTOR)))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh))
        mask = cv2.dilate(mask, kernel)

        num_labels, labels = cv2.connectedComponents(mask, connectivity=8)

        grouped = {}
        for w in words_all:
            x, y = int(w["left"]), int(w["top"])
            ww, hh = int(w["width"]), int(w["height"])
            cx, cy = min(x + ww // 2, mask.shape[1] - 1), min(y + hh // 2, mask.shape[0] - 1)
            label = labels[cy, cx]
            if label == 0:
                continue
            grouped.setdefault(label, []).append(w)

        for words in grouped.values():
            confs = [float(w["conf"]) for w in words if float(w["conf"]) >= 0]
            if len(confs) < MIN_WORD_COUNT or sum(confs) / len(confs) < MIN_MEAN_CONF:
                continue

            xs1 = [int(w["left"]) for w in words]
            ys1 = [int(w["top"]) for w in words]
            xs2 = [int(w["left"]) + int(w["width"]) for w in words]
            ys2 = [int(w["top"]) + int(w["height"]) for w in words]

            x, y = min(xs1), min(ys1)
            w, h = max(xs2) - x, max(ys2) - y
            blocks.append((x, y, w, h, words, sum(confs) / len(confs)))

    # -------- drop blocks fully contained inside another, larger block --------
    # connected-component regions can be non-convex (e.g. a block wraps around
    # a gap), so a separate, disjoint component's bbox can end up entirely
    # inside another block's bounding rectangle even though they're unrelated
    # - keep only the larger, containing block in that case.
    def is_contained(inner, outer):
        ix, iy, iw, ih = inner[:4]
        ox, oy, ow, oh = outer[:4]
        return ix >= ox and iy >= oy and ix + iw <= ox + ow and iy + ih <= oy + oh

    blocks = [
        b for i, b in enumerate(blocks)
        if not any(
            i != j and is_contained(b, o) and o[2] * o[3] > b[2] * b[3]
            for j, o in enumerate(blocks)
        )
    ]
    checkpoint("block grouping (dilate + connected components)")

    # -------- rank blocks by likely relevance --------
    # 1. distance to image center (camera is usually pointed at the target text,
    #    so closer-to-center blocks are ranked first)
    # 2. character count (more text = more likely to be the intended content,
    #    vs. a short caption/watermark)
    # 3. mean OCR confidence
    # 4. block area (a bigger block is usually a more prominent piece of content)
    #
    # distance is bucketed into rings, so blocks that are roughly equally close
    # to the center get ranked by the next criteria instead of by float noise.

    img_h, img_w = img.shape
    img_cx, img_cy = img_w / 2, img_h / 2
    max_dist = math.hypot(img_cx, img_cy)

    def block_priority(block):
        x, y, w, h, words, mean_conf = block
        cx, cy = x + w / 2, y + h / 2
        dist_ratio = math.hypot(cx - img_cx, cy - img_cy) / max_dist  # 0=center, 1=corner
        center_bucket = round(dist_ratio, 1)
        char_count = sum(len(word["text"]) for word in words)
        area = w * h
        return (center_bucket, -char_count, -mean_conf, -area)

    blocks.sort(key=block_priority)
    blocks = [
        (x, y, w, h, " ".join(word["text"] for word in words))
        for x, y, w, h, words, mean_conf in blocks
    ]
    checkpoint("ranking")

    print(len(blocks))

    with open("text.txt", "w") as f:
        for i, (x, y, w, h, text) in enumerate(blocks, start=1):
            f.write(f"TEXT BLOCK {i}.\n\n")
            f.write(text)
            f.write("...\n\n")

    for x, y, w, h, text in blocks:
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 0), 1)     
    cv2.imwrite("words.png", out)
    checkpoint("write text.txt + words.png")

    read_text_file_aloud()
    checkpoint("tts (piper, backgrounded)")


def repeat_last_text():
    print("Repeat text")
    read_text_file_aloud()

# settings loop actions
def change_language():
    print("Change language")

def change_sound_level():
    print("Change sound level")

def stop_reading():
    subprocess.run(["pkill", "pw-play"], check=False)

# wake_up() plays a short blip of silence before the first real speech, to
# "wake up" the (usually bluetooth) audio sink from standby - without it, the
# first syllable of the following phrase (e.g. "Read new text") gets cut off.
# It used to run `ffmpeg -f lavfi -i anullsrc ... | pw-play` to generate that
# silence on the fly, which meant a shell + ffmpeg + pw-play fork on every
# single touch - and ffmpeg's own startup cost (loading its codec/format
# libs) is slow on a Pi Zero 2. The silence is static, so it's generated once
# at startup instead, and wake_up() just plays that file directly.
WAKEUP_WAV_PATH = "wakeup.wav"


def _generate_wakeup_wav(path, duration=0.25, rate=22050):
    n_samples = int(duration * rate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * n_samples)


_generate_wakeup_wav(WAKEUP_WAV_PATH)


def wake_up():
    subprocess.run(["pw-play", WAKEUP_WAV_PATH], check=False)

# ----------------------------------------------------------
# Settings menu
# ----------------------------------------------------------
class SettingsMenu(Enum):
    CHANGE_LANGUAGE = 0
    CHANGE_SOUND_LEVEL = 1
    LEAVE = 2

def settings_loop():
    print("Settings loop")
    menu = SettingsMenu.CHANGE_LANGUAGE

    while True:
        speak_menu(menu)
        event = wait_for_yes_no()

        if event == True:
            if menu == SettingsMenu.CHANGE_LANGUAGE:
                change_language()

            elif menu == SettingsMenu.CHANGE_SOUND_LEVEL:
                change_sound_level()

            elif menu == SettingsMenu.LEAVE:
                print("Returning to main menu")

            return    
                
        elif event == False:
            print("Moving to next menu")
            menu = next_settings_menu(menu)
            continue

        elif event is None:  # timeout
            print("Returning to main menu")
            return

#----------------------------------------------------------
# Instructions 
#----------------------------------------------------------

class Instructions(Enum):
    KEEP_CAMERA = 0
    PHOTO_TAKEN = 1

# ----------------------------------------------------------
# Main menu
# ----------------------------------------------------------

class MainMenu(Enum):
    READ_NEW_TEXT = 0
    REPEAT_LAST_TEXT = 1
    SETTINGS = 2
    LEAVE = 3

def main_loop():
    print("Main loop")
    menu = MainMenu.READ_NEW_TEXT
    wait_for_touch_flag = True

    while True:
        # stop any still-playing text-reading before starting new audio
        # (wake-up beep and/or the menu announcement below) - otherwise the
        # new audio has to wait for the audio device to free up, which can
        # take as long as the leftover reading has left to play.

        if wait_for_touch_flag:
            wait_for_touch()
            wake_up()
            print("Touch detected")

        stop_reading()
        wait_for_touch_flag = False
        speak_menu(menu)
        event = wait_for_yes_no()

        if event == True:
            if menu == MainMenu.READ_NEW_TEXT:
                process_new_image()

            elif menu == MainMenu.REPEAT_LAST_TEXT:
                repeat_last_text()

            elif menu == MainMenu.SETTINGS:
                settings_loop()

            elif menu == MainMenu.LEAVE:
                print("Leaving main menu")

            menu = MainMenu.READ_NEW_TEXT
            wait_for_touch_flag = True
            print("Returning to main menu")
            continue

        elif event == False:
            print("Moving to next menu")
            menu = next_main_menu(menu)
            continue

        elif event is None:  # timeout
            print("Returning to main menu")
            menu = MainMenu.READ_NEW_TEXT
            wait_for_touch_flag = True
            continue



if __name__ == "__main__":
    main_loop()