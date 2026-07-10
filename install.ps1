param(
  [string]$Config = $env:HFC_CONFIG,
  [string]$EnvFile = $env:HFC_ENV_FILE,
  [string]$Version = $env:HFC_VERSION,
  [string]$ProfileId = $env:HERMES_FEISHU_CARD_PROFILE_ID,
  [string]$EventUrl = $env:HERMES_FEISHU_CARD_EVENT_URL,
  [switch]$NoRepair
)

$ErrorActionPreference = "Stop"

$Repo = if ($env:HFC_REPO) { $env:HFC_REPO } else { "baileyh8/hermes-feishu-streaming-card" }
$HermesDir = if ($env:HERMES_DIR) { $env:HERMES_DIR } else { Join-Path $HOME ".hermes/hermes-agent" }
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
        return $Version
    }
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ "User-Agent" = "hermes-feishu-card-installer" }
        if ($release.tag_name) {
            return [string]$release.tag_name
        }
    } catch {
        return "main"
    }
    return "main"
}

function Read-HfcEnvFile {
    $values = @{}
    if (!(Test-Path $EnvFile)) {
        return $values
    }
    Write-HfcLog "loading credentials from $EnvFile"
    $allowed = @(
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
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if (!$line -or $line.StartsWith("#") -or !$line.Contains("=")) {
            return
        }
        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($key -in $allowed) {
            $values[$key] = $value
        }
    }
    return $values
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
    $next = foreach ($line in $lines) {
        if ($line.StartsWith("$Key=")) {
            "$Key=$Value"
            $updated = $true
        } else {
            $line
        }
    }
    if (!$updated) {
        $next += "$Key=$Value"
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
    if ($env:FEISHU_APP_ID -and $env:FEISHU_APP_SECRET) {
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
}

function Install-HfcPackage {
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
    $tag = Resolve-HfcVersion
    $spec = "git+https://github.com/$Repo.git"
    if ($tag -and $tag -ne "main") {
        $spec = "$spec@$tag"
    }
    [Environment]::SetEnvironmentVariable("HFC_INSTALL_SPEC", $spec, "Process")
    Write-HfcLog "installing $Repo@$tag"
    $pipArgs = @("install", "--upgrade", $spec)
    if ($PipUserFlag -and $PipUserFlag -notin @("0", "false", "False")) {
        $pipArgs = @("install", $PipUserFlag, "--upgrade", $spec)
    }
    & $PythonBin -m pip @pipArgs
}

function Invoke-HfcSetup {
    $args = @(
        "-m", "hermes_feishu_card.cli", "setup",
        "--hermes-dir", $HermesDir,
        "--config", $ConfigPath,
        "--env-file", $EnvFile,
        "--profile-id", $ProfileId,
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
$ProfileId = if ($ProfileId) { $ProfileId } else { "default" }
$EventUrl = if ($EventUrl) { $EventUrl } else { "http://127.0.0.1:8765/events" }

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
[Environment]::SetEnvironmentVariable("HFC_VERSION", $Version, "Process")
[Environment]::SetEnvironmentVariable("HERMES_FEISHU_CARD_PROFILE_ID", $ProfileId, "Process")
[Environment]::SetEnvironmentVariable("HERMES_FEISHU_CARD_EVENT_URL", $EventUrl, "Process")
[Environment]::SetEnvironmentVariable("HFC_NO_REPAIR", $NoRepairValue, "Process")

Ensure-HfcCredentials
Install-HfcPackage
Invoke-HfcSetup
Write-HfcLog "done"
Write-HfcLog "status: $PythonBin -m hermes_feishu_card.cli status --config `"$ConfigPath`""
