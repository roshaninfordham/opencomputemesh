#Requires -Version 5.1
<#
.SYNOPSIS
  Install ocm-node from OpenComputeMesh GitHub releases on Windows x64.

.DESCRIPTION
  Downloads the latest (or a pinned) release, verifies it against SHA256SUMS.txt, installs it under
  %LOCALAPPDATA%\Programs\ocm-node and adds that folder to the user PATH. No admin rights needed.

.EXAMPLE
  irm https://raw.githubusercontent.com/roshaninfordham/opencomputemesh/main/scripts/install.ps1 | iex

.EXAMPLE
  $env:OCM_VERSION = 'v0.1.3'; .\install.ps1
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repo = if ($env:OCM_REPO) { $env:OCM_REPO } else { 'roshaninfordham/opencomputemesh' }
$version = if ($env:OCM_VERSION) { $env:OCM_VERSION } else { 'latest' }
$installDir = if ($env:OCM_INSTALL_DIR) { $env:OCM_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'Programs\ocm-node' }

try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

$arch = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
if ($arch -eq 'ARM64') {
    throw 'no Windows arm64 build yet. Build one from source (crates/ocm-node) or use the Python agent: uv tool install opencomputemesh'
}
if ($arch -ne 'AMD64') {
    throw "unsupported Windows architecture: $arch (only x64 has a build)"
}

function Resolve-ReleaseTag([string]$wanted) {
    if ($wanted -ne 'latest') {
        if ($wanted -notmatch '^v') { return "v$wanted" }
        return $wanted
    }
    $url = "https://github.com/$repo/releases/latest"
    $response = Invoke-WebRequest -Uri $url -UseBasicParsing
    $final = $null
    if ($response.BaseResponse.ResponseUri) {
        $final = $response.BaseResponse.ResponseUri.AbsoluteUri
    } elseif ($response.BaseResponse.RequestMessage -and $response.BaseResponse.RequestMessage.RequestUri) {
        $final = $response.BaseResponse.RequestMessage.RequestUri.AbsoluteUri
    }
    if (-not $final) {
        throw "could not read the latest release from $url (set `$env:OCM_VERSION = 'vX.Y.Z' to install a known tag)"
    }
    return ($final.TrimEnd('/') -split '/')[-1]
}

$tag = Resolve-ReleaseTag $version
if ($tag -notmatch '^v\d+\.\d+\.\d+$') {
    throw "not a release tag: $version (expected vX.Y.Z)"
}

$versionNumber = $tag.Substring(1)
$asset = "ocm-node-$versionNumber-windows-x64.exe"
$base = "https://github.com/$repo/releases/download/$tag"
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("ocm-install-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null

try {
    Write-Host "downloading $asset ($tag)"
    Invoke-WebRequest -Uri "$base/$asset" -OutFile (Join-Path $tmp $asset) -UseBasicParsing
    Invoke-WebRequest -Uri "$base/SHA256SUMS.txt" -OutFile (Join-Path $tmp 'SHA256SUMS.txt') -UseBasicParsing

    $expected = $null
    foreach ($line in Get-Content (Join-Path $tmp 'SHA256SUMS.txt')) {
        $parts = $line -split '\s+', 2
        if ($parts.Count -eq 2 -and $parts[1].Trim() -eq $asset) {
            $expected = $parts[0].Trim().ToLowerInvariant()
            break
        }
    }
    if (-not $expected) { throw "SHA256SUMS.txt has no entry for $asset" }

    $actual = (Get-FileHash (Join-Path $tmp $asset) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "checksum mismatch for ${asset}: expected $expected, got $actual. Nothing was installed."
    }

    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    $target = Join-Path $installDir 'ocm-node.exe'
    $old = $null
    if (Test-Path $target) {
        try { $old = (& $target --version 2>$null | Select-Object -First 1) } catch { }
    }
    Copy-Item -Path (Join-Path $tmp $asset) -Destination $target -Force
    $new = $null
    try { $new = (& $target --version 2>$null | Select-Object -First 1) } catch { }
    if (-not $new) { $new = "ocm-node $versionNumber" }

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($null -eq $userPath) { $userPath = '' }
    $trimmed = $installDir.TrimEnd('\')
    $onPath = @($userPath -split ';' | Where-Object { $_ -and $_.Trim().TrimEnd('\') -ieq $trimmed }).Count -gt 0
    $addedToPath = $false
    if (-not $onPath) {
        $newPath = if ($userPath.Trim()) { $userPath.TrimEnd(';') + ';' + $installDir } else { $installDir }
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        $env:Path = $env:Path + ';' + $installDir
        $addedToPath = $true
    }

    Write-Host ''
    Write-Host "$new -> $target"
    if ($old -and $old -ne $new) { Write-Host "replaced $old" }
    Write-Host "sha256 verified against SHA256SUMS.txt ($tag)"
    if ($addedToPath) {
        Write-Host ''
        Write-Host "Added $installDir to your user PATH. Open a new terminal to run ocm-node by name."
    }
    Write-Host ''
    Write-Host 'next:'
    Write-Host "  ocm-node --coordinator https://your-coordinator --name `"$env:COMPUTERNAME`""
    Write-Host 'It asks for the one-time token from Machines -> Share my compute.'
    Write-Host 'Windows may warn the first time: the binary is not code-signed.'
}
finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
