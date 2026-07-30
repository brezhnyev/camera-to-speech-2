# Raspberry Pi Setup Help

## 1. Locale / `raspi-config`

``` bash
sudo raspi-config
```

-   **Localisation Options → Locale**
-   Generate: `de_DE.UTF-8`
-   Default locale: `en_GB.UTF-8` (or `en_US.UTF-8`)
-   **Keyboard:** German
-   **Reboot**
-   Purpose: generate `de_DE.UTF-8` while keeping the system language
    English.

------------------------------------------------------------------------

## 2. Camera

``` bash
rpicam-still --list-cameras
rpicam-still -o test.jpg
```

------------------------------------------------------------------------

## 3. APT packages

``` bash
sudo apt install \
    git \
    python3-pip \
    python3-smbus \
    i2c-tools \
    sysstat \
    bluez-alsa-utils \
    libcamera-apps \
    tesseract-ocr \
    libgl1
```

------------------------------------------------------------------------

## 4. Bluetooth

``` text
bluetoothctl
power on
agent on
default-agent
scan on
pair <MAC>
trust <MAC>
connect <MAC>
```

Check:

``` bash
wpctl status
pactl list short sinks
```

------------------------------------------------------------------------

## 5. Python virtual environment

``` bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt --no-input
```

`--system-site-packages` is required so the venv can use apt-installed
modules (e.g. `python3-smbus`).

------------------------------------------------------------------------

## 6. `requirements.txt`

``` text
mpu6050-raspberrypi
opencv-python==4.10.0.84
numpy==1.26.4
tesserocr
piper-tts
```

------------------------------------------------------------------------

## 7. Piper voice

``` bash
mkdir -p voices
cd voices

wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx.json
```

------------------------------------------------------------------------

## 8. Notes

-   ONNX warning about `/sys/class/drm/card0` can be ignored.
-   `ImportError: libGL.so.1` → `sudo apt install libgl1`
-   `FileNotFoundError: tesseract` → `sudo apt install tesseract-ocr`
-   `python3-smbus` is required for the MPU6050.
