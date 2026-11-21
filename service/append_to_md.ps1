param(
    [Parameter(Mandatory = $true)][string]$Log,
    [Parameter(Mandatory = $true)][string]$Tmp,
    [Parameter(Mandatory = $true)][int]$ExitCode
)

$timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

if (Test-Path -LiteralPath $Tmp) {
    # diag.bat переключает консоль на UTF-8 (chcp 65001), читаем как UTF-8
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $output = [System.IO.File]::ReadAllText($Tmp, $utf8).Trim()
} else {
    $output = ''
}
if ([string]::IsNullOrWhiteSpace($output)) { $output = '(диагностика не выдала текста)' }

$result = if ($ExitCode -eq 0) { 'OK' } else { "ОШИБКА (код $ExitCode)" }

$template = @'

## {0}

**Результат:** {1}

```text
{2}
```

---

'@

# Приводим переводы строк к LF и убираем «голые» CR (nslookup их оставляет)
$md = ($template -f $timestamp, $result, $output) -replace "`r`n", "`n" -replace "`r", ""

$utf8Bom = New-Object System.Text.UTF8Encoding($true)
if (Test-Path -LiteralPath $Log) {
    [System.IO.File]::AppendAllText($Log, $md, $utf8Bom)
} else {
    [System.IO.File]::WriteAllText($Log, $md, $utf8Bom)
}

exit $ExitCode
