param(
    [Parameter(Mandatory = $false)]
    [string]$LeoRecipient
)

$ErrorActionPreference = 'Stop'

$evmPattern = '^0x[0-9a-fA-F]{40}$'
$walletsPath = Join-Path $env:LOCALAPPDATA 'NexusVector\Config\wallets.private-local.json'
$annaMarkPath = Join-Path $env:LOCALAPPDATA 'NexusVector\Config\anna_mark_video_recipients.private-local.json'
$configPath = Join-Path $env:LOCALAPPDATA 'NexusVector\Config\anna_mark_leo_video_recipients.private-local.json'

if (-not (Test-Path -LiteralPath $walletsPath -PathType Leaf)) {
    throw 'Nexus Vector private wallet registry was not found.'
}
if (-not (Test-Path -LiteralPath $annaMarkPath -PathType Leaf)) {
    throw 'Existing Anna/Mark private recipient config was not found.'
}

$walletDocument = Get-Content -LiteralPath $walletsPath -Raw | ConvertFrom-Json
if ($walletDocument.schema_version -ne 1) {
    throw 'Wallet registry schema mismatch.'
}
if ($walletDocument.network.chain_id -ne 84532 -or $walletDocument.network.environment -ne 'testnet') {
    throw 'Wallet registry is not bound to Base Sepolia testnet.'
}
if ($walletDocument.safety.mainnet_blocked -ne $true) {
    throw 'Wallet registry does not confirm mainnet_blocked=true.'
}

$annaMarkDocument = Get-Content -LiteralPath $annaMarkPath -Raw | ConvertFrom-Json
if ($annaMarkDocument.schema_version -ne 1) {
    throw 'Existing Anna/Mark recipient config schema mismatch.'
}
if ($annaMarkDocument.network.chain_id -ne 84532 -or $annaMarkDocument.network.environment -ne 'testnet') {
    throw 'Existing Anna/Mark recipient config is not bound to Base Sepolia testnet.'
}
if ($annaMarkDocument.safety.mainnet_blocked -ne $true) {
    throw 'Existing Anna/Mark recipient config does not confirm mainnet_blocked=true.'
}

$sender = [string]$walletDocument.wallets.keeperhub_organization_wallet
$anna = [string]$walletDocument.wallets.personal_recipient_wallet
$mark = [string]$annaMarkDocument.recipients.mark_recipient_wallet

foreach ($entry in @($sender, $anna, $mark)) {
    if ($entry -notmatch $evmPattern) {
        throw 'One of the existing sender/Anna/Mark EVM addresses is invalid.'
    }
}

if ([string]::IsNullOrWhiteSpace($LeoRecipient)) {
    $LeoRecipient = Read-Host 'Leo recipient Base Sepolia EVM address (user-controlled)'
}
$LeoRecipient = $LeoRecipient.Trim()
if ($LeoRecipient -notmatch $evmPattern) {
    throw 'Leo recipient must be a valid 0x-prefixed 20-byte EVM address.'
}

$normalized = @(
    $sender.ToLowerInvariant(),
    $anna.ToLowerInvariant(),
    $mark.ToLowerInvariant(),
    $LeoRecipient.ToLowerInvariant()
)
if (($normalized | Select-Object -Unique).Count -ne 4) {
    throw 'Sender, Anna, Mark and Leo addresses must all be distinct.'
}

$directory = Split-Path -Parent $configPath
New-Item -ItemType Directory -Path $directory -Force | Out-Null

$document = [ordered]@{
    schema_version = 1
    updated_at_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.ffffffZ')
    network = [ordered]@{
        name = 'Base Sepolia'
        chain_id = 84532
        environment = 'testnet'
    }
    recipients = [ordered]@{
        mark_recipient_wallet = $mark
        leo_recipient_wallet = $LeoRecipient
    }
    safety = [ordered]@{
        mainnet_blocked = $true
        contains_seed_phrase = $false
        contains_wallet_private_key = $false
        contains_turnkey_signing_key = $false
    }
}

$json = $document | ConvertTo-Json -Depth 5
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($configPath, $json + [Environment]::NewLine, $utf8NoBom)

function Mask-Address([string]$value) {
    return $value.Substring(0, 8) + '…' + $value.Substring($value.Length - 6)
}

[ordered]@{
    status = 'PASS'
    config_path = $configPath
    network = 'Base Sepolia'
    chain_id = 84532
    anna_recipient_masked = (Mask-Address $anna)
    mark_recipient_masked = (Mask-Address $mark)
    leo_recipient_masked = (Mask-Address $LeoRecipient)
    sender_masked = (Mask-Address $sender)
    recipients_distinct = $true
    mainnet_blocked = $true
} | ConvertTo-Json -Compress
