power_watchdog() {
    while rfkill list wifi | grep -q "Soft blocked: no"; do
        sleep 300

        if ! who | grep -q pts; then
            rfkill block wifi
            #systemctl stop bluetooth
            vcgencmd display_power 0
            break
        fi
    done
}