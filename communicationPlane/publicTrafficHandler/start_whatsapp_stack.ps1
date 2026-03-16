$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$EnvPath = Join-Path $ProjectRoot "env"

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

if (-not $env:SERVER_HOST) { $env:SERVER_HOST = Get-EnvValueFromFile -Path $EnvPath -Key "SERVER_HOST" }
if (-not $env:SERVER_PORT) { $env:SERVER_PORT = Get-EnvValueFromFile -Path $EnvPath -Key "SERVER_PORT" }
if (-not $env:TUNNEL_TOKEN) { $env:TUNNEL_TOKEN = Get-EnvValueFromFile -Path $EnvPath -Key "TUNNEL_TOKEN" }

if (-not $env:SERVER_HOST) { $env:SERVER_HOST = "127.0.0.1" }
if (-not $env:SERVER_PORT) { $env:SERVER_PORT = "5050" }

$serverCmd = if ($env:SERVER_CMD) { $env:SERVER_CMD } else { "python app.py" }
$cloudflared = if ($env:CLOUDFLARED_BIN) { $env:CLOUDFLARED_BIN } else { "cloudflared" }

function Test-ServerRunning {
    $uri = "http://$($env:SERVER_HOST):$($env:SERVER_PORT)/health"
    try {
        $response = Invoke-WebRequest -Uri $uri -Method Get -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-TunnelRunning {
    $procs = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" | Where-Object { $_.CommandLine -match "tunnel run" }
    if ($procs) { return $true }
    return $false
}

$serverProcess = $null
$tunnelProcess = $null

if (Test-ServerRunning) {
    Write-Host "Server already running. Skipping server start."
} else {
    $serverDir = Join-Path $ProjectRoot "communicationPlane\server"
    $serverProcess = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $serverCmd -WorkingDirectory $serverDir -PassThru
    Write-Host "Server PID: $($serverProcess.Id)"
}

if (Test-TunnelRunning) {
    Write-Host "Tunnel already running. Skipping tunnel start."
} else {
    if (-not $env:TUNNEL_TOKEN) {
        Write-Warning "TUNNEL_TOKEN missing. Skipping tunnel start."
    } else {
        $cloudflaredCmd = Get-Command $cloudflared -ErrorAction SilentlyContinue
        if (-not $cloudflaredCmd) {
            Write-Warning "cloudflared not found in PATH. Skipping tunnel start."
        } else {
            $tunnelProcess = Start-Process -FilePath $cloudflared -ArgumentList "tunnel", "run", "--token", $env:TUNNEL_TOKEN -PassThru
            Write-Host "Tunnel PID: $($tunnelProcess.Id)"
        }
    }
}

if (-not $serverProcess -and -not $tunnelProcess) {
    Write-Host "Server and tunnel already running. Nothing to do."
    exit 0
}

while ($true) {
    if ($serverProcess -and $serverProcess.HasExited) {
        Write-Host "Server stopped. Shutting down tunnel."
        break
    }
    if ($tunnelProcess -and $tunnelProcess.HasExited) {
        Write-Host "Tunnel stopped. Shutting down server."
        break
    }
    Start-Sleep -Seconds 1
}

if ($serverProcess -and -not $serverProcess.HasExited) {
    Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
}
if ($tunnelProcess -and -not $tunnelProcess.HasExited) {
    Stop-Process -Id $tunnelProcess.Id -Force -ErrorAction SilentlyContinue
}
