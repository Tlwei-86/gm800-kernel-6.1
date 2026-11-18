<#
.SYNOPSIS
    Pack GitHub Actions artifacts (Image + rk3566-gm800.dtb) into a ready-to-flash
    GM800 p8 recovery boot image (syncing BOTH v2 dtb and RSCE rk-kernel.dtb).

.EXAMPLE
    .\tools\pack_local.ps1 -ArtifactsDir .\artifacts -Template ..\release\gm800_linux_boot.img
#>
param(
    [string]$ArtifactsDir = "$PSScriptRoot\..\artifacts",
    [string]$Template     = "$PSScriptRoot\..\..\release\gm800_linux_boot.img",
    [string]$Output       = "$PSScriptRoot\..\out\gm800_linux61_boot.img"
)

$ErrorActionPreference = "Stop"

$KernelPath = Join-Path $ArtifactsDir "Image"
$DtbPath    = Join-Path $ArtifactsDir "rk3566-gm800.dtb"
$Builder    = Join-Path $PSScriptRoot "build_bootimg_61.py"

if (!(Test-Path $KernelPath)) { throw "Missing $KernelPath (download from GitHub Actions Artifacts first)" }
if (!(Test-Path $DtbPath))    { throw "Missing $DtbPath (download from GitHub Actions Artifacts first)" }
if (!(Test-Path $Template))   { throw "Missing template $Template" }

$OutDir = Split-Path -Parent $Output
if (!(Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

# Pure Linux 6.1 cmdline (keeps FIQ console + firmware path + cgroup v2 + ramoops friendly)
$Cmdline = "earlycon=uart8250,mmio32,0xfe660000 console=ttyFIQ0 firmware_class.path=/lib/firmware rootwait rw cgroup_no_v1=all systemd.unified_cgroup_hierarchy=1"

Write-Host "Running RSCE + Boot v2 selftest on template..." -ForegroundColor Cyan
python $Builder selftest $Template

Write-Host "`nPacking GM800 Linux 6.1 boot image (syncing v2 DTB + RSCE rk-kernel.dtb)..." -ForegroundColor Cyan
python $Builder build `
    --template $Template `
    --kernel $KernelPath `
    --dtb $DtbPath `
    --cmdline $Cmdline `
    --drop-dtbo `
    -o $Output

Write-Host "`nDone! Output ready to flash to eMMC p8 (mmcblk2p8): $Output" -ForegroundColor Green
