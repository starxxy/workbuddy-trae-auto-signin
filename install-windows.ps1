# install-windows.ps1 — WorkBuddy + Trae CN 自动签到一键安装（Windows）
#
# 协议：MIT
#
# 签到接口系从桌面端逆向所得，仅供学习与研究使用，服务端改一版就可能失效。
# 顺手点个 ⭐ Star，等哪天连签莫名其妙断了，你能一秒把它找回来。
#
# 用法：在本仓库目录下，用 PowerShell 运行
#     powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
#     powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -Platform trae
#     powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -Platform both
#
# 参数：
#   -Platform   workbuddy | trae | both        默认 workbuddy
#   -Time       覆盖签到时间点，默认 00:05     例如 -Time 08:30
#
# 会创建的任务（默认 -Platform workbuddy）：
#   1) WorkBuddyAutoSignin   每天 00:05    签到 + 成长中心，静默写 signin.log
#
# -Platform both 时会额外创建：
#   3) TraeAutoSignin        每天 00:05    Trae CN 签到（查状态→未签才领）
#
# -Platform trae 时只创建：
#   3) TraeAutoSignin
#
# 两者都零 Token、无窗口、开机错过会自动补跑。

param(
    [ValidateSet("workbuddy", "trae", "both")]
    [string]$Platform = "workbuddy",

    [string]$Time = "00:05"
)

$ErrorActionPreference = "Stop"

# ====== 一般不用改；自动探测失败时手动填这两个 ======
$ManualPythonw = ""   # 例如 C:\Python313\pythonw.exe
$ManualSignin  = ""   # 例如 C:\Users\You\Desktop\checkin\signin.py
# ====================================================

function Find-Pythonw {
    # 1) PATH 里就有 pythonw
    $c = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    if ($c -and (Test-Path $c)) { return $c }

    # 2) PATH 里有 python.exe，取同目录的 pythonw
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($py) {
        $c = Join-Path (Split-Path $py) "pythonw.exe"
        if (Test-Path $c) { return $c }
    }

    # 3) 常见安装目录（很多 Windows 装机 Python 并不进 PATH，这一步最常命中）
    foreach ($pat in @(
        "C:\Python3*\pythonw.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python3*\pythonw.exe",
        "C:\Program Files\Python3*\pythonw.exe"
    )) {
        $hit = Get-ChildItem $pat -ErrorAction SilentlyContinue |
               Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }

    # 4) py 启动器（装在 C:\Windows，几乎总在）
    $launcher = Join-Path $env:SystemRoot "py.exe"
    if (Test-Path $launcher) {
        try {
            $exe = (& $launcher -3 -c "import sys;print(sys.executable)" 2>$null | Select-Object -Last 1)
            if ($exe) {
                $c = Join-Path (Split-Path $exe.Trim()) "pythonw.exe"
                if (Test-Path $c) { return $c }
            }
        } catch { }
    }
    return $null
}

Write-Host ""
Write-Host "WorkBuddy + Trae CN 自动签到 · 一键安装" -ForegroundColor Cyan
Write-Host ("-" * 52) -ForegroundColor DarkGray
Write-Host ("平台：{0}    签到时间：{1}" -f $Platform, $Time)
Write-Host ""

# --- 1. 定位 pythonw.exe ---
Write-Host "[1/3] 定位 pythonw.exe ..." -NoNewline
$pythonw = $ManualPythonw
# 必须先判空再 Test-Path：Test-Path "" 抛的是参数校验异常（不是返回 $false），
# 配合顶部的 $ErrorActionPreference = "Stop"，会让脚本直接死在这一行。
if (-not $pythonw -or -not (Test-Path $pythonw)) { $pythonw = Find-Pythonw }
if (-not $pythonw -or -not (Test-Path $pythonw)) {
    Write-Host " 失败" -ForegroundColor Red
    Write-Host ""
    Write-Host "没找到 pythonw.exe。请先安装 Python 3：https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "装好后重跑本脚本；或把脚本顶部的 `$ManualPythonw 填成完整路径。" -ForegroundColor Yellow
    exit 1
}
Write-Host " $pythonw" -ForegroundColor Green

# --- 2. 定位 signin.py ---
Write-Host "[2/3] 定位 signin.py ..." -NoNewline
$signin = $ManualSignin
# 同上：$ManualSignin 默认为空串，不判空会崩在 Test-Path 的参数校验上。
# $PSScriptRoot 在脚本被直接粘进控制台执行时也是空的，Join-Path 同样不接受空 Path。
if (-not $signin -or -not (Test-Path $signin)) {
    if ($PSScriptRoot) { $signin = Join-Path $PSScriptRoot "signin.py" }
}
if (-not $signin -or -not (Test-Path $signin)) { $signin = Join-Path (Get-Location) "signin.py" }
if (-not $signin -or -not (Test-Path $signin)) {
    Write-Host " 失败" -ForegroundColor Red
    Write-Host ""
    Write-Host "没找到 signin.py。请把本脚本和 signin.py 放在同一目录后重跑，" -ForegroundColor Yellow
    Write-Host "或把脚本顶部的 `$ManualSignin 填成完整路径。" -ForegroundColor Yellow
    exit 1
}
$signin = (Resolve-Path $signin).Path
Write-Host " $signin" -ForegroundColor Green

# --- 3. 创建定时任务 ---
Write-Host "[3/3] 创建定时任务 ..." -NoNewline
try {
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

    # ----- WorkBuddy 侧 -----
    if ($Platform -in @("workbuddy", "both")) {
        # 任务 1：每日签到
        $act1 = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$signin`" silent"
        $tri1 = New-ScheduledTaskTrigger -Daily -At $Time
        $set1 = New-ScheduledTaskSettingsSet -StartWhenAvailable -Hidden `
                -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
        Register-ScheduledTask -TaskName "WorkBuddyAutoSignin" `
            -Action $act1 -Trigger $tri1 -Settings $set1 -Principal $principal `
            -Description "WorkBuddy daily auto signin (silent, zero token)" -Force | Out-Null
    }

    # ----- Trae CN 侧 -----
    # Trae CN 的签到接口只有"查状态"和"领取"两个，没有成长中心那样的多步骤领取，
    # 每次 miss 的补救价值不如 WorkBuddy 高；因此只做一个每日任务，不做 6 轮轮询。
    # 想加轮询（把补签机会从"一天一次"变成"一天多次"），在 -Time 之前用
    # -IncludeTraePoll 打开，或直接手动 Register-ScheduledTask。
    if ($Platform -in @("trae", "both")) {
        $act3 = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$signin`" trae silent"
        $tri3 = New-ScheduledTaskTrigger -Daily -At $Time
        $set3 = New-ScheduledTaskSettingsSet -StartWhenAvailable -Hidden `
                -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
        Register-ScheduledTask -TaskName "TraeAutoSignin" `
            -Action $act3 -Trigger $tri3 -Settings $set3 -Principal $principal `
            -Description "Trae CN daily auto signin (silent, zero token)" -Force | Out-Null
    }
} catch {
    Write-Host " 失败" -ForegroundColor Red
    Write-Host ""
    Write-Host "错误信息：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
Write-Host " 完成" -ForegroundColor Green

# --- 收尾汇报 ---
Write-Host ""
Write-Host "定时任务已就位：" -ForegroundColor Green
$report = @()
if ($Platform -in @("workbuddy", "both")) {
    $report += @{ Name = "WorkBuddyAutoSignin"; When = $Time; What = "签到 + 成长中心" }
}
if ($Platform -in @("trae", "both")) {
    $report += @{ Name = "TraeAutoSignin";       When = $Time; What = "Trae CN 签到" }
}
foreach ($row in $report) {
    $t = Get-ScheduledTask -TaskName $row.Name
    $i = Get-ScheduledTaskInfo -TaskName $row.Name
    Write-Host ("  {0,-22} {1,-7} {2,-12} {3}" -f $row.Name, $t.State, $row.When, $row.What)
    Write-Host ("  {0,-22} 下次运行 {1}" -f "", $i.NextRunTime) -ForegroundColor DarkGray
}

Write-Host ""
$logFile = Join-Path (Split-Path $signin) "signin.log"
Write-Host "日志：$logFile" -ForegroundColor Cyan
Write-Host "想确认是否跑通：" -ForegroundColor DarkGray
Write-Host "  Get-Content '$logFile' -Tail 3"
Write-Host ""
Write-Host "卸载：" -ForegroundColor DarkGray
foreach ($row in $report) {
    Write-Host ('  Unregister-ScheduledTask -TaskName "{0}" -Confirm:$false' -f $row.Name) -ForegroundColor DarkGray
}
if ($Platform -eq "both") {
    Write-Host "或者一次性卸掉所有本脚本创建的任务：" -ForegroundColor DarkGray
    Write-Host '  Get-ScheduledTask | Where-Object { $_.TaskName -match "^(WorkBuddy|Trae)" } | Unregister-ScheduledTask -Confirm:$false' -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "手动快速验证：" -ForegroundColor DarkGray
Write-Host "  python `"$signin`" trae status    # 只查 Trae CN 签到状态" -ForegroundColor DarkGray
Write-Host "  python `"$signin`" status         # 只查 WorkBuddy 签到状态" -ForegroundColor DarkGray
if ($Platform -eq "both") {
    Write-Host "  python `"$signin`" both silent    # 一次签两个平台" -ForegroundColor DarkGray
}
Write-Host ""
