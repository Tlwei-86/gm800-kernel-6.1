# GM800 (RK3566) Linux 6.1 BSP 云端编译工程

面向国美云 GM800 智能屏（RK3566 + 4G LPDDR4X + 128G eMMC + 8" 800×1280 MIPI-DSI）的 **全功能最优 Linux 6.1 BSP 云端编译仓库**。

**全程零本地编译环境**：所有交叉编译均在 GitHub Actions（Ubuntu 24.04 x86_64 4 核免费 Runner）完成，Windows 本机只需运行 Python 完成 1 秒打包。

---

## 目录结构

```
gm800-kernel-6.1/
├── .github/workflows/build-gm800-kernel.yml  # GitHub Actions 自动交叉编译工作流
├── config/gm800_61.fragment                  # 全功能最优 Kconfig 增量配置
├── dts/rk3566-gm800.dts                      # 逐引脚核对的 GM800 专属 6.1 设备树
├── firmware/
│   ├── regulatory.db(.p7s)                   # 无线电区域数据库
│   ├── aic8800/SDIO/aic8800D80/              # AIC8800D80 WiFi6/BT5 全套固件（15 文件，对齐 CONFIG_AIC_FW_PATH）
│   └── silead/
│       ├── gslx680-gm800.fw                  # 从原厂 4.19 内核精确提取的标准 Silead 触摸固件
│       └── gslx6801_gm800_fw.h               # 同源 C 头文件备份（含 512 项 config_data_id）
└── tools/
    ├── build_bootimg_61.py                   # 同步更新 v2 DTB + RSCE rk-kernel.dtb 的打包器
    ├── pack_local.ps1                        # Windows 一键本地合成 p8 镜像脚本
    ├── gm800-wifi-load-dual.sh               # 4.19 / 6.1 双内核自适应 WiFi 加载脚本
    └── extract_gsl_fw.py                     # 原厂触摸固件提取工具（已运行留档）
```

---

## 核心特性（对比原厂 4.19 与现成 ophub 6.1 的全面升级）

- **精准分立电源树**：还原 GM800 无 PMIC 架构（`/vdd-cpu` PWM0、`/vdd-logic` PWM1、GPU/NPU/DMC `vdd_fixed` 0.95V、全 3.3V `pmu_io_domains`），根治套用 RK809 EVB2 DTB 卡 logo 问题。
- **双处 DTB 同步打包**：`build_bootimg_61.py` 同时更新 Android Boot v2 `dtb` 与 `second`（RSCE）内 `rk-kernel.dtb` 并重算 20 字节 SHA-1，杜绝 U-Boot 2017.09 错读旧 4.19 DTB。
- **零修改兼容现有 U 盘系统**：`aliases` 保持 `mmc2 = &sdhci`、`ethernet1 = &gmac1`，现有 `initramfs`（`/dev/mmcblk2p4`、`/dev/mmcblk2p14`）、`/etc/fstab` 与 LVGL 仪表盘无缝兼容。
- **图形与 AI 全满配**：VOP2 + MIPI-DSI + 内建 PWM 背光 + 开源 **Panfrost** GPU（Ubuntu 24.04 Mesa 3D 加速）+ **RGA2** 2D 加速 + **MPP** 硬解 + **RKNPU 0.9.8**。
- **全外设无损保留**：Silead 原厂精确固件触摸、RTL8201F 百兆有线（告别 `eth0 down` 死机）、AIC8800D80 WiFi6 + BT5、CS4333 双声道喇叭 + 4 麦 PDM、HI-846 800 万摄像头 + RKISP21、电源键/呼叫键/ADC 按键、HYM8563 RTC 唤醒、PWM3 红外遥控。
- **纯净 Linux + 容器 + 免拆机排障**：关闭 SELinux 与 EFI stub；开启完整 Docker（USER/PID/IPC NS、cgroup2、overlayfs、nftables、WireGuard）、`kexec` 及 `pstore/ramoops@110000`。

---

## 使用步骤

### 第 1 步：推送到 GitHub 并触发云端编译（~15 分钟）

```powershell
cd d:\BaiduNetdiskDownload\GM800_pack\gm800-kernel-6.1
git init
git add .
git commit -m "Initial GM800 RK3566 Linux 6.1 BSP cloud build"
git branch -M main
git remote add origin https://github.com/<你的用户名>/gm800-kernel-6.1.git
git push -u origin main
```

1. 打开 GitHub 仓库页面 → **Actions** → 选择 **Build GM800 RK3566 Linux 6.1 BSP Kernel** → 点击 **Run workflow**（默认参数即最优配置 `armbian/linux-rockchip` @ `rk-6.1-rkr5.1`）。
2. 等待约 12~18 分钟编译完成，在构建页面的 **Artifacts** 下载 `gm800-kernel-6.1.*-gm800.zip`。
3. 将压缩包解压到本工程的 `artifacts\` 目录下（确保存在 `artifacts\Image`、`artifacts\rk3566-gm800.dtb`、`artifacts\modules-*.tar.gz`、`artifacts\firmware-gm800.tar.gz`）。

### 第 2 步：Windows 本地 1 秒合成 p8 启动镜像

```powershell
cd d:\BaiduNetdiskDownload\GM800_pack\gm800-kernel-6.1
.\tools\pack_local.ps1
```

成功后会输出 `out\gm800_linux61_boot.img` 以及用于刷写回读校验的 `count`、`main_md5`、`tail_md5`。

### 第 3 步：部署 6.1 模块与固件到 U 盘 Armbian

趁板子仍在运行现役 4.19 Armbian（SSH 连通状态），把模块、固件和双内核 WiFi 脚本上传安装（**不会破坏现役 4.19 启动，随时可回退**）：

```bash
# 1) 从 Windows 上传到板子 /tmp/
scp artifacts/modules-6.1.*-gm800.tar.gz artifacts/firmware-gm800.tar.gz tools/gm800-wifi-load-dual.sh root@<板IP>:/tmp/

# 2) SSH 登录板子安装
ssh root@<板IP> << 'EOF'
tar -xzf /tmp/modules-6.1.*-gm800.tar.gz -C /lib/modules/
tar -xzf /tmp/firmware-gm800.tar.gz -C /lib/firmware/
depmod -a $(ls /lib/modules/ | grep 6.1 | tail -n1) 2>/dev/null || true
install -m 755 /tmp/gm800-wifi-load-dual.sh /usr/local/sbin/gm800-wifi-load.sh
sync
ls -la /lib/modules/ /lib/firmware/silead/ /lib/firmware/aic8800/SDIO/aic8800D80/
EOF
```

### 第 4 步：安全刷入 p8（严格遵守双段 MD5 回读纪律）

> **安全前提**：确认已拆除安卓 `/vendor/bin/install-recovery.sh`，且本地留存了原 4.19 的 `release/gm800_linux_boot.img` 备份；绝不触碰 eMMC 前 16 MiB。

```bash
# 1) 上传新镜像
scp out/gm800_linux61_boot.img root@<板IP>:/tmp/b61.img

# 2) 写入 eMMC p8（recovery 分区）
ssh root@<板IP> "dd if=/tmp/b61.img of=/dev/mmcblk2p8 bs=4096 conv=fsync"

# 3) 双段回读校验（把 COUNT 和 REM 替换为 pack_local.ps1 输出的值，核对 MD5 完全一致！）
ssh root@<板IP> "dd if=/dev/mmcblk2p8 bs=4096 count=<COUNT> | md5sum"
ssh root@<板IP> "dd if=/dev/mmcblk2p8 bs=4096 skip=<COUNT> count=1 | head -c <REM> | md5sum"

# 4) 校验一致后重启进入 Linux 6.1
ssh root@<板IP> "reboot"
```

---

## 排障与回退指南

### 免拆机读取启动崩溃日志（`ramoops`）
如果首次刷入 6.1 出现卡 logo 或未上线，**无需拆机焊接串口**：
1. 拔掉 U 盘重启进入原厂安卓（或刷回原 4.19 `gm800_linux_boot.img` 进入 4.19 Armbian）。
2. 查看保留内存日志：
   ```bash
   # 在安卓或 4.19 Linux 下检查 pstore / ramoops
   ls -la /sys/fs/pstore/
   cat /sys/fs/pstore/console-ramoops-0 | tail -n 120
   ```
3. 根据最后几行内核日志即可精准定位问题，修改 `dts/rk3566-gm800.dts` 推送重编即可。

### 秒级回退原 4.19 系统
- 在安卓侧 `adb root` 后把 `release/gm800_linux_boot.img` 写回 `/dev/block/mmcblk2p8` 即可 100% 恢复原 4.19 系统；
- 极端情况随时可用复位键进 Maskrom + RKDevTool 恢复（`misc=0x8000`, `recovery=0x20800`）。

---

## 上板后 12 项全功能验收清单

```bash
uname -a                                              # 1. 确认 6.1.*-gm800 内核
cat /sys/class/backlight/backlight1/bl_power          # 2. 屏幕背光与 DRM/LVGL 正常
ls -la /dev/dri/ && dmesg | grep -iE "panfrost|rknpu" # 3. Panfrost GPU + RKNPU 0.9.8
evtest /dev/input/by-path/*                           # 4. Silead 触摸 + 电源键/ADC按键
ip link set end1 down && sleep 2 && ip link set end1 up # 5. 有线网 down/up 不再死机！
nmcli dev status && hciconfig -a                      # 6. WiFi6 wlan0 + 蓝牙5 hci0
speaker-test -c 2 -t sine -f 440 -l 2                 # 7. CS4333 双声道外放
arecord -D hw:0,0 -f S16_LE -r 16000 -c 4 -d 3 /tmp/t.wav # 8. 4通道 PDM 麦克风录音
v4l2-ctl --list-devices                               # 9. HI-846 摄像头 + RKISP21
cat /sys/class/rtc/rtc0/date /sys/class/rtc/rtc0/time # 10. HYM8563 RTC 走时
modprobe wireguard && nft list tables                 # 11. WireGuard + nftables
unshare --user --pid --fork true && echo DOCKER_OK    # 12. 完整容器 namespace
```
