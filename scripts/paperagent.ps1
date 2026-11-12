param([Parameter(Mandatory=$true)][ValidateSet('start','stop')][string]$Action)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $root 'logs'
$log = Join-Path $logDir 'paperagent.log'
$stateFile = Join-Path $logDir 'processes.json'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
function Log([string]$message) {
    $line = '{0} {1}' -f (Get-Date -Format o), $message
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
    Write-Host $message
}
function Read-State {
    if (Test-Path -LiteralPath $stateFile) { return Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json }
    return $null
}
function Save-State($state) {
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateFile -Encoding UTF8
}
function Owned($entry) {
    if (-not $entry) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$entry.pid)" -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    return $process.CreationDate.ToUniversalTime().Ticks -eq [long]$entry.createdTicks -and
        $process.ExecutablePath -eq $entry.executable -and
        $process.CommandLine -and $process.CommandLine.Contains($entry.token) -and
        $process.CommandLine.Contains((Join-Path $PSScriptRoot 'service.ps1'))
}
function Stop-Owned($state) {
    $allSafe = $true
    foreach ($entry in @($state.frontend, $state.backend)) {
        if (-not $entry) { continue }
        if (Owned $entry) {
            Log "Stopping $($entry.role), PID $($entry.pid), including its child processes."
            & taskkill.exe /PID $entry.pid /T /F 2>&1 | Out-Null
            Start-Sleep -Milliseconds 400
            if (Owned $entry) { Log "Could not stop $($entry.role)."; $allSafe = $false }
            else { Log "$($entry.role) stopped." }
        } elseif (Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$entry.pid)" -ErrorAction SilentlyContinue) {
            Log "PID $($entry.pid) no longer matches its recorded identity; not stopping it."
            $allSafe = $false
        } else { Log "$($entry.role) already exited." }
    }
    if ($allSafe) { Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue }
    return $allSafe
}
function Start-ServiceProcess([string]$role, [string]$token) {
    $service = Join-Path $PSScriptRoot 'service.ps1'
    $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"{0}"' -f $service),'-Role',$role,'-Token',$token)
    $p = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList $args -WindowStyle Hidden -PassThru
    $info = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)"
    if (-not $info) { throw "$role process exited immediately" }
    $entry = @{role=$role; pid=$p.Id; createdTicks=$info.CreationDate.ToUniversalTime().Ticks; executable=$info.ExecutablePath; token=$token}
    Log "Started $role supervisor PID $($p.Id), owner=$token."
    return $entry
}
function Wait-Http([string]$url, [string]$label, $state) {
    $end = (Get-Date).AddSeconds(35)
    while ((Get-Date) -lt $end) {
        if (-not (Owned $state.backend) -or ($state.frontend -and -not (Owned $state.frontend))) { throw "A service exited before $label was ready." }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3
            if ($response.StatusCode -eq 200) { Log "${label}: HTTP 200"; return }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    throw "$label failed or timed out."
}
$hash = [BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant()))).Replace('-','').Substring(0,24)
$mutex = New-Object Threading.Mutex($false, "Local\PaperAgent-$hash")
$locked = $false
$state = $null
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another PaperAgent start/stop operation is in progress.' }
    $state = Read-State
    if ($Action -eq 'stop') {
        if (-not $state) { Log 'No recorded PaperAgent processes.'; exit 0 }
        if (-not (Stop-Owned $state)) { throw 'Some process identities could not be verified; state retained.' }
        exit 0
    }
    if ($state) {
        if ((Owned $state.backend) -or (Owned $state.frontend)) { throw 'PaperAgent is already running or partially running. Stop it first.' }
        foreach ($entry in @($state.backend,$state.frontend)) {
            if ($entry -and (Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$entry.pid)" -ErrorAction SilentlyContinue)) { throw 'A recorded PID has been reused; run stop and inspect the retained state.' }
        }
        Remove-Item -LiteralPath $stateFile -Force
    }
    $python = Join-Path $root '后端\.venv\Scripts\python.exe'
    $vite = Join-Path $root '前端\node_modules\vite\bin\vite.js'
    foreach ($path in @((Join-Path $root '后端\app\main.py'), (Join-Path $root '前端\package.json'), $python, $vite)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required project file missing: $path" }
    }
    if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) { throw 'Node.js is not available on PATH.' }
    foreach ($port in @(8000,5173)) {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        if ($listeners.Count) {
            foreach ($listener in $listeners) { Log "Port $port occupied: address=$($listener.LocalAddress), PID=$($listener.OwningProcess). No process was stopped." }
            throw "Port $port is occupied."
        }
    }
    $services = @(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'mysql' })
    if ($services.Count) { foreach ($service in $services) { Log "MySQL service $($service.Name): $($service.Status)." } }
    $dbCheck = @'
from app.core.config import get_database_settings
from app.infrastructure.database import get_engine
from sqlalchemy import text
try:
    s = get_database_settings()
    if s.database != 'paper_agent' or s.user.lower() != 'paper_agent_app': raise ValueError()
    with get_engine().connect() as c:
        if c.execute(text('SELECT DATABASE()')).scalar_one() != 'paper_agent': raise ValueError()
        if c.execute(text('SELECT CURRENT_USER()')).scalar_one().split('@')[0].lower() != 'paper_agent_app': raise ValueError()
        if c.execute(text('SELECT 1')).scalar_one() != 1: raise ValueError()
except Exception:
    raise SystemExit(1)
'@
    Push-Location (Join-Path $root '后端')
    try { $dbCheck | & $python - 2>$null; if ($LASTEXITCODE -ne 0) { throw 'MySQL connection or dedicated account check failed.' } }
    finally { Pop-Location }
    Log 'MySQL dedicated database/account SELECT 1: OK.'
    $token = [guid]::NewGuid().ToString()
    $state = @{ version=1; owner=$token; created=(Get-Date).ToUniversalTime().ToString('o'); backend=$null; frontend=$null }
    $state.backend = Start-ServiceProcess 'backend' $token
    Save-State $state
    Wait-Http 'http://127.0.0.1:8000/health' 'Backend health' $state
    Wait-Http 'http://127.0.0.1:8000/api/projects?page=1&page_size=1' 'Project list / MySQL' $state
    $state.frontend = Start-ServiceProcess 'frontend' $token
    Save-State $state
    Wait-Http 'http://127.0.0.1:5173/projects' 'Frontend page' $state
    Wait-Http 'http://127.0.0.1:5173/api/projects?page=1&page_size=1' 'Frontend proxy / MySQL' $state
    Start-Process 'http://127.0.0.1:5173/projects'
    Log 'PaperAgent is ready; browser opened.'
} catch {
    Log "Failure: $($_.Exception.Message)"
    if ($Action -eq 'start' -and $state -and $state.owner -and $token -and $state.owner -eq $token) {
        if (-not (Stop-Owned $state)) { Log 'Cleanup incomplete; inspect the retained state.' }
    }
    Write-Host "See log: $log"
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
