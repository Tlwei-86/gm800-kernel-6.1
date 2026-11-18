#!/bin/sh
# GM800 dual-kernel (4.19 BSP / 6.1 BSP) WiFi loader for AIC8800D80
# Drop-in replacement for /usr/local/sbin/gm800-wifi-load.sh
exec >>/var/log/gm800-wifi.log 2>&1
KVER="$(uname -r)"
echo "=== wifi-load dual start $(date) (kernel=$KVER) ==="

# 0) Hard power-cycle AIC8800 chip via Bluetooth rfkill (clears half-powered state)
for r in /sys/class/rfkill/rfkill*; do
    [ -f "$r/type" ] || continue
    case "$(cat "$r/type")" in bluetooth)
        echo 0 > "$r/state" 2>/dev/null; sleep 2
        echo 1 > "$r/state" 2>/dev/null ;;
    esac
done
sleep 2

case "$KVER" in
    4.19*)
        # Legacy 4.19 BSP path: manual insmod from /root/aic
        K=/root/aic
        H=/sys/bus/platform/drivers/dwmmc_rockchip
        echo fe2c0000.dwmmc > "$H/unbind" 2>/dev/null; sleep 1
        insmod "$K/aic8800_bsp.ko" 2>/dev/null
        insmod "$K/aic8800_btlpm.ko" 2>/dev/null
        echo fe2c0000.dwmmc > "$H/bind" 2>/dev/null; sleep 6
        insmod "$K/aic8800_fdrv.ko" 2>/dev/null; sleep 8
        ;;
    *)
        # Linux 6.1 BSP path: native modprobe from /lib/modules/$KVER + /lib/firmware/aic8800/SDIO/aic8800D80/
        H=/sys/bus/platform/drivers/dwmmc_rockchip
        DEV=fe2c0000.mmc
        [ -e "$H/fe2c0000.dwmmc" ] && DEV=fe2c0000.dwmmc

        # If already brought up automatically by udev, skip rebind dance
        if [ ! -d /sys/class/net/wlan0 ]; then
            echo "$DEV" > "$H/unbind" 2>/dev/null; sleep 1
            modprobe aic8800_bsp 2>/dev/null || true
            modprobe aic8800_btlpm 2>/dev/null || true
            echo "$DEV" > "$H/bind" 2>/dev/null; sleep 4
            modprobe aic8800_fdrv 2>/dev/null || true
            sleep 5
        fi
        ;;
esac

echo "ifaces: $(ls /sys/class/net/ | tr '\n' ' ')"
ls /sys/class/net/ | grep -q wlan0 && echo WIFI_LOAD_OK || echo WIFI_LOAD_FAIL
