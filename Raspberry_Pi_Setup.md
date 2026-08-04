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
sudo apt update && sudo apt install git python3-pip python3-smbus i2c-tools sysstat tesseract-ocr libgl1 pipewire wireplumber pipewire-audio pipewire-pulse libspa-0.2-bluetooth bluez libcamera-apps
```

### Other useful Packages

```bash
    sudo apt install rhvoice
    sudo apt install rhvoice-russian
    sudo apt install rhvoice-english   
    sudo apt install sysstat    

```

------------------------------------------------------------------------

## Bluetooth Audio Setup (PipeWire)

### 1. Pair the headset (no reboot)

Check that Bluetooth is not blocked:

```bash
rfkill list
sudo rfkill unblock bluetooth
```

Pair the headset:

```bash
bluetoothctl
power on
scan on
# wait until headset appears
scan off
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
```

If `connect` fails with:

```
org.bluez.Error.Failed br-connection-profile-unavailable
```

continue with the PipeWire setup.

---

### 2. Install PipeWire (logout/login or reboot recommended)

Bluetooth audio (A2DP) **requires PipeWire**. ALSA (`aplay`) alone cannot play to a Bluetooth headset.

```bash
sudo apt install -y pipewire wireplumber pipewire-pulse libspa-0.2-bluetooth pulseaudio-utils
```

---

### 3. Headless Raspberry Pi fix (restart WirePlumber or reboot)

Create the configuration directory:

```bash
mkdir -p ~/.config/wireplumber/wireplumber.conf.d
```

Create:

```
~/.config/wireplumber/wireplumber.conf.d/50-bluez-no-seat.conf
```

Contents:

```text
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
```

Restart WirePlumber:

```bash
systemctl --user restart wireplumber
```

(or simply reboot)

---

### 4. Verify and test

Reconnect if necessary:

```bash
bluetoothctl
connect XX:XX:XX:XX:XX:XX
```

Verify that the headset appears:

```bash
wpctl status
```

Test audio:

```bash
pw-play /usr/share/sounds/alsa/Front_Center.wav
```

**Note:** `pw-play` works directly with PipeWire. `aplay` requires additional ALSA→PipeWire integration.

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
tesserocr<2.10
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
