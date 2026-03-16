$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$EnvPath = Join-Path $ProjectRoot "env"
$LogDir = Join-Path $ProjectRoot ".cloudflared"
$PidFile = if ($env:PID_FILE) { $env:PID_FILE } else { Join-Path $LogDir "tunnel.pid" }
$LogFile = if ($env:LOG_FILE) { $env:LOG_FILE } else { Join-Path $LogDir "tunnel.log" }
$cloudflared = if ($env:CLOUDFLARED_BIN) { $env:CLOUDFLARED_BIN } else { "cloudflared" }

function Get-EnvValueFromFile {
    param(
        [string]$Path,
        [string]$Key
    )
    if (-not (Test-Path $Path)) {
        return $null
    }
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $parts = $trimmed.Split("=", 2)
        if ($parts.Length -lt 2) { continue }
        if ($parts[0].Trim() -ne $Key) { continue }
        return $parts[1].Trim().Trim('"').Trim("'")
    }
    return $null
}

if (-not $env:TUNNEL_TOKEN) { $env:TUNNEL_TOKEN = Get-EnvValueFromFile -Path $EnvPath -Key "TUNNEL_TOKEN" }

function Test-TunnelRunning {
    if (-not (Test-Path $PidFile)) { return $false }
    $pid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if (-not $pid) { return $false }
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    return $null -ne $proc
}

function Start-Tunnel {
    if (Test-TunnelRunning) {
        Write-Host "Tunnel already running (pid $(Get-Content $PidFile))."
        return
    }
    if (-not $env:TUNNEL_TOKEN) {
        throw "TUNNEL_TOKEN is required."
    }
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $proc = Start-Process -FilePath $cloudflared -ArgumentList "tunnel", "run", "--token", $env:TUNNEL_TOKEN -RedirectStandardOutput $LogFile -RedirectStandardError $LogFile -PassThru -WindowStyle Hidden
    Set-Content -Path $PidFile -Value $proc.Id
    Write-Host "Tunnel started with pid $($proc.Id)."
}

function Stop-Tunnel {
    if (-not (Test-TunnelRunning)) {
        Write-Host "Tunnel not running."
        return
    }
    $pid = Get-Content $PidFile
    Write-Host "Stopping tunnel pid $pid."
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}

function Get-TunnelStatus {
    if (Test-TunnelRunning) {
        Write-Host "Tunnel running (pid $(Get-Content $PidFile))."
    } else {
        Write-Host "Tunnel not running."
    }
}

$command = if ($args.Count -gt 0) { $args[0] } else { "start" }

switch ($command) {
    "start" { Start-Tunnel }
    "stop" { Stop-Tunnel }
    "status" { Get-TunnelStatus }
    "restart" { Stop-Tunnel; Start-Tunnel }
    Default { Write-Host "Usage: tunnel_daemon.ps1 {start|stop|status|restart}"; exit 1 }
}
