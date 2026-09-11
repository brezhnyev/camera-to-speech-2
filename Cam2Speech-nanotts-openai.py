from email.mime import image, text
import signal
from mpu6050 import mpu6050
from gpiozero import Button
from enum import Enum
import subprocess
import threading
import time
import cv2
import math
import wave
import numpy as np
import subprocess
import os
import json
import smbus

from tesserocr import PyTessBaseAPI, RIL
from PIL import Image

TESSDATA_PATH = "/usr/share/tesseract-ocr/5/tessdata"
LANGUAGE_CONFIG = {
    "EN": {
        "tesseract": "eng",
        "nanotts": "en-GB",
        "battery": "Battery left: {percent:.0f} percent",
        "text": "Recognized text"
    },
    "DE": {
        "tesseract": "deu",
        "nanotts": "de-DE",
        "battery": "Verbleibende Akkuladung: {percent:.0f} Prozent",
        "text": "Erkannter Text"
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


def read_text_file_aloud(text=None):

    if text is None:
        text = ""

        try:
            with open("text.json", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            speak_instruction(Instructions.TEXT_NOT_FOUND)
            return

        if data.get("scene"):
            text += data["scene"]

        text += LANGUAGE_CONFIG[SYSTEM_LANGUAGE]["text"] + ":\n"

        if data.get("text"):
            text += data["text"]

    if not text.strip():
        speak_instruction(Instructions.TEXT_NOT_FOUND)
        return

    speak_text_now(text)


def speak_text_now(text):
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


def next_menu(menu, option):
    items = list(menu)
    return items[(items.index(option) + 1) % len(items)]

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
            

def play_waiting_sound(stop_event):
    while not stop_event.is_set():
        player = subprocess.Popen(
            ["aplay", "sounds/clock-tick.wav"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        while player.poll() is None:
            if stop_event.wait(0.05):
                player.terminate()
                player.wait()
                return

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


def upload_image_openai(api_key, image_bytes):
    r = subprocess.run(
        [
            "curl", "-s",
            "https://api.openai.com/v1/files",
            "-H", "Authorization: Bearer " + api_key,
            "-F", "purpose=user_data",
            "-F", "file=@-;filename=image.jpg;type=image/jpeg",
        ],
        input=image_bytes,
        capture_output=True,
    )
    data = json.loads(r.stdout)
    return data["id"]


def request_openai_text(api_key, file_id, prompt_text):
    payload = {
        "model": "gpt-5.6-luna",
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt_text},
                {"type": "input_image", "file_id": file_id},
            ]
        }]
    }

    r = subprocess.run(
        [
            "curl", "-s",
            "https://api.openai.com/v1/responses",
            "-H", "Authorization: Bearer " + api_key,
            "-H", "Content-Type: application/json",
            "--data-binary", "@-"
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True
    )

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

    return text


def parse_original_and_translation(response):
    # parses the "ORIGINAL: ...\nTRANSLATION: ..." plain-text format used by
    # both the scene and text-detection prompts below
    original = None
    translation = None
    for line in response.splitlines():
        line = line.strip()
        if line.startswith("ORIGINAL:"):
            original = line[len("ORIGINAL:"):].strip()
        elif line.startswith("TRANSLATION:"):
            value = line[len("TRANSLATION:"):].strip()
            translation = None if value.upper() in ("NONE", "NULL", "") else value
    return original, translation


def process_new_image_openai():

    cleanup_photo_files()
    IMAGE = "img-no-finger.jpg"
    take_photo(IMAGE)

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

    t = time.time()

    try:
        stop_tick = threading.Event()
        tick_thread = threading.Thread(
            target=play_waiting_sound,
            args=(stop_tick,)
        )
        tick_thread.start()
        # upload the image once so both requests below can reference it by
        # file_id, instead of re-sending the base64-encoded image twice
        file_id = upload_image_openai(OPENAI_API_KEY, encoded.tobytes())
        scene_response = request_openai_text(
            OPENAI_API_KEY, file_id,
            f"""
            Briefly describe the scene in the image for a blind user.

            System language: {SYSTEM_LANGUAGE}

            Respond with exactly two lines, nothing else:
            ORIGINAL: <brief description of the scene, in its original language>
            TRANSLATION: <translation of the description into {SYSTEM_LANGUAGE}, or the word NONE if the description is already in {SYSTEM_LANGUAGE}>
            """
        )
        scene_text, scene_translation = parse_original_and_translation(scene_response)
        stop_tick.set()
        tick_thread.join()
        subprocess.run(["aplay", "sounds/microwave.wav"], check=False)

        # read the scene aloud right away - translation if there is one,
        # otherwise the original - while the text-detection request for the
        # same image runs concurrently with speaking the scene information; join both before continuing so the
        # results below only combine once both have finished
        text_request = {}

        def fetch_text():
            try:
                response = request_openai_text(
                    OPENAI_API_KEY, file_id,
                    f"""
                    Extract the meaningful text from the image for a blind user.

                    System language: {SYSTEM_LANGUAGE}

                    Ignore:
                    - website names, navigation, menus, category labels, ads, buttons, and repeated UI text
                    - decorative or isolated text that does not contribute meaningful information
                    - separators and visual punctuation such as ·

                    Preserve the meaningful content naturally, including headlines, descriptions, author names, and relevant article text.
                    Do not describe the page or add information that is not written there.

                    Respond with exactly two lines, nothing else:
                    ORIGINAL: <meaningful original text, formatted naturally for speech>
                    TRANSLATION: <translation into {SYSTEM_LANGUAGE}, or NONE if already in {SYSTEM_LANGUAGE}>
                    """
                )
                text_request["text"], text_request["translation"] = parse_original_and_translation(response)
            except ValueError as error:
                text_request["error"] = error

        text_thread = threading.Thread(target=fetch_text)
        speak_thread = threading.Thread(target=speak_text_now, args=(scene_translation or scene_text,))

        text_thread.start()
        speak_thread.start()

        text_thread.join()
        speak_thread.join()

        if "error" in text_request:
            raise text_request["error"]

    except ValueError as e:
        print("OpenAI response error:", e)
        speak_instruction(Instructions.TEXT_NOT_FOUND)
        return

    print("API:", time.time() - t)

    result = {
        "scene": scene_translation or scene_text,
        "text": text_request["translation"] or text_request["text"],
    }

    with open("text.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    threading.Thread(target=read_text_file_aloud, args=(text_request["translation"] or text_request["text"],)).start()


def process_new_image_tesseract():

    cleanup_photo_files()
    IMAGE = "img-no-finger.jpg"
    take_photo(IMAGE)

    stop_tick = threading.Event()
    tick_thread = threading.Thread(
        target=play_waiting_sound,
        args=(stop_tick,)
    )
    tick_thread.start()

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
        "text": "\n".join(b["text"] for b in blocks)
    }

    with open("text.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    t_prev = checkpoint("write text.json + words.jpg", t_prev)

    for b in blocks:
        x, y, w, h = b["left"], b["top"], b["width"], b["height"]
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 0), 1)

    cv2.imwrite("words.jpg", out)
    t_prev = checkpoint("write text.txt + words.jpg", t_prev)

    stop_tick.set()
    tick_thread.join()
    subprocess.run(["aplay", "sounds/microwave.wav"], check=False)

    threading.Thread(target=read_text_file_aloud).start()


def repeat_last_text():
    print("Repeat text")
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

    read_text_file_aloud(LANGUAGE_CONFIG[SYSTEM_LANGUAGE]["battery"].format(percent=soc / 256) + "\n")


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
    menu_option = ProcessingSettingsMenu.TOGGLE_PROCESSING

    while True:
        speak_processing_menu(menu_option)
        event = wait_for_yes_no()
        if event == True:
            if menu_option == ProcessingSettingsMenu.TOGGLE_PROCESSING:
                print("Toggled processing mode:", "ONLINE" if LOCAL_PROCESSING else "OFFLINE")
                LOCAL_PROCESSING = not LOCAL_PROCESSING
                return
            if menu_option == ProcessingSettingsMenu.LEAVE:
                print("Returning to settings menu")
                return
        
        elif event == False:
            print("Moving to next menu")
            menu_option = next_menu(ProcessingSettingsMenu, menu_option)
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
    menu_option = SettingsMenu.CHANGE_LANGUAGE

    while True:
        speak_menu(menu_option)
        event = wait_for_yes_no()

        if event == True:
            if menu_option == SettingsMenu.CHANGE_LANGUAGE:
                change_language_loop()

            elif menu_option == SettingsMenu.CHANGE_SOUND_LEVEL:
                change_sound_level()

            elif menu_option == SettingsMenu.TOGGLE_PROCESSING:
                processing_setting_loop()

            elif menu_option == SettingsMenu.LEAVE:
                print("Returning to main menu")

            return    
                
        elif event == False:
            print("Moving to next menu")
            menu_option = next_menu(SettingsMenu, menu_option)
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
    REPEAT_LAST_TEXT = 2
    BATTERY_STATUS = 3
    SETTINGS = 4
    LEAVE = 5

def main_loop():
    print("Main loop")
    subprocess.run(["aplay", WAKEUP_WAV_PATH], check=False)
    subprocess.run(
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.75"],
        check=False
    )
    subprocess.run(["aplay", SOUND_DIR + "/READY_OPERATE.wav"], check=True)
    menu_option = MainMenu.TAKE_NEW_PHOTO
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
        speak_menu(menu_option)
        event = wait_for_yes_no()

        if event == True:
            if menu_option == MainMenu.TAKE_NEW_PHOTO:
                if LOCAL_PROCESSING:
                    process_new_image_tesseract()
                else:
                    process_new_image_openai()

            elif menu_option == MainMenu.REPEAT_LAST_TEXT:
                repeat_last_text()

            elif menu_option == MainMenu.BATTERY_STATUS:
                check_battery_status()

            elif menu_option == MainMenu.SETTINGS:
                settings_loop()

            elif menu_option == MainMenu.LEAVE:
                print("Leaving main menu")

            menu_option = MainMenu.TAKE_NEW_PHOTO
            wait_for_touch_flag = True
            print("Returning to main menu")
            continue

        elif event == False:
            print("Moving to next menu")
            menu_option = next_menu(MainMenu, menu_option)
            continue

        elif event is None:  # timeout
            print("Returning to main menu")
            menu_option = MainMenu.TAKE_NEW_PHOTO
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