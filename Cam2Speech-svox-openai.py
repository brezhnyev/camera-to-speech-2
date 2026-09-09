from email.mime import image, text
import queue
import signal
from mpu6050 import mpu6050
from gpiozero import Button
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
import base64
import json
import sys
import smbus

sys.path.insert(0, "/home/pi/camera-to-speech-2/blaze_app_python")
sys.path.insert(0, "/home/pi/camera-to-speech-2/blaze_app_python/blaze_common")

from blaze_tflite.blazedetector import BlazeDetector
from blaze_tflite.blazelandmark import BlazeLandmark

from tesserocr import PyTessBaseAPI, RIL
from PIL import Image

TESSDATA_PATH = "/usr/share/tesseract-ocr/5/tessdata"
LANGUAGE_CONFIG = {
    "EN": {
        "tesseract": "eng",
        "nanotts": "en-GB",
        "battery": "Battery left: {percent:.0f} percent",
    },
    "DE": {
        "tesseract": "deu",
        "nanotts": "de-DE",
        "battery": "Verbleibende Akkuladung: {percent:.0f} Prozent",
    },
}
SYSTEM_LANGUAGE = "EN"
SOUND_DIR = "sounds/" + SYSTEM_LANGUAGE


def create_tesseract_api():
    return PyTessBaseAPI(
        path=TESSDATA_PATH,
        lang=LANGUAGE_CONFIG[SYSTEM_LANGUAGE]["tesseract"],
        psm=3,
    )


api = create_tesseract_api()


def set_system_language(language):
    global SYSTEM_LANGUAGE, SOUND_DIR, api

    if language not in LANGUAGE_CONFIG:
        raise ValueError(f"Unsupported language: {language}")

    if language == SYSTEM_LANGUAGE:
        return

    api.End()
    SYSTEM_LANGUAGE = language
    SOUND_DIR = "sounds/" + SYSTEM_LANGUAGE
    api = create_tesseract_api()

# os.setpriority(os.PRIO_PROCESS, 0, -10) # this causes interrupts (counter effect)

# reduce image length & width by this factor before running OCR (speeds
# things up on constrained hardware, at the cost of recognition accuracy on
# small text - tesseract works best with ~20-30px tall characters, and that
# shrinks along with the image). MIN_WORD_COUNT/MIN_MEAN_CONF below don't need
# to change with FACTOR: word counts and confidence percentages are both
# resolution-independent.
FACTOR = 1

# raspi camera capture resolution - must match wake_up()'s rpicam-still --width/--height
WIDTH = 4608
HEIGHT = 2592

# columns cropped off the left and right of the camera frame before
# OCR/finger-detection, keeping the centered square region - must match the
# [:, CROP_LEFT:CROP_RIGHT] crop in process_new_image_tesseract()
CROP_LEFT = (WIDTH-HEIGHT)
CROP_RIGHT = WIDTH

# rpicam-still always writes captures to this fixed path (see wake_up())
CAM_IMG = "img.jpg"
NANOTTS = "nanotts/nanotts"

# a paragraph must have at least this many recognized words, with at least
# this mean confidence, to be treated as a real text block (vs. photo/logo
# noise that Tesseract stumbles into on --psm 3 page segmentation)
MIN_WORD_COUNT = 2
MIN_MEAN_CONF = 60
MIN_WORD_CONF = 60

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
DESKEW_FACTOR = 4

# -------- contrast enhancement (CLAHE) before OCR --------
# photographed pages usually have uneven lighting (shadows, flash falloff
# across the page), so a single global equalize/normalize either does
# nothing useful locally or blows out noise in already-bright regions.
# CLAHE equalizes contrast within small tiles instead, which handles uneven
# lighting much better.
CLAHE_CLIP_LIMIT = 2.0
CLAHE_TILE_SIZE = (8, 8)

cam = None

LOCAL_PROCESSING = True

# ----------------------------------------------------------
# Hardware
# ----------------------------------------------------------

touch = Button(22, pull_up=False)
mpu = mpu6050(0x68)


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


def read_text_file_aloud():

    text = ""

    if os.path.exists("battery.txt"):
        text = open("battery.txt", encoding="utf-8").read()

    else:
        try:
            with open("text.json", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            speak_instruction(Instructions.TEXT_NOT_FOUND)
            return

        objects = data.get("objects", [])

        # Finger exists -> read the closest object and all objects embedded in it
        if os.path.exists("finger-pos.txt") and objects:

            with open("finger-pos.txt") as f:
                x, y = map(float, f.read().strip().split(","))

            def distance(b):
                cx = b["x"] + b["w"] / 2
                cy = b["y"] + b["h"] / 2
                return (cx - x) ** 2 + (cy - y) ** 2

            remaining = list(objects)
            selected = []

            root = min(remaining, key=distance)
            selected.append(root)
            remaining.remove(root)

            def is_inside(inner, outer):
                return (
                    inner["x"] >= outer["x"]
                    and inner["y"] >= outer["y"]
                    and inner["x"] + inner["w"] <= outer["x"] + outer["w"]
                    and inner["y"] + inner["h"] <= outer["y"] + outer["h"]
                )

            while remaining:
                candidates = [obj for obj in remaining if is_inside(obj, root)]
                if not candidates:
                    break

                candidate = min(candidates, key=distance)
                selected.append(candidate)
                remaining.remove(candidate)

            text = "\n".join(
                obj["translation"] or obj["text"]
                for obj in selected
                if obj.get("translation") or obj.get("text")
            )

        # No finger -> scene + all objects
        else:
            texts = []

            if data.get("scene"):
                texts.append(data["scene"])

            for obj in objects:
                text = obj["translation"] or obj["text"]
                if text:
                    texts.append(text)

            text = "\n".join(texts)

    if not text.strip():
        speak_instruction(Instructions.TEXT_NOT_FOUND)
        return

    try:
        subprocess.run(
            [NANOTTS, "-v", LANGUAGE_CONFIG[SYSTEM_LANGUAGE]["nanotts"], "--play"],
            input=text,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        if error.returncode != -signal.SIGTERM:
            raise


def next_main_menu(menu):
    items = list(MainMenu)
    return items[(items.index(menu) + 1) % len(items)]

def next_settings_menu(menu):
    items = list(SettingsMenu)
    return items[(items.index(menu) + 1) % len(items)]

def next_language_menu(menu):
    items = list(LanguageMenu)
    return items[(items.index(menu) + 1) % len(items)]

def speak_language_menu(menu):
    text = {
        LanguageMenu.ENGLISH: "ENGLISH",
        LanguageMenu.GERMAN: "GERMAN",
    }[menu]
    subprocess.run(["aplay", SOUND_DIR + "/" + text + ".wav"], check=True)

def speak_processing_menu(menu):
    if LOCAL_PROCESSING:
        text = {
            ProcessingSettingsMenu.TOGGLE_PROCESSING: "ACTIVATE_ONLINE_PROCESSING",
            ProcessingSettingsMenu.LEAVE: "LEAVE",
        }[menu]
    else:
        text = {
            ProcessingSettingsMenu.TOGGLE_PROCESSING: "ACTIVATE_OFFLINE_PROCESSING",
            ProcessingSettingsMenu.LEAVE: "LEAVE",
        }[menu]
    subprocess.run(["aplay", SOUND_DIR + "/" + text + ".wav"], check=True)

def speak_menu(menu):
    text = {
        MainMenu.TAKE_NEW_PHOTO: "TAKE_NEW_PHOTO",
        MainMenu.ASK_AGAIN: "ASK_AGAIN",
        MainMenu.REPEAT_LAST_TEXT: "REPEAT_LAST_TEXT",
        MainMenu.BATTERY_STATUS: "BATTERY_STATUS",
        MainMenu.SETTINGS: "SETTINGS",
        MainMenu.LEAVE: "LEAVE",
        SettingsMenu.CHANGE_LANGUAGE: "CHANGE_LANGUAGE",
        SettingsMenu.CHANGE_SOUND_LEVEL: "CHANGE_SOUND_LEVEL",
        SettingsMenu.TOGGLE_PROCESSING: "TOGGLE_PROCESSING",
        SettingsMenu.LEAVE: "LEAVE",
    }[menu]
    subprocess.run(["aplay", SOUND_DIR + "/" + text + ".wav"], check=True)

def speak_instruction(instruction):
    text = {
        Instructions.KEEP_CAMERA: "KEEP_CAMERA",
        Instructions.PHOTO_TAKEN: "PHOTO_TAKEN",
        Instructions.TEXT_NOT_FOUND: "TEXT_NOT_FOUND",
        Instructions.POINT_BLOCK: "POINT_BLOCK",
        Instructions.HAND_FOUND: "HAND_FOUND",
        Instructions.NO_HAND_FOUND: "NO_HAND_FOUND",
    }[instruction]
    subprocess.run(["aplay", SOUND_DIR + "/" + text + ".wav"], check=True)
            

# ----------------------------------------------------------
# Actions
# ----------------------------------------------------------

def checkpoint(label, t_prev):
    now = time.perf_counter()
    print(f"[profile] {label}: {now - t_prev:.3f}s")
    return now

# main loop actions

def cleanup_photo_files():
    # run once per touch, before any captures - NOT inside take_photo(),
    # since a session now takes two photos and the second one must not wipe
    # out the first photo/text.txt while they're still being processed
    subprocess.run(
        ["rm", "-f", "text.json", CAM_IMG, "img-finger.jpg", "finger-pos.txt", "battery.txt"],
        check=True,
    )

def take_photo(name):
    # -------- profiling: checkpoints around each stage --------
    t_prev = time.perf_counter()
    global cam
    if cam is None:
        return

    print("Capture image:", name)
    if name == "img-no-finger.jpg":
        speak_instruction(Instructions.KEEP_CAMERA)  # not in thread!
    cam.send_signal(signal.SIGUSR1)
    # --signal makes rpicam-still perform a single capture then exit - wait for
    # that exit so the file is guaranteed to be fully written before it's read
    # below (sending the signal alone doesn't block until the capture and
    # file write actually finish).
    while not os.path.exists(CAM_IMG): # TODO: not reliable!
        time.sleep(0.1)
    # cam always writes to CAM_IMG - rename to the requested name so the
    # two captures per session don't overwrite each other
    subprocess.run(["aplay", "sounds/camera_shutter.wav"], check=True) # TODO: still potential for parallel execution
    os.replace(CAM_IMG, name)
    if (name == "img-finger.jpg"): # TODO: nicer way to do this
        cam.terminate()
        cam.wait(timeout=5)
        cam = None
    t_prev = checkpoint("capture image", t_prev)


def process_new_image_openai():

    cleanup_photo_files()
    IMAGE = "img-no-finger.jpg"
    take_photo(IMAGE)
    finger_thread = threading.Thread(target=find_finger_tip)
    finger_thread.start()

    OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

    source = cv2.imread(IMAGE)
    if source is None:
        return

    raw = cv2.rotate(
        source[:, CROP_LEFT:CROP_RIGHT],
        cv2.ROTATE_90_CLOCKWISE,
    )
    img = cv2.resize(
        raw,
        (round(raw.shape[1] / FACTOR), round(raw.shape[0] / FACTOR)),
        interpolation=cv2.INTER_AREA
    )

    _, encoded = cv2.imencode(".jpg", img)
    image = base64.b64encode(encoded).decode()

    payload = {
        "model": "gpt-5.6-luna",
        "input": [{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": f"""
                    Analyze the image for a blind user.

                    System language: {SYSTEM_LANGUAGE}

                    Return only valid JSON matching exactly this structure:

                    {{
                    "scene": "short description of the whole scene",
                    "objects": [
                        {{
                        "x": 0,
                        "y": 0,
                        "w": 0,
                        "h": 0,
                        "text": null,
                        "translation": null
                        }}
                    ]
                    }}

                    Rules:
                    - Return at most 10 important logical objects.
                    - x,y,w,h are pixel coordinates in the supplied image.
                    - x,y are the top-left corner of the bounding box.
                    - For non-text objects, place a brief description in the "text" field in the system language.
                    - For detected text blocks, put the exact readable original text in the "text" field.
                    - For detected text blocks whose language differs from the system language, put its translation in the "translation" field.
                    - Otherwise set "translation" to null.
                    - "scene" is a short description of the whole image in the system language.
                    """
                },
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{image}"
                }
            ]
        }]
    }

    t = time.time()

    r = subprocess.run(
        [
            "curl", "-s",
            "https://api.openai.com/v1/responses",
            "-H", "Authorization: Bearer " + OPENAI_API_KEY,
            "-H", "Content-Type: application/json",
            "--data-binary", "@-"
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True
    )

    print("API:", time.time() - t)

    try:
        data = json.loads(r.stdout)

        text = None
        for item in data["output"]:
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = content["text"]
                    break
            if text:
                break

        if not text:
            raise ValueError("No output_text returned")

        result = json.loads(text)

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print("OpenAI response error:", e)
        speak_instruction(Instructions.TEXT_NOT_FOUND)
        return

    with open("text.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    finger_thread.join()
    threading.Thread(target=read_text_file_aloud).start()


def process_new_image_tesseract():

    cleanup_photo_files()
    IMAGE = "img-no-finger.jpg"
    take_photo(IMAGE)
    finger_thread = threading.Thread(target=find_finger_tip)
    finger_thread.start()

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
    # -------- profiling: checkpoints around each stage --------
    t_prev = time.perf_counter()

    source = cv2.imread(IMAGE)
    if source is None:
        return

    raw = cv2.rotate(
        cv2.cvtColor(source[:, CROP_LEFT:CROP_RIGHT], cv2.COLOR_BGR2GRAY),
        cv2.ROTATE_90_CLOCKWISE,
    )
    img = cv2.resize(
        raw,
        (round(raw.shape[1] / FACTOR), round(raw.shape[0] / FACTOR)),
        interpolation=cv2.INTER_AREA
    )
    t_prev = checkpoint("load + resize", t_prev)

    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILE_SIZE)
    img = clahe.apply(img)
    t_prev = checkpoint("contrast enhancement (CLAHE)", t_prev)

    deskew_img = cv2.resize(
        img, (img.shape[1] // DESKEW_FACTOR, img.shape[0] // DESKEW_FACTOR)
    )
    angle = deskew_angle(deskew_img)
    if abs(angle) > 0.1:
        img = rotate_image(img, angle)
    t_prev = checkpoint("deskew", t_prev)

    out = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    # -------- single tesseract call does layout analysis + OCR in one pass --------
    # --psm 3 (fully automatic page segmentation) finds paragraph/block boxes
    # itself, so there's no need for MSER, dilation, or a separate OCR filter step.
    api.SetImageBytes(
        img.tobytes(),
        img.shape[1],
        img.shape[0],
        1,
        img.shape[1]
    )
    api.Recognize()
    t_prev = checkpoint("tesseract OCR", t_prev)

    blocks = []

    ri = api.GetIterator()
    if ri:
        while True:  # one iteration per paragraph
            x1, y1, x2, y2 = ri.BoundingBox(RIL.PARA)
            para_conf = ri.Confidence(RIL.PARA)

            # walk the words inside this paragraph, keeping only the ones
            # confident enough - drops stray garbage characters/symbols
            # Tesseract misreads from noise within an otherwise good paragraph
            words = []
            while True:
                word_text = (ri.GetUTF8Text(RIL.WORD) or "").strip()
                word_conf = ri.Confidence(RIL.WORD)
                if word_text and word_conf >= MIN_WORD_CONF:
                    words.append(word_text)

                if ri.IsAtFinalElement(RIL.PARA, RIL.WORD):
                    break
                ri.Next(RIL.WORD)

            if words:
                blocks.append({
                    "left": x1,
                    "top": y1,
                    "width": x2 - x1,
                    "height": y2 - y1,
                    "text": " ".join(words),
                    "conf": para_conf,
                })

            if not ri.Next(RIL.PARA):
                break
    t_prev = checkpoint("parse tsv", t_prev)

    # drop paragraphs with too few words or too low confidence - filters out
    # photo/logo noise Tesseract stumbles into on --psm 3 page segmentation
    blocks = [
        b for b in blocks
        if len(b["text"].split()) >= MIN_WORD_COUNT and b["conf"] >= MIN_MEAN_CONF
    ]

    t_prev = checkpoint("filter blocks", t_prev)
    print(len(blocks))

    data = {
        "scene": None,
        "objects": [
            {
                "x": b["left"],
                "y": b["top"],
                "w": b["width"],
                "h": b["height"],
                "text": b["text"],
                "translation": None
            }
            for b in blocks
        ]
    }

    with open("text.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    t_prev = checkpoint("write text.json + words.jpg", t_prev)

    for b in blocks:
        x, y, w, h = b["left"], b["top"], b["width"], b["height"]
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 0), 1)
    cv2.imwrite("words.jpg", out)
    t_prev = checkpoint("write text.txt + words.jpg", t_prev)

    finger_thread.join()
    threading.Thread(target=read_text_file_aloud).start()


def find_finger_tip():

    subprocess.run(["rm", "-f", "finger-pos.txt"], check=True)
    speak_instruction(Instructions.POINT_BLOCK)
    time.sleep(2)
    IMAGE = "img-finger.jpg"
    take_photo(IMAGE)

    detector = BlazeDetector("blazepalm")
    detector.load_model("/home/pi/camera-to-speech-2/blaze_app_python/blaze_tflite/models/palm_detection_lite.tflite")

    landmark = BlazeLandmark("blazehandlandmark")
    landmark.load_model("/home/pi/camera-to-speech-2/blaze_app_python/blaze_tflite/models/hand_landmark_lite.tflite")

    source = cv2.imread(IMAGE)
    if source is None:
        return

    raw = cv2.rotate(
        cv2.cvtColor(source[:, CROP_LEFT:CROP_RIGHT], cv2.COLOR_BGR2RGB),
        cv2.ROTATE_90_CLOCKWISE,
    )
    img = cv2.resize(
        raw,
        (round(raw.shape[1] / FACTOR), round(raw.shape[0] / FACTOR)),
        interpolation=cv2.INTER_AREA
    )

    t = time.time()

    img1, scale1, pad1 = detector.resize_pad(img)
    nd = detector.predict_on_image(img1)

    print("Hands:", len(nd))

    if len(nd):
        threading.Thread(target=speak_instruction, args=(Instructions.HAND_FOUND,)).start()
        detections = detector.denormalize_detections(nd, scale1, pad1)

        xc, yc, scale, theta = detector.detection2roi(detections)

        roi_img, roi_affine, roi_box = landmark.extract_roi(
            img, xc, yc, theta, scale
        )

        result = landmark.predict(roi_img)

        if len(result) == 3:
            flags, landmarks, handedness = result
        else:
            flags, landmarks = result

        print("Landmarks shape:", landmarks.shape)
        print("Flags:", flags)

        landmarks = landmark.denormalize_landmarks(landmarks, roi_affine)

        # landmark 8 = index fingertip, in the FACTOR-downscaled image's
        # coordinate space - scale it back up to raw pixel coordinates
        # before the crop/rotate transform below
        tip = landmarks[0, 8]

        print("Index fingertip:", int(tip[0]), int(tip[1])) # in full size cropped and rotated image space

        cv2.circle(img, (int(tip[0]), int(tip[1])), 20, (0, 0, 255), 5)
        cv2.imwrite("finger-result.jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))

        with open("finger-pos.txt", "w") as f:
            f.write(f"{tip[0]},{tip[1]}\n")

    else:
        speak_instruction(Instructions.NO_HAND_FOUND)


def repeat_last_text():
    print("Repeat text")
    threading.Thread(target=read_text_file_aloud).start()

def ask_again():
    print("Asking again")
    find_finger_tip()
    threading.Thread(target=read_text_file_aloud).start()

def check_battery_status():
    print("Checking battery status")
    bus = smbus.SMBus(1)

    v = bus.read_word_data(0x36, 0x02)
    v = ((v & 0xFF) << 8) | (v >> 8)

    soc = bus.read_word_data(0x36, 0x04)
    soc = ((soc & 0xFF) << 8) | (soc >> 8)

    print("Voltage:", v * 1.25 / 1000 / 16, "V")
    print("Battery:", soc / 256, "%")

    with open("battery.txt", "w", encoding="utf-8") as f:
        f.write(LANGUAGE_CONFIG[SYSTEM_LANGUAGE]["battery"].format(percent=soc / 256) + "\n")

    read_text_file_aloud()


def change_sound_level():
    print("Change sound level")


def stop_reading():

    # nanotts owns playback directly when --play is used.
    subprocess.run(["pkill", "-x", "nanotts"], check=False)
    subprocess.run(["pkill", "aplay"], check=False)


# wake_up() plays a short blip of silence before the first real speech, to
# "wake up" the (usually bluetooth) audio sink from standby - without it, the
# first syllable of the following phrase (e.g. "Read new text") gets cut off.
# It used to run `ffmpeg -f lavfi -i anullsrc ... | aplay` to generate that
# silence on the fly, which meant a shell + ffmpeg + aplay fork on every
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
    global cam
    subprocess.run(["aplay", WAKEUP_WAV_PATH], check=False)
    cam = subprocess.Popen(
        [
            "rpicam-still",
            "--signal",
            "-t", "999999",
            "--width", str(WIDTH),
            "--height", str(HEIGHT),
            "-o", CAM_IMG,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

# ----------------------------------------------------------
# Settings menu
# ----------------------------------------------------------

# settings loop actions
class LanguageMenu(Enum):
    ENGLISH = "EN"
    GERMAN = "DE"

def change_language_loop():
    menu = LanguageMenu.ENGLISH

    while True:
        speak_language_menu(menu)
        event = wait_for_yes_no()

        if event is True:
            set_system_language(menu.value)
            print("Selected language:", menu.value)
            return

        if event is False:
            menu = next_language_menu(menu)
            continue

        return

class ProcessingSettingsMenu(Enum):
    TOGGLE_PROCESSING = 0
    LEAVE = 1

def processing_setting_loop():
    global LOCAL_PROCESSING
    print("Setting processing loop")
    menu = ProcessingSettingsMenu.TOGGLE_PROCESSING

    while True:
        speak_processing_menu(menu)
        event = wait_for_yes_no()
        if event == True:
            if menu == ProcessingSettingsMenu.TOGGLE_PROCESSING:
                LOCAL_PROCESSING = not LOCAL_PROCESSING
                print("Toggled processing mode:", "ONLINE" if LOCAL_PROCESSING else "OFFLINE")
                return
            if menu == ProcessingSettingsMenu.LEAVE:
                print("Returning to settings menu")
                return
        
        elif event == False:
            print("Moving to next menu")
            menu = next_settings_menu(menu)
            continue

        elif event is None:  # timeout
            print("Returning to main menu")
            return

class SettingsMenu(Enum):
    CHANGE_LANGUAGE = 0
    CHANGE_SOUND_LEVEL = 1
    TOGGLE_PROCESSING = 2
    LEAVE = 3

def settings_loop():
    print("Settings loop")
    menu = SettingsMenu.CHANGE_LANGUAGE

    while True:
        speak_menu(menu)
        event = wait_for_yes_no()

        if event == True:
            if menu == SettingsMenu.CHANGE_LANGUAGE:
                change_language_loop()

            elif menu == SettingsMenu.CHANGE_SOUND_LEVEL:
                change_sound_level()

            elif menu == SettingsMenu.TOGGLE_PROCESSING:
                processing_setting_loop()

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
    TEXT_NOT_FOUND = 2
    POINT_BLOCK = 3
    HAND_FOUND = 4
    NO_HAND_FOUND = 5

# ----------------------------------------------------------
# Main menu
# ----------------------------------------------------------

class MainMenu(Enum):
    TAKE_NEW_PHOTO = 0
    ASK_AGAIN = 1
    REPEAT_LAST_TEXT = 2
    BATTERY_STATUS = 3
    SETTINGS = 4
    LEAVE = 5

def main_loop():
    print("Main loop")
    subprocess.run(["aplay", WAKEUP_WAV_PATH], check=False)
    subprocess.run(["aplay", SOUND_DIR + "/READY_OPERATE.wav"], check=True)
    menu = MainMenu.TAKE_NEW_PHOTO
    wait_for_touch_flag = True
    global cam

    while True:
        # stop any still-playing text-reading before starting new audio
        # (wake-up beep and/or the menu announcement below) - otherwise the
        # new audio has to wait for the audio device to free up, which can
        # take as long as the leftover reading has left to play.

        if wait_for_touch_flag:
            if cam is not None:
                cam.terminate()
                cam = None
            wait_for_touch()
            wake_up()
            print("Touch detected")

        stop_reading()
        wait_for_touch_flag = False
        speak_menu(menu)
        event = wait_for_yes_no()

        if event == True:
            if menu == MainMenu.TAKE_NEW_PHOTO:
                if LOCAL_PROCESSING:
                    process_new_image_tesseract()
                else:
                    process_new_image_openai()

            elif menu == MainMenu.ASK_AGAIN:
                ask_again()

            elif menu == MainMenu.REPEAT_LAST_TEXT:
                repeat_last_text()

            elif menu == MainMenu.BATTERY_STATUS:
                check_battery_status()

            elif menu == MainMenu.SETTINGS:
                settings_loop()

            elif menu == MainMenu.LEAVE:
                print("Leaving main menu")

            menu = MainMenu.TAKE_NEW_PHOTO
            wait_for_touch_flag = True
            print("Returning to main menu")
            continue

        elif event == False:
            print("Moving to next menu")
            menu = next_main_menu(menu)
            continue

        elif event is None:  # timeout
            print("Returning to main menu")
            menu = MainMenu.TAKE_NEW_PHOTO
            wait_for_touch_flag = True
            continue



if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("Exiting program")

        if cam is not None:
            subprocess.run(["pkill", "rpicam-still"], check=False)

        if api is not None:
            api.End()