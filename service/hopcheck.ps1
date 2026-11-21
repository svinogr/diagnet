# Проверка маршрута по узлам в стиле WinMTR.
# Строит маршрут до HOP_TARGET и параллельно пингует каждый узел
# HOP_COUNT раз, пишет таблицу потерь/задержек в hop_results.md.
# Параметры берутся из config.bat (HOP_TARGET, HOP_COUNT).
$ErrorActionPreference = 'SilentlyContinue'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $scriptDir 'config.bat'
$target = 'ya.ru'
$count = 1000
if (Test-Path $configPath) {
    foreach ($line in [System.IO.File]::ReadAllLines($configPath, [Text.Encoding]::ASCII)) {
        if ($line -match '^set\s+"([A-Z0-9_]+)=(.*)"') {
            if ($Matches[1] -eq 'HOP_TARGET' -and $Matches[2]) { $target = $Matches[2].Trim() }
            if ($Matches[1] -eq 'HOP_COUNT' -and $Matches[2]) { $count = [int]$Matches[2].Trim() }
        }
    }
}
if ($count -lt 1) { $count = 1000 }

$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$startTime = Get-Date
$logPath = Join-Path $scriptDir 'hop_results.md'
$workDir = Join-Path $env:TEMP ('hopcheck_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $workDir | Out-Null

# --- маршрут ---
$traceOut = & "$env:SystemRoot\System32\tracert.exe" -d -h 30 $target 2>&1 | Out-String
$hops = @()
foreach ($line in ($traceOut -split "`r?`n")) {
    if ($line -match '^\s*(\d+)\s+.*\b(\d{1,3}(?:\.\d{1,3}){3})\s*$') {
        $hops += @(@{ n = [int]$Matches[1]; ip = $Matches[2] })
    }
}

$result = 'OK'
$code = 0
$body = @()
$body += "Target: $target"
$body += "Packets per hop: $count"

if ($hops.Count -eq 0) {
    $result = 'ОШИБКА'
    $code = 1
    $body += 'TRACERT FAIL: маршрут не построен'
    $body += ''
    $body += ($traceOut -split "`r?`n" | Select-Object -First 5)
} else {
    # --- параллельный ping всех узлов ---
    $procs = @()
    foreach ($h in $hops) {
        $f = Join-Path $workDir ('hop_{0:d2}.txt' -f $h.n)
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "$env:SystemRoot\System32\cmd.exe"
        $psi.Arguments = '/c ping -n ' + $count + ' -w 1000 ' + $h.ip + ' > "' + $f + '"'
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $p = New-Object System.Diagnostics.Process
        $p.StartInfo = $psi
        [void]$p.Start()
        $procs += @(@{ p = $p; hop = $h; file = $f })
    }
    foreach ($item in $procs) {
        [void]$item.p.WaitForExit(45 * 60 * 1000)
        if (-not $item.p.HasExited) { $item.p.Kill() }
    }

    # --- разбор результатов (ping пишет в OEM, кодировка 866) ---
    $rows = @()
    $oem = [Text.Encoding]::GetEncoding(866)
    foreach ($item in ($procs | Sort-Object { $_.hop.n })) {
        $text = ''
        if (Test-Path $item.file) { $text = [System.IO.File]::ReadAllText($item.file, $oem) }
        $sent = 0; $recv = 0; $loss = 0.0; $avg = -1
        if ($text -match 'Sent = (\d+), Received = (\d+), Lost = \d+ \(([\d.]+)% loss\)') {
            $sent = [int]$Matches[1]; $recv = [int]$Matches[2]; $loss = [double]$Matches[3]
        } elseif ($text -match 'отправлено = (\d+), получено = (\d+), потеряно = \d+') {
            $sent = [int]$Matches[1]; $recv = [int]$Matches[2]
            if ($text -match '\(([\d.]+)% потерь\)') { $loss = [double]$Matches[1] }
            elseif ($sent -gt 0) { $loss = [math]::Round(100.0 * ($sent - $recv) / $sent, 1) }
        }
        if ($text -match 'Average = (\d+)') { $avg = [int]$Matches[1] }
        elseif ($text -match 'Среднее = (\d+)') { $avg = [int]$Matches[1] }
        $rows += @(@{ n = $item.hop.n; ip = $item.hop.ip; sent = $sent; recv = $recv; loss = $loss; avg = $avg })
    }

    $dur = (Get-Date) - $startTime
    $body += ('Duration: {0}m{1:D2}s' -f [int]$dur.TotalMinutes, $dur.Seconds)
    $body += ''
    $body += '{0,-4} {1,-16} {2,-6} {3,-6} {4,-7} {5}' -f 'Hop', 'IP', 'Sent', 'Recv', 'Loss%', 'Avg'
    $badHops = @()
    foreach ($r in $rows) {
        $avgTxt = if ($r.avg -ge 0) { [string]$r.avg } else { '?' }
        $body += '{0,-4} {1,-16} {2,-6} {3,-6} {4,-7} {5}' -f $r.n, $r.ip, $r.sent, $r.recv, $r.loss, $avgTxt
        if ($r.loss -gt 0) { $badHops += $r }
    }
    if ($badHops.Count -gt 0) {
        $body += ''
        foreach ($r in $badHops) { $body += ('*** HOP {0} {1}: LOSS {2}% ***' -f $r.n, $r.ip, $r.loss) }
    }
    $maxN = ($rows | Measure-Object -Property n -Maximum).Maximum
    $targetRow = $rows | Where-Object { $_.n -eq $maxN }
    if ($targetRow -and $targetRow.loss -gt 0) { $result = 'ОШИБКА'; $code = 1 }
}

# --- запись в лог ---
$bt = [char]96
$fence = "$bt$bt$bt"
$resLine = $result
if ($code -gt 0) { $resLine = "$result (код $code)" }
$md = "`n## $timestamp`n`n**Результат:** $resLine`n`n" + $fence + "text`n" +
      ($body -join "`n") + "`n" + $fence + "`n`n---`n"
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
if (Test-Path $logPath) { [System.IO.File]::AppendAllText($logPath, $md, $utf8Bom) }
else { [System.IO.File]::WriteAllText($logPath, $md, $utf8Bom) }

Remove-Item -Recurse -Force $workDir | Out-Null
exit $code
