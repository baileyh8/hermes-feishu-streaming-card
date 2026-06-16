$ErrorActionPreference = "Stop"

$Repo = if ($env:HFC_REPO) { $env:HFC_REPO } else { "baileyh8/hermes-feishu-streaming-card" }
$Version = if ($env:HFC_VERSION) { $env:HFC_VERSION } else { "latest" }
$HermesDir = if ($env:HERMES_DIR) { $env:HERMES_DIR } else { Join-Path $HOME ".hermes/hermes-agent" }
$ConfigPath = if ($env:HFC_CONFIG) { $env:HFC_CONFIG } else { Join-Path $HOME ".hermes/config.yaml" }
$EnvFile = if ($env:HFC_ENV_FILE) { $env:HFC_ENV_FILE } else { Join-Path (Split-Path -Parent $ConfigPath) ".env" }
$PythonBin = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$PipUserFlag = if ($env:HFC_PIP_USER) { $env:HFC_PIP_USER } else { "--user" }

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

function Load-HfcEnvFile {
    if (!(Test-Path $EnvFile)) {
        return
    }
    Write-HfcLog "loading credentials from $EnvFile"
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if (!$line -or $line.StartsWith("#") -or !$line.Contains("=")) {
            return
        }
        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($key) {
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
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
        "--yes"
    )
    if ($env:HFC_SKIP_START -eq "1") {
        $args += "--skip-start"
    }
    Write-HfcLog "running setup"
    & $PythonBin @args
}

Load-HfcEnvFile
Ensure-HfcCredentials
Install-HfcPackage
Invoke-HfcSetup
Write-HfcLog "done"
Write-HfcLog "status: $PythonBin -m hermes_feishu_card.cli status --config `"$ConfigPath`""
