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

## Software

### Operating System

* Raspberry Pi OS Lite 64 bit

### Typical CPU consumption.

Idle (600 MHz)

<pre>
pi@raspberrypi:~ $ mpstat -P ALL 1
Linux 6.12.93+rpt-rpi-v8 (raspberrypi)  24.07.2026      _aarch64_       (4 CPU)

11:36:30     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
11:36:31     all    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:36:31       0    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:36:31       1    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:36:31       2    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:36:31       3    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00

11:36:31     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
11:36:32     all    0,25    0,00    0,25    0,00    0,00    0,00    0,00    0,00    0,00   99,50
11:36:32       0    0,00    0,00    0,99    0,00    0,00    0,00    0,00    0,00    0,00   99,01
11:36:32       1    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:36:32       2    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:36:32       3    1,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00   99,00

11:36:32     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
11:36:33     all    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:36:33       0    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:36:33       1    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:36:33       2    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:36:33       3    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
</pre>

Full load (1GHz ffmpeg+tesseract)

<pre>
11:40:33     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
11:40:34     all   24,87    0,00    0,50    1,01    0,00    0,00    0,00    0,00    0,00   73,62
11:40:34       0    1,04    0,00    1,04    0,00    0,00    0,00    0,00    0,00    0,00   97,92
11:40:34       1   96,04    0,00    0,00    3,96    0,00    0,00    0,00    0,00    0,00    0,00
11:40:34       2    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00  100,00
11:40:34       3    0,98    0,00    0,98    0,00    0,00    0,00    0,00    0,00    0,00   98,04

11:40:34     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
11:40:35     all   49,50    0,00    1,25    0,00    0,00    0,00    0,00    0,00    0,00   49,25
11:40:35       0   32,00    0,00    4,00    0,00    0,00    0,00    0,00    0,00    0,00   64,00
11:40:35       1   99,00    0,00    1,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00
11:40:35       2   33,66    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00   66,34
11:40:35       3   33,33    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00   66,67

11:40:35     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
11:40:36     all   45,06    0,00    1,01    0,25    0,00    0,00    0,00    0,00    0,00   53,67
11:40:36       0   26,80    0,00    2,06    0,00    0,00    0,00    0,00    0,00    0,00   71,13
11:40:36       1   99,00    0,00    1,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00
11:40:36       2   26,53    0,00    0,00    1,02    0,00    0,00    0,00    0,00    0,00   72,45
11:40:36       3   27,00    0,00    1,00    0,00    0,00    0,00    0,00    0,00    0,00   72,00
</pre>

Read aloud (1GH RHVoice+aplay)
<pre>
11:42:48     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
11:42:49     all    7,91    0,00    0,26    0,00    0,00    0,00    0,00    0,00    0,00   91,84
11:42:49       0    3,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00   97,00
11:42:49       1    0,00    0,00    1,05    0,00    0,00    0,00    0,00    0,00    0,00   98,95
11:42:49       2   27,27    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00   72,73
11:42:49       3    1,02    0,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00   98,98

11:42:49     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
11:42:50     all    5,04    0,00    3,27    0,00    0,00    0,00    0,00    0,00    0,00   91,69
11:42:50       0    3,92    0,00    3,92    0,00    0,00    0,00    0,00    0,00    0,00   92,16
11:42:50       1    3,03    0,00    5,05    0,00    0,00    0,00    0,00    0,00    0,00   91,92
11:42:50       2   11,11    0,00    3,03    0,00    0,00    0,00    0,00    0,00    0,00   85,86
11:42:50       3    2,06    0,00    1,03    0,00    0,00    0,00    0,00    0,00    0,00   96,91

11:42:50     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
11:42:51     all   48,35    0,00    5,57    0,00    0,00    0,00    0,00    0,00    0,00   46,08
11:42:51       0   92,00    0,00    8,00    0,00    0,00    0,00    0,00    0,00    0,00    0,00
11:42:51       1    4,21    0,00    3,16    0,00    0,00    0,00    0,00    0,00    0,00   92,63
11:42:51       2    4,04    0,00    2,02    0,00    0,00    0,00    0,00    0,00    0,00   93,94
11:42:51       3   90,10    0,00    8,91    0,00    0,00    0,00    0,00    0,00    0,00    0,99
</pre>

Raspi switches itself from 600 MHz to 1 GHz when more CPU resources are required.

### Temperature

<pre>
Every 1,0s: vcgencmd measure_temp                           raspberrypi: Fri Jul 24 11:44:33 2026

temp=49.4'C
</pre>

### Useful utilities
```
# CPU temperature
vcgencmd measure_temp

# CPU frequency
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq

# CPU governor
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor

# CPU utilization (all cores)
mpstat -P ALL 1

# CPU throttling
vcgencmd get_throttled

# Camera autosuspend
cat /sys/bus/usb/devices/1-1.2/power/runtime_status

# Enable camera autosuspend
echo auto | sudo tee /sys/bus/usb/devices/1-1.2/power/control

# Wi-Fi OFF
rfkill block wifi

# Bluetooth OFF
systemctl stop bluetooth

# HDMI OFF
vcgencmd display_power 0

# Running USB devices
lsusb

# GPIO interrupts (wait for button)
gpiomon --num-events=1 --edges=rising -c gpiochip0 17 22 27
```