param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9-]{2,63}$')]
    [string]$RunRef
)

$ErrorActionPreference = 'Stop'

$credentialPath = Join-Path $env:LOCALAPPDATA 'NexusVector\Secrets\keeperhub_organization_api_key.credential.xml'
$pythonTool = Join-Path $PSScriptRoot 'capture_keeperhub_provider_transaction_binding.py'

if (-not (Test-Path -LiteralPath $credentialPath -PathType Leaf)) {
    throw 'KeeperHub DPAPI credential store was not found.'
}
if (-not (Test-Path -LiteralPath $pythonTool -PathType Leaf)) {
    throw 'Provider transaction binding tool was not found.'
}

$credential = Import-Clixml -LiteralPath $credentialPath
if (-not ($credential -is [System.Management.Automation.PSCredential])) {
    throw 'KeeperHub credential store has an unexpected format.'
}

$bstr = [IntPtr]::Zero
$plain = $null
$exitCode = 2

try {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($credential.Password)
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    if ([string]::IsNullOrWhiteSpace($plain)) {
        throw 'KeeperHub credential store contains an empty API key.'
    }

    $env:KEEPERHUB_API_KEY = $plain
    python $pythonTool --run-ref $RunRef
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
