import gzip
import os

rd_path = r'd:\BaiduNetdiskDownload\GM800_pack\rescue\ramdisk_419.cpio.gz'
out_path = r'd:\BaiduNetdiskDownload\GM800_pack\gm800-kernel-6.1\out\initrd_emmc.cpio.gz'

with gzip.open(rd_path, 'rb') as f:
    rd = f.read()

pos = 0
entries = []
while pos < len(rd):
    assert rd[pos:pos+6] == b'070701'
    hdr = rd[pos:pos+110]
    namesize = int(hdr[94:102], 16)
    filesize = int(hdr[54:62], 16)
    name_bytes = rd[pos+110:pos+110+namesize]
    name = name_bytes[:-1].decode('latin1')
    data_start = (pos + 110 + namesize + 3) & ~3
    data = rd[data_start:data_start+filesize]
    entries.append((hdr, name_bytes, data, name))
    if name == 'TRAILER!!!':
        break
    pos = (data_start + filesize + 3) & ~3

new_init = '''#!/bin/busybox sh
BB=/bin/busybox
export PATH=/bin:/sbin:/usr/bin:/usr/sbin

$BB mkdir -p /log /proc /sys /dev /config /newroot /screens /tmp
LOG=/log/boot.log
exec >>"$LOG" 2>&1

echo "=== GM800 Linux 6.1 Dynamic Step-Shell Initramfs Start ==="
$BB uname -a

# --- [1] Pseudo filesystems ---
$BB mount -t proc proc /proc 2>/dev/null
$BB mount -t sysfs sysfs /sys 2>/dev/null
$BB mount -t devtmpfs devtmpfs /dev 2>/dev/null
$BB mkdir -p /dev/pts
$BB mount -t devpts devpts /dev/pts 2>/dev/null
$BB mkdir -p /sys/kernel/debug
$BB mount -t debugfs none /sys/kernel/debug 2>/dev/null
$BB --install -s /bin 2>/dev/null
echo "[1] pseudo-fs mounted"

# --- [2] Clear BCB FIRST (Rollback Safety Guarantee) ---
MISC=""
for d in /sys/class/block/*/; do
    n=${d%/}; n=${n##*/}
    case "$n" in loop*|ram*|dm-*) continue ;; esac
    if $BB grep -q "PARTNAME=misc" "${d}uevent" 2>/dev/null; then
        MISC=/dev/$n; break
    fi
done
if [ -b "$MISC" ]; then
    $BB dd if=/dev/zero of="$MISC" bs=512 count=8 2>/dev/null \
        && echo "[2] BCB cleared ($MISC) - power cycle will return to Android" \
        || echo "[2] WARNING: BCB clear FAILED on $MISC"
else
    echo "[2] WARNING: misc partition not found"
fi

# --- [3] Backlight + early screen ---
for bm in /sys/class/backlight/*/max_brightness; do
    [ -f "$bm" ] && $BB cat "$bm" > "${bm%/*}/brightness" 2>/dev/null
done
screen() {
    if [ -e /dev/fb0 ]; then
        $BB zcat "/screens/$1.raw.gz" > /dev/fb0 2>/dev/null
        echo 0 > /sys/class/graphics/fb0/blank 2>/dev/null
    fi
}
screen early 2>/dev/null
echo "[3] backlight up, screen early drawn"

# --- [4] USB CDC-ACM console ---
echo peripheral > /sys/devices/platform/fe8a0000.usb2-phy/otg_mode 2>/dev/null
$BB mount -t configfs none /config 2>/dev/null
if [ -d /config/usb_gadget ]; then
    $BB mkdir -p /config/usb_gadget/gm800/configs/c.1 \
                 /config/usb_gadget/gm800/functions/acm.usb0
    echo 0x1d6b > /config/usb_gadget/gm800/idVendor 2>/dev/null
    echo 0x0104 > /config/usb_gadget/gm800/idProduct 2>/dev/null
    $BB ln -sf /config/usb_gadget/gm800/functions/acm.usb0 \
               /config/usb_gadget/gm800/configs/c.1/ 2>/dev/null
    echo fcc00000.dwc3 > /config/usb_gadget/gm800/UDC 2>/dev/null
    $BB sleep 1
    if [ -e /dev/ttyGS0 ]; then
        ($BB tail -f -n +1 "$LOG" > /dev/ttyGS0 2>/dev/null) &
        (exec $BB sh </dev/ttyGS0 >/dev/ttyGS0 2>&1) &
        echo "[4] USB serial live on ttyGS0"
    fi
fi

# --- [5] Locate & mount userdata with RETRY LOOP (up to 15s) ---
DATA=""
for try in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    for d in /sys/class/block/*/; do
        n=${d%/}; n=${n##*/}
        case "$n" in loop*|ram*|dm-*) continue ;; esac
        if $BB grep -q "PARTNAME=userdata" "${d}uevent" 2>/dev/null; then
            DATA=/dev/$n; break 2
        fi
    done
    $BB sleep 1
done
echo "[5] userdata device = ${DATA:-NOT FOUND}"

if [ -n "$DATA" ]; then
    # Try mounting RW (native support when kernel has CONFIG_QUOTA=y)
    if $BB mount -t ext4 -o rw,noatime,errors=continue "$DATA" /newroot 2>/dev/null; then
        echo "[5] userdata mounted RW: $(grep /newroot /proc/mounts)"
    else
        $BB mount -t ext4 -o ro "$DATA" /newroot 2>/dev/null
        echo "[5] userdata mounted RO fallback: $(grep /newroot /proc/mounts)"
    fi
fi

# --- [6] Locate rootfs + setup 100% Read-Write OverlayFS ---
ROOTFS_IMG=""
for candidate in /newroot/armbian_rootfs.img /newroot/data/armbian_rootfs.img; do
    if [ -f "$candidate" ]; then
        ROOTFS_IMG="$candidate"
        break
    fi
done

ARMROOT=""
if [ -n "$ROOTFS_IMG" ]; then
    echo "[6] Found Armbian rootfs image: $ROOTFS_IMG"
    LOOPDEV=$($BB losetup -f 2>/dev/null || echo "/dev/loop0")
    [ ! -b "$LOOPDEV" ] && $BB mknod "$LOOPDEV" b 7 0 2>/dev/null
    $BB losetup "$LOOPDEV" "$ROOTFS_IMG"
    $BB mkdir -p /armroot
    if $BB mount -t ext4 -o ro "$LOOPDEV" /armroot 2>/dev/null; then
        echo "[6] Armbian loop rootfs mounted on $LOOPDEV (lowerdir)"
        $BB mkdir -p /newroot_rw
        
        OVERLAY_OK=0
        # 1. Look for loop-backed eMMC persistent overlay image (ext4 without casefold)
        OVERLAY_IMG=""
        for img in /newroot/armbian_overlay.img /newroot/data/armbian_overlay.img; do
            if [ -f "$img" ]; then
                OVERLAY_IMG="$img"
                break
            fi
        done
        
        if [ -n "$OVERLAY_IMG" ] && [ -w /newroot ]; then
            echo "[6] Found eMMC overlay image: $OVERLAY_IMG"
            LOOP_OVL=$($BB losetup -f 2>/dev/null || echo "/dev/loop1")
            [ ! -b "$LOOP_OVL" ] && $BB mknod "$LOOP_OVL" b 7 1 2>/dev/null
            $BB losetup "$LOOP_OVL" "$OVERLAY_IMG"
            $BB mkdir -p /emmc_overlay
            if $BB mount -t ext4 -o rw,noatime "$LOOP_OVL" /emmc_overlay 2>/dev/null; then
                $BB mkdir -p /emmc_overlay/upper /emmc_overlay/work 2>/dev/null
                if $BB mount -t overlay overlay -o lowerdir=/armroot,upperdir=/emmc_overlay/upper,workdir=/emmc_overlay/work /newroot_rw 2>/dev/null; then
                    echo "[6] 🎉 eMMC Persistent OverlayFS mounted successfully! (30GB Storage on eMMC)"
                    OVERLAY_OK=1
                fi
            fi
        fi

        # 2. Fallback to RAM tmpfs if eMMC overlay was not mounted
        if [ $OVERLAY_OK -eq 0 ]; then
            echo "[6] Fallback: using RAM tmpfs OverlayFS (2560M)"
            $BB mkdir -p /overlay
            $BB mount -t tmpfs -o size=2560M tmpfs /overlay 2>/dev/null
            $BB mkdir -p /overlay/upper /overlay/work
            if $BB mount -t overlay overlay -o lowerdir=/armroot,upperdir=/overlay/upper,workdir=/overlay/work /newroot_rw 2>/dev/null; then
                OVERLAY_OK=1
            fi
        fi

        if [ $OVERLAY_OK -eq 1 ]; then
            ARMROOT=/newroot_rw
            # Bind mount pseudo filesystems into /newroot_rw for immediate chroot support
            $BB mount -t proc proc /newroot_rw/proc 2>/dev/null
            $BB mount -t sysfs sysfs /newroot_rw/sys 2>/dev/null
            $BB mount --bind /dev /newroot_rw/dev 2>/dev/null
            $BB mount -t devpts devpts /newroot_rw/dev/pts 2>/dev/null
            $BB mkdir -p /newroot_rw/sys/kernel/debug /newroot_rw/tmp
            $BB mount --bind /sys/kernel/debug /newroot_rw/sys/kernel/debug 2>/dev/null

            # [6.1] 200M RAM Accelerators: tmpfs on /tmp, /dev/shm, /run
            $BB mkdir -p /newroot_rw/tmp /newroot_rw/run /newroot_rw/dev/shm
            $BB mount -t tmpfs -o size=200M,mode=1777 tmpfs /newroot_rw/tmp 2>/dev/null
            $BB mount -t tmpfs -o size=200M,mode=1777 tmpfs /newroot_rw/dev/shm 2>/dev/null
            $BB mount -t tmpfs -o size=50M,mode=0755 tmpfs /newroot_rw/run 2>/dev/null
            echo "[6.1] 200M RAM Accelerators mounted (/tmp, /dev/shm, /run)"

            # [6.2] Android User Data Sharing (/sdcard)
            if [ -d /newroot/media/0 ]; then
                $BB mkdir -p /newroot_rw/mnt/sdcard /newroot_rw/sdcard
                $BB mount --bind /newroot/media/0 /newroot_rw/mnt/sdcard 2>/dev/null
                $BB mount --bind /newroot/media/0 /newroot_rw/sdcard 2>/dev/null
                echo "[6.2] Android shared storage mounted at /mnt/sdcard and /sdcard"
            fi
            $BB mkdir -p /newroot_rw/mnt/userdata
            $BB mount --bind /newroot /newroot_rw/mnt/userdata 2>/dev/null

            # Establish kernel rootfs firmware and module links
            $BB mkdir -p /lib /vendor/etc
            $BB ln -sf /newroot_rw/lib/firmware /lib/firmware
            $BB ln -sf /newroot_rw/lib/modules /lib/modules
            $BB ln -sf /newroot_rw/vendor/etc/firmware /vendor/etc/firmware
            echo -n "/newroot_rw/lib/firmware" > /sys/module/firmware_class/parameters/path 2>/dev/null
        else
            echo "[6] OverlayFS mount failed, direct /armroot"
            ARMROOT=/armroot
            $BB mkdir -p /lib /vendor/etc
            $BB ln -sf /armroot/lib/firmware /lib/firmware
            $BB ln -sf /armroot/lib/modules /lib/modules
            $BB ln -sf /armroot/vendor/etc/firmware /vendor/etc/firmware
            echo -n "/armroot/lib/firmware" > /sys/module/firmware_class/parameters/path 2>/dev/null
        fi
    else
        echo "[6] Error: failed to mount $LOOPDEV to /armroot"
    fi
fi

# --- [7] Dynamic IP Configuration & Universal Network Bringup ---
echo "=== [7] Configuring eth0 network ==="

TARGET_IP="192.168.10.159"
TARGET_MASK="255.255.255.0"
TARGET_GW="192.168.10.1"

for cfg in /newroot/gm800_net.conf /newroot/data/gm800_net.conf /armroot/etc/gm800_net.conf; do
    if [ -f "$cfg" ]; then
        echo "[7] Loading network config from $cfg"
        . "$cfg"
        break
    fi
done

CMD_IP=$($BB sed -n 's/.*gm800_ip=\([^ ]*\).*/\\1/p' /proc/cmdline 2>/dev/null)
[ -n "$CMD_IP" ] && TARGET_IP="$CMD_IP"
CMD_GW=$($BB sed -n 's/.*gm800_gw=\([^ ]*\).*/\\1/p' /proc/cmdline 2>/dev/null)
[ -n "$CMD_GW" ] && TARGET_GW="$CMD_GW"
CMD_MASK=$($BB sed -n 's/.*gm800_mask=\([^ ]*\).*/\\1/p' /proc/cmdline 2>/dev/null)
[ -n "$CMD_MASK" ] && TARGET_MASK="$CMD_MASK"

BCAST=$(echo "$TARGET_IP" | $BB sed 's/\\.[0-9]*$/.255/')
echo "[7] Active Network: IP=$TARGET_IP MASK=$TARGET_MASK GW=$TARGET_GW BCAST=$BCAST"

$BB ifconfig lo 127.0.0.1 up 2>/dev/null
$BB ifconfig eth0 "$TARGET_IP" netmask "$TARGET_MASK" broadcast "$BCAST" up 2>/dev/null
$BB ifconfig eth0 promisc 2>/dev/null
$BB route add default gw "$TARGET_GW" 2>/dev/null

echo "=== [7] Waiting up to 15s for Ethernet carrier ==="
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if [ "$($BB cat /sys/class/net/eth0/carrier 2>/dev/null)" = "1" ]; then
        echo "=== [7] eth0 carrier is UP on try $i! ==="
        break
    fi
    $BB sleep 1
done

# --- [7.1] Hardware IOCTL Injection & RMII Dynamic Probing Engine ---
echo "=== [7.1] Starting Hardware IOCTL Injection & RMII Auto-Tuning ==="
if [ -x /newroot_rw/usr/bin/python3 ]; then
    $BB cat << 'PYEOF' > /newroot_rw/tmp/tune_eth.py
import socket, fcntl, struct, subprocess, time, os

SIOCGMIIPHY = 0x8947
SIOCGMIIREG = 0x8948
SIOCSMIIREG = 0x8949

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
data = struct.pack("16sHHHH16x", b"eth0", 0, 0, 0, 0)
try:
    res = fcntl.ioctl(s.fileno(), SIOCGMIIPHY, data)
    _, phy_id, _, _, _ = struct.unpack("16sHHHH16x", res)
except Exception:
    phy_id = 0

def read_mii(reg):
    d = struct.pack("16sHHHH16x", b"eth0", phy_id, reg, 0, 0)
    r = fcntl.ioctl(s.fileno(), SIOCGMIIREG, d)
    return struct.unpack("16sHHHH16x", r)[4]

def write_mii(reg, val):
    d = struct.pack("16sHHHH16x", b"eth0", phy_id, reg, val, 0)
    fcntl.ioctl(s.fileno(), SIOCSMIIREG, d)

print(f"[ioctl] PHY detected at addr {phy_id}: ID=0x{read_mii(2):04x} 0x{read_mii(3):04x}")

def get_rx():
    try:
        with open("/sys/class/net/eth0/statistics/rx_packets") as f:
            return int(f.read().strip())
    except Exception:
        return 0

target_gw = "192.168.10.1"

def probe_rx():
    rx0 = get_rx()
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        u.sendto(b"GM800_RX_PROBE", (target_gw, 12345))
        u.sendto(b"GM800_RX_PROBE", ("255.255.255.255", 12345))
        u.close()
    except Exception:
        pass
    if os.path.exists("/usr/bin/arping"):
        subprocess.run(["/usr/bin/arping", "-c", "1", "-w", "1", "-I", "eth0", target_gw],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.05)
    return get_rx() > rx0

print("[diag] Page 0 Reg 0..5:")
for r in range(6):
    print(f"  Reg {r} = 0x{read_mii(r):04x}")

write_mii(31, 7)
p7_16_init = read_mii(16)
write_mii(31, 0)
print(f"[diag] Initial Page 7 Reg 16 = 0x{p7_16_init:04x}")

locked = False
if probe_rx():
    print(f"🎉 [auto] RX already working on initial config! (rx={get_rx()})")
    locked = True

if not locked:
    print("[auto] Testing Android 4.19 baseline (Page 7 Reg 16 = 0x0ffa)...")
    write_mii(31, 7)
    write_mii(16, 0x0ffa)
    rb = read_mii(16)
    write_mii(31, 0)
    print(f"[auto] Written 0x0ffa, readback: 0x{rb:04x}")
    if probe_rx():
        print(f"🎉🎉🎉 [auto] LOCKED with 0x0ffa! (rx={get_rx()})")
        locked = True

if not locked:
    print("[auto] Sweeping RTL8201F RX offsets (0..15) with base 0x0f0a...")
    for rx_off in range(16):
        val = 0x0f0a | (rx_off << 4)
        write_mii(31, 7)
        write_mii(16, val)
        write_mii(31, 0)
        if probe_rx():
            print(f"🎉🎉🎉 [auto] LOCKED RX offset {rx_off}: 0x{val:04x}! (rx={get_rx()})")
            locked = True
            break

if not locked:
    print("[auto] Sweeping RTL8201F RX offsets (0..15) with ext-clk bit 12 (0x1f0a)...")
    for rx_off in range(16):
        val = 0x1f0a | (rx_off << 4)
        write_mii(31, 7)
        write_mii(16, val)
        write_mii(31, 0)
        if probe_rx():
            print(f"🎉🎉🎉 [auto] LOCKED ext-clk RX offset {rx_off}: 0x{val:04x}! (rx={get_rx()})")
            locked = True
            break

if not locked:
    print("[auto] Testing BMCR auto-negotiation restart with 0x0ffa...")
    write_mii(31, 7)
    write_mii(16, 0x0ffa)
    write_mii(31, 0)
    write_mii(0, 0x1200)
    time.sleep(1.5)
    if probe_rx():
        print(f"🎉🎉🎉 [auto] LOCKED after auto-neg restart! (rx={get_rx()})")
        locked = True

if not locked:
    write_mii(31, 7)
    write_mii(16, 0x0ffa)
    write_mii(31, 0)
    print(f"[auto] Tuning completed without lock, restored 0x0ffa. Final rx={get_rx()}")

try:
    print("=== MAC REGISTERS (ethtool -d) ===")
    res_d = subprocess.run(["/usr/sbin/ethtool", "-d", "eth0"],
                           capture_output=True, text=True, timeout=5)
    for line in res_d.stdout.splitlines()[:15]:
        print("  " + line)
except Exception as e:
    print(f"ethtool -d failed: {e}")

try:
    print("=== MAC STATISTICS (ethtool -S) ===")
    res_s = subprocess.run(["/usr/sbin/ethtool", "-S", "eth0"],
                           capture_output=True, text=True, timeout=5)
    for line in res_s.stdout.splitlines():
        if any(k in line for k in ["rx_", "error", "filter", "crc", "drop"]):
            print("  " + line)
except Exception as e:
    print(f"ethtool -S failed: {e}")
PYEOF
    $BB chroot /newroot_rw /usr/bin/python3 /tmp/tune_eth.py 2>&1
fi

if [ -f /sys/kernel/debug/clk/clk_summary ]; then
    echo "=== CLOCK SUMMARY (GMAC) ==="
    $BB grep -E "gmac|clk_mac|ethernet" /sys/kernel/debug/clk/clk_summary 2>/dev/null
fi

echo "=== [7.1] Auto-tuning finished! Final RX=$($BB cat /sys/class/net/eth0/statistics/rx_packets 2>/dev/null) ==="

# Gratuitous Broadcast ARP announcement to subnet
$BB arping -b -U -c 3 -I eth0 "$TARGET_IP" 2>/dev/null
$BB arping -b -A -c 3 -I eth0 "$TARGET_IP" 2>/dev/null
$BB arping -c 3 -I eth0 "$TARGET_GW" 2>/dev/null
$BB ping -c 2 "$BCAST" 2>/dev/null
$BB ping -c 2 "$TARGET_GW" 2>/dev/null

screen ok 2>/dev/null

# --- [8] Start SSH Server & Persistent Root Shell ---
echo "=== [8] Starting SSH & Native Root Shell ==="
if [ -x /newroot_rw/usr/sbin/sshd ]; then
    $BB mkdir -p /newroot_rw/run/sshd /newroot_rw/var/run/sshd /newroot_rw/root/.ssh
    echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGhyCd+Ee3jEoVu+15bFELo3SX8BH5oWz+Z462ENHDM9 tlwei_rd@DESKTOP-OOA1LIM" > /newroot_rw/root/.ssh/authorized_keys
    $BB chmod 700 /newroot_rw/root/.ssh
    $BB chmod 600 /newroot_rw/root/.ssh/authorized_keys
    $BB sed -i "s/#PermitRootLogin.*/PermitRootLogin yes/" /newroot_rw/etc/ssh/sshd_config 2>/dev/null
    $BB chroot /newroot_rw /usr/sbin/sshd 2>/dev/null \
        && echo "[8] OpenSSH server active on port 22 (SSH root access enabled!)"
fi
$BB nc -lk -p 2323 -e /bin/sh &
echo "=== [8] nc -lk active on $TARGET_IP:2323 ==="

# --- [8.1] Auto-load Touchscreen & GPU drivers ---
echo "=== [8.1] Loading Hardware Drivers (Touchscreen Silead, GPU Panfrost) ==="
$BB chroot /newroot_rw /sbin/modprobe silead 2>/dev/null || $BB chroot /newroot_rw /sbin/insmod /lib/modules/6.1.115-gm800/kernel/drivers/input/touchscreen/silead.ko 2>/dev/null
$BB chroot /newroot_rw /sbin/modprobe panfrost 2>/dev/null

# --- [8.2] Auto-start Tailscale if present ---
if [ -x /newroot_rw/usr/sbin/tailscaled ]; then
    $BB mkdir -p /newroot_rw/var/run/tailscale /newroot_rw/var/lib/tailscale /newroot_rw/dev/net
    [ ! -e /newroot_rw/dev/net/tun ] && $BB mknod /newroot_rw/dev/net/tun c 10 200 2>/dev/null
    $BB chroot /newroot_rw /usr/sbin/tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock --port=41641 >/newroot_rw/var/log/tailscaled.log 2>&1 &
    echo "[8.2] Tailscale daemon started in background"
fi

# --- [9] Watchdog with Live Periodic Diagnostics to misc@128KB ---
echo "=== [9] GM800 Linux 6.1 Step Shell READY! (touch /stay or /newroot/stay to stay indefinitely) ==="
REMAINING=300
while [ $REMAINING -gt 0 ]; do
    if [ -f /tmp/stay ] || [ -f /stay ] || [ -f /newroot/stay ] || [ -f /newroot/data/stay ] || [ -f /newroot_rw/tmp/stay ] || [ -f /newroot_rw/stay ]; then
        echo "[stay] Watchdog disabled, staying indefinitely in Linux 6.1"
        $BB sleep 30
        continue
    fi
    
    # Save periodic snapshot to misc@128KB
    if [ -n "$MISC" ] && [ -b "$MISC" ]; then
        (
            echo "=== GM800 PERIODIC SNAPSHOT (rem=${REMAINING}s) ==="
            $BB date 2>&1
            $BB uptime 2>&1
            echo "=== NETWORK ==="
            $BB ifconfig -a 2>&1
            echo "=== ACTIVE LISTENERS ==="
            $BB netstat -tlpn 2>&1
            echo "=== BOOT LOG TAIL ==="
            $BB tail -n 45 "$LOG" 2>&1
            echo "=== END OF SNAPSHOT ==="
        ) | $BB dd of="$MISC" bs=1024 seek=128 count=128 conv=notrunc 2>/dev/null
        $BB sync
    fi
    
    $BB sleep 10
    REMAINING=$((REMAINING - 10))
done

echo "Watchdog expired. Applying PWM voltage stabilization and triggering kernel reset..."
for r in /sys/class/regulator/*; do
    [ -f "$r/name" ] && $BB grep -qE "vdd_cpu|vdd_logic" "$r/name" && echo 1050000 > "$r/microvolts" 2>/dev/null
done
for p in /sys/class/pwm/pwmchip*/pwm*; do
    [ -f "$p/duty_cycle" ] && echo 2500 > "$p/duty_cycle" 2>/dev/null
done
$BB sync
echo 1 > /proc/sys/kernel/sysrq 2>/dev/null
echo b > /proc/sysrq-trigger 2>/dev/null
$BB sleep 2
echo 1 > /dev/watchdog 2>/dev/null
$BB reboot -f
exit 0
'''

new_init_bytes = new_init.encode('utf-8')

out_cpio = bytearray()
for hdr, name_bytes, data, name in entries:
    if name == 'init':
        data = new_init_bytes
        new_filesize = len(data)
        new_hdr = hdr[:54] + f'{new_filesize:08x}'.encode('ascii') + hdr[62:]
    else:
        new_hdr = hdr
    
    out_cpio.extend(new_hdr)
    out_cpio.extend(name_bytes)
    while len(out_cpio) % 4 != 0:
        out_cpio.append(0)
    out_cpio.extend(data)
    while len(out_cpio) % 4 != 0:
        out_cpio.append(0)

while len(out_cpio) % 512 != 0:
    out_cpio.append(0)

with gzip.open(out_path, 'wb', compresslevel=9) as f:
    f.write(out_cpio)

print(f'Successfully built {out_path} (size={len(out_cpio)} uncompressed, {os.path.getsize(out_path)} compressed)')
