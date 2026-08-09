param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('prepare', 'status', 'simulate', 'broadcast', 'provider-bind', 'verify')]
    [string]$Command,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9-]{2,63}$')]
    [string]$RunRef,

    [Parameter(Mandatory = $false)]
    [ValidateSet('anna', 'mark', 'leo')]
    [string]$Effect,

    [Parameter(Mandatory = $false)]
    [string]$Approval,

    [Parameter(Mandatory = $false)]
    [switch]$ApproveTestnetWrite,

    [Parameter(Mandatory = $false)]
    [string]$LogPath
)

$ErrorActionPreference = 'Stop'

$pythonTool = Join-Path $PSScriptRoot 'anna_mark_leo_video_mission.py'
$preflightTool = Join-Path $PSScriptRoot 'anna_mark_leo_video_operator_preflight.py'
$credentialPath = Join-Path $env:LOCALAPPDATA 'NexusVector\Secrets\keeperhub_organization_api_key.credential.xml'
$configuredLogRoot = $env:NEXUS_VECTOR_OPERATOR_LOG_ROOT
if ([string]::IsNullOrWhiteSpace($configuredLogRoot)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'LOCALAPPDATA is required when NEXUS_VECTOR_OPERATOR_LOG_ROOT is not set.'
    }
    $defaultLogRoot = Join-Path $env:LOCALAPPDATA 'NexusVector\Logs\anna-mark-leo-video-mission'
}
else {
    $defaultLogRoot = $configuredLogRoot.Trim()
}

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = Join-Path (Join-Path $defaultLogRoot $RunRef) 'operator_timeline.log'
}

New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Add-TimelineLine {
    param(
        [Parameter(Mandatory = $true)][string]$Event,
        [Parameter(Mandatory = $false)][string]$Text = ''
    )
    $stamp = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    $line = $stamp + ' [' + $Event + '] ' + $Text + [Environment]::NewLine
    [System.IO.File]::AppendAllText($LogPath, $line, $utf8NoBom)
}

function Log-CapturedOutput {
    param(
        [Parameter(Mandatory = $false)]$Lines,
        [Parameter(Mandatory = $true)][string]$Event,
        [Parameter(Mandatory = $false)][switch]$Echo
    )
    foreach ($line in @($Lines)) {
        $text = [string]$line
        Add-TimelineLine -Event $Event -Text $text
        if ($Echo) {
            Write-Output $text
        }
    }
}

if (-not (Test-Path -LiteralPath $pythonTool -PathType Leaf)) {
    throw 'Anna/Mark/Leo video Mission tool was not found.'
}
if (-not (Test-Path -LiteralPath $preflightTool -PathType Leaf)) {
    throw 'Anna/Mark/Leo operator preflight tool was not found.'
}

$effectCommands = @('simulate', 'broadcast', 'provider-bind', 'verify')
$approvalCommands = @('simulate', 'broadcast')
$keyCommands = @('simulate', 'broadcast', 'provider-bind')
$preflightCommands = @('simulate', 'broadcast')

if ($effectCommands -contains $Command -and [string]::IsNullOrWhiteSpace($Effect)) {
    throw "Command '$Command' requires -Effect anna|mark|leo."
}
if ($approvalCommands -contains $Command -and [string]::IsNullOrWhiteSpace($Approval)) {
    throw "Command '$Command' requires -Approval."
}
if ($Command -eq 'broadcast' -and -not $ApproveTestnetWrite) {
    throw 'Broadcast requires -ApproveTestnetWrite.'
}

$arguments = @($Command, '--run-ref', $RunRef)
if ($effectCommands -contains $Command) {
    $arguments += @('--effect', $Effect)
}
if ($approvalCommands -contains $Command) {
    $arguments += @('--approval', $Approval)
}
if ($Command -eq 'broadcast') {
    $arguments += '--approve-testnet-write'
}

$effectForLog = if ([string]::IsNullOrWhiteSpace($Effect)) { '-' } else { $Effect }
Add-TimelineLine -Event 'COMMAND_START' -Text ("command={0} run_ref={1} effect={2}" -f $Command, $RunRef, $effectForLog)
Add-TimelineLine -Event 'SAFETY' -Text 'mainnet_blocked=true auto_mutating_retry=false approval_value_not_logged=true'

# Recovery / duplicate-prevention gate. Local SQLite reads only. On STOP, the
# lower-level runner is not entered and the DPAPI credential is not loaded.
if ($preflightCommands -contains $Command) {
    Add-TimelineLine -Event 'PREFLIGHT_START' -Text ("command={0} effect={1}" -f $Command, $Effect)
    $preflightOutput = & python $preflightTool $Command --run-ref $RunRef --effect $Effect 2>&1
    $preflightExit = $LASTEXITCODE
    Log-CapturedOutput -Lines $preflightOutput -Event 'PREFLIGHT_OUTPUT'
    Add-TimelineLine -Event 'PREFLIGHT_END' -Text ("exit_code={0}" -f $preflightExit)
    if ($preflightExit -ne 0) {
        Log-CapturedOutput -Lines $preflightOutput -Event 'PREFLIGHT_STOP_ECHO' -Echo
        Add-TimelineLine -Event 'COMMAND_END' -Text ("exit_code={0} stopped_before_credential=true" -f $preflightExit)
        Write-Output ("OPERATOR_LOG_PATH={0}" -f $LogPath)
        exit $preflightExit
    }
}

$bstr = [IntPtr]::Zero
$plain = $null
$credential = $null
$exitCode = 2

try {
    if ($keyCommands -contains $Command) {
        Add-TimelineLine -Event 'CREDENTIAL_LOAD_START' -Text 'source=windows_dpapi value_not_logged=true'
        if (-not (Test-Path -LiteralPath $credentialPath -PathType Leaf)) {
            throw 'KeeperHub DPAPI credential store was not found.'
        }
        $credential = Import-Clixml -LiteralPath $credentialPath
        if (-not ($credential -is [System.Management.Automation.PSCredential])) {
            throw 'KeeperHub credential store has an unexpected format.'
        }
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($credential.Password)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        if ([string]::IsNullOrWhiteSpace($plain)) {
            throw 'KeeperHub credential store contains an empty API key.'
        }
        $env:KEEPERHUB_API_KEY = $plain
        Add-TimelineLine -Event 'CREDENTIAL_LOAD_END' -Text 'status=loaded_to_process_env value_not_logged=true'
    }

    Add-TimelineLine -Event 'RUNNER_START' -Text ("command={0} effect={1}" -f $Command, $effectForLog)
    $toolOutput = & python $pythonTool @arguments 2>&1
    $exitCode = $LASTEXITCODE
    Log-CapturedOutput -Lines $toolOutput -Event 'RUNNER_OUTPUT' -Echo
    Add-TimelineLine -Event 'RUNNER_END' -Text ("exit_code={0}" -f $exitCode)
}
catch {
    Add-TimelineLine -Event 'WRAPPER_ERROR' -Text $_.Exception.Message
    throw
}
finally {
    Remove-Item Env:KEEPERHUB_API_KEY -ErrorAction SilentlyContinue
    $plain = $null
    $credential = $null
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        $bstr = [IntPtr]::Zero
    }
    Add-TimelineLine -Event 'CREDENTIAL_CLEANUP' -Text 'process_env_removed=true bstr_zeroed=true'
}

Add-TimelineLine -Event 'COMMAND_END' -Text ("exit_code={0}" -f $exitCode)
Write-Output ("OPERATOR_LOG_PATH={0}" -f $LogPath)
exit $exitCode
