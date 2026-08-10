[CmdletBinding()]
param(
    [string]$RunRef = "adapter-smoke-$(Get-Date -Format yyyyMMddHHmmss)"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$adapter = Join-Path $repoRoot "adapter\server.py"
$logRoot = Join-Path $repoRoot "logs"
$env:NEXUS_VECTOR_RUN_REF = $RunRef
$env:NEXUS_VECTOR_OPERATOR_LOG_ROOT = $logRoot

$proc = $null
try {
    Write-Host "[1/8] Starting adapter with fresh run_ref=$RunRef"
    $proc = Start-Process -FilePath "python" -ArgumentList @($adapter) -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden

    $root = "http://127.0.0.1:8765/"
    $origin = "http://127.0.0.1:8765"
    $token = $null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 250
        try {
            $html = (Invoke-WebRequest -Uri $root -UseBasicParsing).Content
            if ($html -match 'window\.NEXUS_ADAPTER_TOKEN="([^"]+)"') {
                $token = $Matches[1]
                break
            }
        } catch {
            # Adapter is still starting.
        }
    }
    if (-not $token) { throw "Adapter did not become ready or token was not injected." }

    $headers = @{ "X-Nexus-Token" = $token; "Origin" = $origin }

    Write-Host "[2/8] GET status before prepare"
    $statusBefore = Invoke-RestMethod -Uri "$root/api/mission/status" -Headers $headers
    if ($statusBefore.run_ref -ne $RunRef) { throw "Unexpected run_ref before prepare: $($statusBefore.run_ref)" }

    Write-Host "[3/8] POST prepare"
    $prepare = Invoke-RestMethod -Method Post -Uri "$root/api/mission/prepare" -Headers $headers -ContentType "application/json" -Body "{}"
    if ($prepare.status -ne "PREPARED") { throw "Prepare did not return PREPARED." }
    if ($prepare.network_calls -ne 0) { throw "Prepare unexpectedly made network calls." }

    Write-Host "[4/8] GET status after prepare"
    $status = Invoke-RestMethod -Uri "$root/api/mission/status" -Headers $headers
    if ($status.next_effect -ne "anna") { throw "Backend next_effect is not anna: $($status.next_effect)" }
    if ($status.mission_state -ne "READY_FOR_EXECUTION") { throw "Unexpected mission state: $($status.mission_state)" }
    $challenge = $status.simulation_approval_challenge
    if (-not $challenge) { throw "Backend did not provide simulation approval challenge." }

    Write-Host "[5/8] POST simulate anna (safe: no funds movement)"
    $simulateBody = @{ approval = $challenge } | ConvertTo-Json -Compress
    $sim = Invoke-RestMethod -Method Post -Uri "$root/api/mission/anna/simulate" -Headers $headers -ContentType "application/json" -Body $simulateBody
    if ($sim.status -ne "PASS") { throw "Simulation did not PASS." }
    if ($sim.broadcast_posts -ne 0) { throw "Simulation unexpectedly broadcast." }
    if ($sim.funds_movement -ne "NONE_FROM_SIMULATION") { throw "Simulation reports unexpected funds movement: $($sim.funds_movement)" }
    if ($sim.provider_summary.would_revert) { throw "Simulation reports would_revert=true." }
    if (-not $sim.broadcast_approval_challenge) { throw "Simulation did not provide broadcast approval challenge." }

    Write-Host "[6/8] GET status after simulation"
    $statusAfter = Invoke-RestMethod -Uri "$root/api/mission/status" -Headers $headers
    if ($statusAfter.next_effect -ne "anna") { throw "Backend advanced next_effect unexpectedly: $($statusAfter.next_effect)" }

    Write-Host "[7/8] GET static API client"
    $client = (Invoke-WebRequest -Uri "$root/api-client.js" -Headers $headers -UseBasicParsing).Content
    if ($client -notmatch "NexusAdapter") { throw "Adapter client asset was not served." }

    Write-Host "[8/8] Verify operator log exists"
    $logPath = Join-Path (Join-Path $logRoot $RunRef) "operator_timeline.log"
    if (-not (Test-Path $logPath)) { throw "Operator log was not created: $logPath" }

    Write-Host "SMOKE_TEST=PASS"
    Write-Host "RUN_REF=$RunRef"
    Write-Host "LOG=$logPath"
    Write-Host "NO_BROADCAST_PERFORMED=true"
}
finally {
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}
