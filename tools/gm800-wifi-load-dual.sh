#!/bin/sh
# GM800 dual-kernel (4.19 BSP / 6.1 BSP) WiFi loader for AIC8800D80
# Preserves proven v3 sequence (unbind -> bsp+btlpm -> BT rfkill toggle -> bind -> fdrv)
exec >>/var/log/gm800-wifi.log 2>&1
KVER="$(uname -r)"
echo "=== wifi-load dual start $(date) (kernel=$KVER) ==="

H=/sys/bus/platform/drivers/dwmmc_rockchip

rfkill_bt_cycle() {
    for r in /sys/class/rfkill/rfkill*; do
        [ "$(cat "$r/type" 2>/dev/null)" = bluetooth ] || continue
        echo 0 > "$r/state" 2>/dev/null
    done
    sleep 2
    for r in /sys/class/rfkill/rfkill*; do
        [ "$(cat "$r/type" 2>/dev/null)" = bluetooth ] || continue
        echo 1 > "$r/state" 2>/dev/null
    done
    sleep 1
}

case "$KVER" in
    4.19*)
        K=/root/aic
        SDIO=fe2c0000.dwmmc
        rmmod aic8800_fdrv 2>/dev/null
        rmmod aic8800_btlpm 2>/dev/null
        rmmod aic8800_bsp 2>/dev/null
        [ -d "$H/$SDIO" ] && echo "$SDIO" > "$H/unbind" 2>/dev/null
        sleep 1
        insmod "$K/aic8800_bsp.ko" || { echo "bsp fail"; exit 1; }
        insmod "$K/aic8800_btlpm.ko" || { echo "btlpm fail"; exit 1; }
        rfkill_bt_cycle
        echo "$SDIO" > "$H/bind" 2>/dev/null
        sleep 3
        insmod "$K/aic8800_fdrv.ko" && echo "fdrv ok" || echo "fdrv fail rc=$?"
        sleep 8
        ;;
    *)
        # Linux 6.1 BSP path: native modprobe + /lib/firmware/aic8800/SDIO/aic8800D80/
        if [ -d /sys/class/net/wlan0 ]; then
            echo "wlan0 already present, skipping rebind"
        else
            SDIO=""
            for cand in fe2c0000.mmc fe2c0000.dwmmc; do
                if [ -e "/sys/bus/platform/devices/$cand" ]; then
                    SDIO="$cand"
                    break
                fi
            done
            echo "detected SDIO dev: ${SDIO:-none}"

            rmmod aic8800_fdrv 2>/dev/null || true
            rmmod aic8800_btlpm 2>/dev/null || true
            rmmod aic8800_bsp 2>/dev/null || true

            if [ -n "$SDIO" ] && [ -d "$H/$SDIO" ]; then
                echo "$SDIO" > "$H/unbind" 2>/dev/null || true
            fi
            sleep 1
            modprobe aic8800_bsp && echo "bsp ok" || echo "bsp fail rc=$?"
            modprobe aic8800_btlpm && echo "btlpm ok" || echo "btlpm fail rc=$?"
            rfkill_bt_cycle
            if [ -n "$SDIO" ]; then
                echo "$SDIO" > "$H/bind" 2>/dev/null || true
            fi
            sleep 4
            modprobe aic8800_fdrv && echo "fdrv ok" || echo "fdrv fail rc=$?"
            sleep 8
        fi
        ;;
esac

echo "ifaces: $(ls /sys/class/net/ | tr '\n' ' ')"
if ls /sys/class/net/ | grep -q wlan0; then echo WIFI_LOAD_OK; else echo WIFI_LOAD_FAIL; fi
