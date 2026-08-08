param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('prepare', 'status', 'simulate', 'broadcast', 'provider-bind', 'verify')]
    [string]$Command,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9-]{2,63}$')]
    [string]$RunRef,

    [Parameter(Mandatory = $false)]
    [ValidateSet('anna', 'mark')]
    [string]$Effect,

    [Parameter(Mandatory = $false)]
    [string]$Approval,

    [Parameter(Mandatory = $false)]
    [switch]$ApproveTestnetWrite
)

$ErrorActionPreference = 'Stop'

$pythonTool = Join-Path $PSScriptRoot 'anna_mark_video_mission.py'
$credentialPath = Join-Path $env:LOCALAPPDATA 'NexusVector\Secrets\keeperhub_organization_api_key.credential.xml'

if (-not (Test-Path -LiteralPath $pythonTool -PathType Leaf)) {
    throw 'Anna/Mark video Mission tool was not found.'
}

$effectCommands = @('simulate', 'broadcast', 'provider-bind', 'verify')
$approvalCommands = @('simulate', 'broadcast')
$keyCommands = @('simulate', 'broadcast', 'provider-bind')

if ($effectCommands -contains $Command -and [string]::IsNullOrWhiteSpace($Effect)) {
    throw "Command '$Command' requires -Effect anna|mark."
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

$bstr = [IntPtr]::Zero
$plain = $null
$credential = $null
$exitCode = 2

try {
    if ($keyCommands -contains $Command) {
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
    }

    & python $pythonTool @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    Remove-Item Env:KEEPERHUB_API_KEY -ErrorAction SilentlyContinue
    $plain = $null
    $credential = $null
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        $bstr = [IntPtr]::Zero
    }
}

exit $exitCode
