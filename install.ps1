param(
  [string]$Config = $env:HFC_CONFIG,
  [string]$EnvFile = $env:HFC_ENV_FILE,
  [string]$Version = $env:HFC_VERSION,
  [string]$ProfileId = $env:HERMES_FEISHU_CARD_PROFILE_ID,
  [string]$EventUrl = $env:HERMES_FEISHU_CARD_EVENT_URL,
  [string]$HermesHome = $env:HERMES_HOME,
  [switch]$NoRepair
)

$ErrorActionPreference = "Stop"
$ProfileIdExplicit = $PSBoundParameters.ContainsKey("ProfileId")

$Repo = if ($env:HFC_REPO) { $env:HFC_REPO } else { "baileyh8/hermes-feishu-streaming-card" }
$HermesDir = if ($env:HERMES_DIR) { $env:HERMES_DIR } else { Join-Path $HOME ".hermes/hermes-agent" }
$HermesHome = if ($HermesHome) { $HermesHome } else { Split-Path -Parent $HermesDir }
$PythonBin = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$PipUserFlag = if ($env:HFC_PIP_USER) { $env:HFC_PIP_USER } else { "--user" }

if (!$EnvFile) {
    $initialConfig = if ($Config) { $Config } else { Join-Path $HOME ".hermes/config.yaml" }
    $EnvFile = Join-Path (Split-Path -Parent $initialConfig) ".env"
}

function Write-HfcLog {
    param([string]$Message)
    Write-Host "[hermes-feishu-card] $Message"
}

function Fail {
    param([string]$Message)
    Write-Error "[hermes-feishu-card] error: $Message"
    exit 1
}

function Resolve-HfcVersion {
    if ($Version -ne "latest") {
        if ($Version -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or $Version.Contains('..')) {
            Fail "invalid explicit version ref; no package was installed"
        }
        return $Version
    }
    try {
        $release = Invoke-RestMethod `
            -Uri "https://api.github.com/repos/$Repo/releases/latest" `
            -Headers @{ "User-Agent" = "hermes-feishu-card-installer" } `
            -TimeoutSec 15 `
            -ErrorAction Stop
    } catch {
        Fail "latest release lookup failed; no package was installed; retry later or set an explicit vX.Y.Z"
    }
    $tag = if ($null -ne $release) { [string]$release.tag_name } else { "" }
    if ($tag -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') {
        Fail "latest release response was invalid; no package was installed; retry later or set an explicit vX.Y.Z"
    }
    return $tag
}

function Get-HfcInstallSpec {
    param([string]$ResolvedVersion, [string]$Repo)
    return "git+https://github.com/$Repo.git@$ResolvedVersion"
}

$HfcAllowedEnvKeys = @(
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_CONNECTION_MODE",
    "FEISHU_HOME_CHANNEL",
    "HERMES_FEISHU_CARD_HOST",
    "HERMES_FEISHU_CARD_PORT",
    "HERMES_FEISHU_CARD_PROFILE_ID",
    "HERMES_FEISHU_CARD_EVENT_URL",
    "HFC_CONFIG",
    "HFC_VERSION",
    "HFC_NO_REPAIR"
)

function ConvertFrom-HfcEnvValue {
    param([string]$RawValue)
    $text = $RawValue.Trim()
    if (!$text) {
        return [PSCustomObject]@{ Valid = $true; Value = "" }
    }
    if ($text.StartsWith("'")) {
        $match = [regex]::Match($text, "^'([^']*)'\s*(?:#.*)?$")
        if (!$match.Success) {
            return [PSCustomObject]@{ Valid = $false; Value = "" }
        }
        return [PSCustomObject]@{ Valid = $true; Value = $match.Groups[1].Value }
    }
    if ($text.StartsWith('"')) {
        $match = [regex]::Match($text, '^"([^"]*)"\s*(?:#.*)?$')
        if (!$match.Success) {
            return [PSCustomObject]@{ Valid = $false; Value = "" }
        }
        return [PSCustomObject]@{ Valid = $true; Value = $match.Groups[1].Value }
    }
    $value = [regex]::Replace($text, '\s+#.*$', '').TrimEnd()
    return [PSCustomObject]@{ Valid = $true; Value = $value }
}

function ConvertFrom-HfcEnvLine {
    param([string]$Line)
    $text = $Line.Trim()
    if (!$text -or $text.StartsWith("#")) {
        return $null
    }
    $match = [regex]::Match(
        $text,
        '^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$'
    )
    if (!$match.Success) {
        return $null
    }
    $key = $match.Groups[1].Value
    if ($key -notin $HfcAllowedEnvKeys) {
        return $null
    }
    $parsed = ConvertFrom-HfcEnvValue $match.Groups[2].Value
    if (!$parsed.Valid) {
        return $null
    }
    return [PSCustomObject]@{ Key = $key; Value = $parsed.Value }
}

function Read-HfcEnvFile {
    $values = @{}
    if (!(Test-Path $EnvFile)) {
        return $values
    }
    Write-HfcLog "loading credentials from $EnvFile"
    foreach ($line in Get-Content $EnvFile) {
        $entry = ConvertFrom-HfcEnvLine $line
        if ($null -ne $entry) {
            $values[$entry.Key] = $entry.Value
        }
    }
    return $values
}

function ConvertTo-HfcEnvValue {
    param([string]$Value)
    if ($Value.Contains("`r") -or $Value.Contains("`n")) {
        Fail "credential values must not contain line breaks"
    }
    if (!$Value.Contains("'")) {
        return "'$Value'"
    }
    if (!$Value.Contains('"')) {
        return '"' + $Value + '"'
    }
    Fail "credential values must not contain both quote styles"
}

function Set-HfcEnvValue {
    param([string]$Key, [string]$Value)
    $dir = Split-Path -Parent $EnvFile
    if (!(Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    if (!(Test-Path $EnvFile)) {
        New-Item -ItemType File -Force -Path $EnvFile | Out-Null
    }
    $lines = @(Get-Content $EnvFile)
    $updated = $false
    $serialized = ConvertTo-HfcEnvValue $Value
    $assignmentPattern = '^\s*(?:export\s+)?' + [regex]::Escape($Key) + '\s*='
    $next = foreach ($line in $lines) {
        if ($line -match $assignmentPattern) {
            "$Key=$serialized"
            $updated = $true
        } else {
            $line
        }
    }
    if (!$updated) {
        $next += "$Key=$serialized"
    }
    Set-Content -Path $EnvFile -Value $next -Encoding UTF8
    [Environment]::SetEnvironmentVariable($Key, $Value, "Process")
}

function Read-PlainSecret {
    param([string]$Prompt)
    $secure = Read-Host $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Ensure-HfcCredentials {
    if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
        $probe = 'import sys; from hermes_feishu_card.config import load_config; from hermes_feishu_card.cli import _has_feishu_credentials; c=load_config(sys.argv[1],env_file=sys.argv[2]); p=sys.argv[3] or "default"; ps=c.get("profiles"); p=next(iter(ps)) if ps and p=="default" and p not in ps and sys.argv[4]!="1" else p; sys.exit(0 if _has_feishu_credentials(c,p) else 1)'
        $probeProfile = if ($ProfileId) { $ProfileId } else { "default" }
        & $PythonBin -c $probe $ConfigPath $EnvFile $probeProfile ([int]$ProfileIdExplicit) *> $null
        if ($LASTEXITCODE -eq 0) { return }
    }
    if ($env:FEISHU_APP_ID -and $env:FEISHU_APP_SECRET) {
        Set-HfcEnvValue "FEISHU_APP_ID" $env:FEISHU_APP_ID
        Set-HfcEnvValue "FEISHU_APP_SECRET" $env:FEISHU_APP_SECRET
        return
    }
    if ($env:HFC_NO_PROMPT -eq "1") {
        Fail "FEISHU_APP_ID/FEISHU_APP_SECRET are missing. Set them or write them to $EnvFile."
    }
    Write-HfcLog "Feishu credentials were not found. They will be saved to $EnvFile."
    if (!$env:FEISHU_APP_ID) {
        $appId = Read-Host "FEISHU_APP_ID"
        if (!$appId) { Fail "FEISHU_APP_ID is required" }
        Set-HfcEnvValue "FEISHU_APP_ID" $appId
    }
    if (!$env:FEISHU_APP_SECRET) {
        $appSecret = Read-PlainSecret "FEISHU_APP_SECRET"
        if (!$appSecret) { Fail "FEISHU_APP_SECRET is required" }
        Set-HfcEnvValue "FEISHU_APP_SECRET" $appSecret
    }
    Set-HfcEnvValue "FEISHU_APP_ID" $env:FEISHU_APP_ID
    Set-HfcEnvValue "FEISHU_APP_SECRET" $env:FEISHU_APP_SECRET
}

function Install-HfcPackage {
    param([string]$InstallSpec, [string]$ResolvedVersion)
    try {
        & $PythonBin --version | Out-Null
    } catch {
        Fail "$PythonBin was not found. Install Python 3.9+ first or set PYTHON."
    }
    try {
        & $PythonBin -m pip --version | Out-Null
    } catch {
        & $PythonBin -m ensurepip --upgrade | Out-Null
    }
    [Environment]::SetEnvironmentVariable("HFC_INSTALL_SPEC", $InstallSpec, "Process")
    Write-HfcLog "installing $Repo@$ResolvedVersion"
    $pipArgs = @("install", "--upgrade", $InstallSpec)
    if ($PipUserFlag -and $PipUserFlag -notin @("0", "false", "False")) {
        $pipArgs = @("install", $PipUserFlag, "--upgrade", $InstallSpec)
    }
    & $PythonBin -m pip @pipArgs
    if ($LASTEXITCODE -ne 0) {
        Fail "package installation failed with exit code $LASTEXITCODE"
    }
}

function Invoke-HfcSetup {
    $args = @(
        "-m", "hermes_feishu_card.cli", "setup",
        "--hermes-dir", $HermesDir,
        "--hermes-home", $HermesHome,
        "--config", $ConfigPath,
        "--env-file", $EnvFile
    )
    if ($ProfileIdExplicit) {
        $args += @("--profile-id", $ProfileId)
    }
    $args += @(
        "--event-url", $EventUrl,
        "--yes"
    )
    if ($env:HFC_SKIP_START -eq "1") {
        $args += "--skip-start"
    }
    if ($NoRepairValue -eq "1") {
        $args += "--no-repair"
    }
    Write-HfcLog "running setup"
    & $PythonBin @args
    if ($LASTEXITCODE -ne 0) {
        Fail "setup failed with exit code $LASTEXITCODE"
    }
}

$envValues = Read-HfcEnvFile
if (!$Config -and $envValues.ContainsKey("HFC_CONFIG")) {
    $Config = $envValues["HFC_CONFIG"]
}
if (!$Version -and $envValues.ContainsKey("HFC_VERSION")) {
    $Version = $envValues["HFC_VERSION"]
}
if (!$ProfileId -and $envValues.ContainsKey("HERMES_FEISHU_CARD_PROFILE_ID")) {
    $ProfileId = $envValues["HERMES_FEISHU_CARD_PROFILE_ID"]
}
if (!$EventUrl -and $envValues.ContainsKey("HERMES_FEISHU_CARD_EVENT_URL")) {
    $EventUrl = $envValues["HERMES_FEISHU_CARD_EVENT_URL"]
}
$NoRepairValue = if ($NoRepair.IsPresent) {
    "1"
} elseif ($env:HFC_NO_REPAIR) {
    $env:HFC_NO_REPAIR
} elseif ($envValues.ContainsKey("HFC_NO_REPAIR")) {
    $envValues["HFC_NO_REPAIR"]
} else {
    "0"
}

$Config = if ($Config) { $Config } else { Join-Path $HOME ".hermes/config.yaml" }
$ConfigPath = $Config
$Version = if ($Version) { $Version } else { "latest" }
$EventUrl = if ($EventUrl) { $EventUrl } else { "http://127.0.0.1:8765/events" }
$ResolvedVersion = Resolve-HfcVersion
$ResolvedInstallSpec = Get-HfcInstallSpec `
    -ResolvedVersion $ResolvedVersion `
    -Repo $Repo

foreach ($key in @(
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_CONNECTION_MODE",
    "FEISHU_HOME_CHANNEL",
    "HERMES_FEISHU_CARD_HOST",
    "HERMES_FEISHU_CARD_PORT"
)) {
    if (!(Get-Item "Env:$key" -ErrorAction SilentlyContinue) -and $envValues.ContainsKey($key)) {
        [Environment]::SetEnvironmentVariable($key, $envValues[$key], "Process")
    }
}
[Environment]::SetEnvironmentVariable("HFC_CONFIG", $ConfigPath, "Process")
[Environment]::SetEnvironmentVariable("HFC_ENV_FILE", $EnvFile, "Process")
[Environment]::SetEnvironmentVariable("HFC_VERSION", $ResolvedVersion, "Process")
if ($ProfileId) {
    [Environment]::SetEnvironmentVariable("HERMES_FEISHU_CARD_PROFILE_ID", $ProfileId, "Process")
}
[Environment]::SetEnvironmentVariable("HERMES_FEISHU_CARD_EVENT_URL", $EventUrl, "Process")
[Environment]::SetEnvironmentVariable("HFC_NO_REPAIR", $NoRepairValue, "Process")

if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    Install-HfcPackage -InstallSpec $ResolvedInstallSpec -ResolvedVersion $ResolvedVersion
    Ensure-HfcCredentials
} else {
    Ensure-HfcCredentials
    Install-HfcPackage -InstallSpec $ResolvedInstallSpec -ResolvedVersion $ResolvedVersion
}
Invoke-HfcSetup
Write-HfcLog "done"
Write-HfcLog "status: $PythonBin -m hermes_feishu_card.cli status --config `"$ConfigPath`""
