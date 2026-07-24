from email.mime import text

from mpu6050 import mpu6050
from gpiozero import Button
from enum import Enum
import subprocess
import time

# ----------------------------------------------------------
# Hardware
# ----------------------------------------------------------

touch = Button(22, pull_up=False)
mpu = mpu6050(0x68)


def speak(text):
    print(text)
    # os.system(f'echo "{text}" | RHVoice-test -p anna')    


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

def speak_text(text):
    subprocess.run(
        'RHVoice-test -p alan -o - | ffmpeg -loglevel error -i pipe:0 -af "alimiter,volume=10dB" -f wav - | aplay',
        input=text,
        text=True,
        shell=True
    )
    
def next_main_menu(menu):
    items = list(MainMenu)
    return items[(items.index(menu) + 1) % len(items)]

def next_settings_menu(menu):
    items = list(SettingsMenu)
    return items[(items.index(menu) + 1) % len(items)]


def speak_menu(menu):
    text = {
        MainMenu.READ_NEW_TEXT: "Read new text",
        MainMenu.REPEAT_LAST_TEXT: "Repeat last text",
        MainMenu.SETTINGS: "Settings",
        MainMenu.LEAVE: "Leave",
        SettingsMenu.CHANGE_LANGUAGE: "Change language",
        SettingsMenu.CHANGE_SOUND_LEVEL: "Change sound level",
        SettingsMenu.LEAVE: "Leave",
    }[menu]
    speak_text(text)

# ----------------------------------------------------------
# Actions
# ----------------------------------------------------------

# main loop actions

def process_new_image():
    print("Capture image")
    subprocess.run(["bash", "processENG.sh", "true"], check=True)


def repeat_last_text():
    print("Repeat text")
    subprocess.run(["bash", "processENG.sh", "false"], check=True)

# settings loop actions
def change_language():
    print("Change language")

def change_sound_level():
    print("Change sound level")

def stop_reading():
    subprocess.run(["pkill", "aplay"], check=False)

def wake_up():
    subprocess.run(
        'ffmpeg -loglevel error -f lavfi -i anullsrc=r=22050:cl=mono -t 0.25 -f wav - | aplay',
        shell=True,
        check=False
    )

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


class MainMenu(Enum):
    READ_NEW_TEXT = 0
    REPEAT_LAST_TEXT = 1
    SETTINGS = 2
    LEAVE = 3

# ----------------------------------------------------------
# Main menu
# ----------------------------------------------------------
def main_loop():
    print("Main loop")
    menu = MainMenu.READ_NEW_TEXT
    wait_for_touch_flag = True

    while True:
        if wait_for_touch_flag:
            wait_for_touch()
            wake_up()
            print("Touch detected")

        wait_for_touch_flag = False
        stop_reading() # stop any possible reading text
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