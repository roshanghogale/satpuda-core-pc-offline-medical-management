<#
  satpuda_update.ps1 - update check for Satpuda Core.

  Asks the public GitHub releases API for the newest tag, compares it with the
  installed version, and offers to run the new installer.

  Two modes:
    -Silent    run at logon. Stays completely quiet unless there is an update
               (or the release has gone private, which the shop must know about).
               Checks at most once every -IntervalHours.
    (default)  run from the Start Menu. Always reports what it found.

  NO TOKEN IS EMBEDDED, and none should ever be. The check works only because
  the release is PUBLIC. If the repository or the release is made private this
  script cannot see it, and it says so in plain words rather than failing
  silently.
#>

[CmdletBinding()]
param(
    [string] $Repo    = 'roshanghogale/exes-for-satpuda-core',
    [string] $AppRoot = '',
    [string] $InstallerAsset = 'SatpudaCoreInstaller.exe',
    [switch] $Silent,
    [int]    $IntervalHours = 20
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing        | Out-Null

$AppName   = 'Satpuda Core'
$RegKey    = 'HKCU:\Software\Satpuda\SatpudaCore'
$StateDir  = Join-Path $env:LOCALAPPDATA 'SatpudaCore'
$StampFile = Join-Path $StateDir 'last-update-check.txt'
$LogFile   = Join-Path $StateDir 'update-check.log'

function Log([string]$m) {
    try {
        if (-not (Test-Path -LiteralPath $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
        Add-Content -LiteralPath $LogFile -Value ('{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $m) -Encoding UTF8
    } catch { }
}

function Say([string]$text, [string]$title = $AppName, $icon = [System.Windows.Forms.MessageBoxIcon]::Information) {
    if ($Silent) { Log ("(suppressed) " + $text); return }
    [void][System.Windows.Forms.MessageBox]::Show($text, $title, [System.Windows.Forms.MessageBoxButtons]::OK, $icon)
}

function Ask([string]$text, [string]$title = $AppName) {
    $r = [System.Windows.Forms.MessageBox]::Show($text, $title,
            [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
    return ($r -eq [System.Windows.Forms.DialogResult]::Yes)
}

# ---------------------------------------------------------- version compare --

# "v1.0.10" vs "1.0.9" -> numeric, per component, tolerant of a leading v,
# of different lengths, and of junk suffixes like "-beta".
function ConvertTo-VersionParts([string]$s) {
    if (-not $s) { return @() }
    $s = $s.Trim()
    if ($s -match '^[vV]') { $s = $s.Substring(1) }
    $s = ($s -split '[-+ ]')[0]
    $out = @()
    foreach ($p in ($s -split '\.')) {
        $m = [regex]::Match($p, '^\d+')
        if ($m.Success) { $out += [int]$m.Value } else { $out += 0 }
    }
    return $out
}

function Compare-Version([string]$a, [string]$b) {   # 1 if a>b, -1 if a<b, 0 equal
    $x = ConvertTo-VersionParts $a
    $y = ConvertTo-VersionParts $b
    if ($x.Count -eq 0 -or $y.Count -eq 0) { return 0 }
    $n = [Math]::Max($x.Count, $y.Count)
    for ($i = 0; $i -lt $n; $i++) {
        $xi = if ($i -lt $x.Count) { $x[$i] } else { 0 }
        $yi = if ($i -lt $y.Count) { $y[$i] } else { 0 }
        if ($xi -gt $yi) { return 1 }
        if ($xi -lt $yi) { return -1 }
    }
    return 0
}

# ------------------------------------------------------- installed version --

# Same order of truth the installer's wizard uses, so the number shown here can
# never disagree with the one in the wizard or in Programs and Features:
#
#   1. <app>\app\.satpuda-install.json - written LAST inside the staging folder
#      by satpuda_fetch.ps1, so it exists only if an extraction ran all the way
#      through. This is the version of the program on the disk.
#   2. HKCU ...\Tag - the same value, recorded when a wizard run finished.
#   3. HKCU ...\Version - the version of the INSTALLER that ran, which is only
#      a rough stand-in and is used last for that reason.
$installed = ''
if (-not $AppRoot) {
    try { $AppRoot = (Get-ItemProperty -Path $RegKey -Name 'InstallPath' -ErrorAction Stop).InstallPath } catch { }
}
if ($AppRoot) {
    try {
        $marker = Join-Path (Join-Path $AppRoot 'app') '.satpuda-install.json'
        if (Test-Path -LiteralPath $marker) { $installed = (Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json).tag }
    } catch { }
}
if (-not $installed) {
    try { $installed = (Get-ItemProperty -Path $RegKey -Name 'Tag' -ErrorAction Stop).Tag } catch { }
}
if (-not $installed) {
    try { $installed = (Get-ItemProperty -Path $RegKey -Name 'Version' -ErrorAction Stop).Version } catch { }
}
if (-not $installed) {
    Log 'installed version unknown; nothing to compare against'
    Say "Could not tell which version of $AppName is installed, so the update check was skipped." $AppName ([System.Windows.Forms.MessageBoxIcon]::Warning)
    exit 1
}
Log ("installed version: " + $installed)

# ---------------------------------------------------- once-a-day throttle --

if ($Silent) {
    try {
        if (Test-Path -LiteralPath $StampFile) {
            $last = [DateTime]::Parse((Get-Content -LiteralPath $StampFile -Raw).Trim())
            if ((Get-Date) -lt $last.AddHours($IntervalHours)) { Log 'checked recently; skipping'; exit 0 }
        }
    } catch { }
}

# ------------------------------------------------------------- the request --

try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 12288 } catch { }
} catch { }

$latestTag = ''; $releaseUrl = ''; $assets = @()

try {
    $req = [System.Net.HttpWebRequest]::Create("https://api.github.com/repos/$Repo/releases/latest")
    $req.UserAgent = 'SatpudaCoreUpdateCheck'
    $req.Accept    = 'application/vnd.github+json'
    $req.Timeout   = 20000
    $req.ReadWriteTimeout = 20000
    $resp = $req.GetResponse()
    try {
        $sr   = New-Object System.IO.StreamReader($resp.GetResponseStream())
        $body = $sr.ReadToEnd(); $sr.Dispose()
    } finally { $resp.Close() }

    $rel        = $body | ConvertFrom-Json
    $latestTag  = $rel.tag_name
    $releaseUrl = $rel.html_url
    $assets     = $rel.assets
    try { Set-Content -LiteralPath $StampFile -Value (Get-Date).ToString('o') -Encoding UTF8 } catch { }
    Log ("latest tag: " + $latestTag)
}
catch [System.Net.WebException] {
    $we   = $_.Exception
    $resp = $we.Response

    # --- failure A: no internet -------------------------------------------
    if (-not $resp) {
        Log ("network failure: " + $we.Status)
        Say "Could not reach the internet to check for a new version of $AppName.`n`n$AppName keeps working normally. It will check again the next time this computer starts." `
            $AppName ([System.Windows.Forms.MessageBoxIcon]::Information)
        exit 0
    }

    $code      = [int]$resp.StatusCode
    $remaining = $resp.Headers['X-RateLimit-Remaining']
    $reset     = $resp.Headers['X-RateLimit-Reset']

    # --- failure B: GitHub rate limit -------------------------------------
    if (($code -eq 403 -or $code -eq 429) -and $remaining -eq '0') {
        $when = 'a little while'
        if ($reset) {
            try { $when = ([DateTimeOffset]::FromUnixTimeSeconds([int64]$reset)).LocalDateTime.ToString('h:mm tt') } catch { }
        }
        Log ("rate limited until " + $when)
        try { Set-Content -LiteralPath $StampFile -Value (Get-Date).ToString('o') -Encoding UTF8 } catch { }
        Say "GitHub is temporarily limiting update checks from this internet connection.`n`nThis is not a problem with $AppName. Try again after $when." `
            $AppName ([System.Windows.Forms.MessageBoxIcon]::Information)
        exit 0
    }

    # --- failure C: private or missing release ----------------------------
    if ($code -eq 404 -or $code -eq 401) {
        Log ("release not visible: HTTP " + $code)
        # GitHub gave a definite answer, so this counts as a completed check.
        # Without stamping it, the warning below would reappear at every single
        # logon instead of at most once per interval.
        try { Set-Content -LiteralPath $StampFile -Value (Get-Date).ToString('o') -Encoding UTF8 } catch { }
        # Deliberately shown even in silent mode: if the release stops being
        # public, every shop silently stops getting updates, and nobody notices.
        [void][System.Windows.Forms.MessageBox]::Show(
            "$AppName cannot see its update page on GitHub.`n`nThe release is private or has been removed. Ask Roshan to make the release public - update checking cannot work until then.",
            $AppName, [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning)
        exit 0
    }

    if ($code -eq 403) {
        Log 'HTTP 403 (not rate limit)'
        Say "GitHub refused the update check (error 403).`n`nIf this keeps happening, ask Roshan to check that the release is public." `
            $AppName ([System.Windows.Forms.MessageBoxIcon]::Warning)
        exit 0
    }

    Log ("HTTP " + $code)
    Say "The update check did not work (error $code). $AppName keeps working normally." $AppName ([System.Windows.Forms.MessageBoxIcon]::Warning)
    exit 0
}
catch {
    Log ("unexpected: " + $_.Exception.Message)
    Say "The update check could not be completed. $AppName keeps working normally." $AppName ([System.Windows.Forms.MessageBoxIcon]::Warning)
    exit 0
}

if (-not $latestTag) {
    Log 'no tag in response'
    Say "GitHub did not report a version number. $AppName keeps working normally." $AppName ([System.Windows.Forms.MessageBoxIcon]::Warning)
    exit 0
}

# ------------------------------------------------------------- compare it --

$cmp = Compare-Version $latestTag $installed
Log ("compare " + $latestTag + " vs " + $installed + " = " + $cmp)

if ($cmp -le 0) {
    Say "$AppName is up to date.`n`nInstalled version: $installed"
    exit 0
}

$url = ''
foreach ($a in $assets) { if ($a.name -eq $InstallerAsset) { $url = $a.browser_download_url; $size = [int64]$a.size } }

if (-not $url) {
    # New version exists but no installer attached to it. Do not pretend.
    if (Ask "$AppName $latestTag is available (you have $installed).`n`nThe installer file is not attached to that release. Open the download page in your browser?") {
        Start-Process $releaseUrl
    }
    exit 0
}

if (-not (Ask "A new version of $AppName is available.`n`n    You have:   $installed`n    Available:  $latestTag`n`nDownload and install it now?")) {
    Log 'user declined'
    exit 0
}

# ------------------------------------------- download the new installer --

$dest = Join-Path $env:TEMP ("SatpudaCoreInstaller-" + $latestTag + ".exe")

$form = New-Object System.Windows.Forms.Form
$form.Text            = "$AppName update"
$form.Width           = 460
$form.Height          = 150
$form.FormBorderStyle = 'FixedDialog'
$form.StartPosition   = 'CenterScreen'
$form.MaximizeBox     = $false
$form.MinimizeBox     = $false

$label = New-Object System.Windows.Forms.Label
$label.Text = "Downloading $AppName $latestTag ..."
$label.SetBounds(15, 15, 420, 20)
$form.Controls.Add($label)

$bar = New-Object System.Windows.Forms.ProgressBar
$bar.SetBounds(15, 42, 420, 22)
$bar.Style = 'Continuous'
$form.Controls.Add($bar)

$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Cancel'
$cancel.SetBounds(355, 75, 80, 26)
$form.Controls.Add($cancel)

$wc = New-Object System.Net.WebClient
$wc.Headers.Add('User-Agent', 'SatpudaCoreUpdateCheck')
$script:failed = $null
$script:done   = $false

# DownloadFileAsync puts the transfer on a background thread; the events come
# back to this form's thread. The window stays responsive and Cancel works.
$wc.add_DownloadProgressChanged({
    param($s, $e)
    $bar.Value  = $e.ProgressPercentage
    $label.Text = "Downloading $AppName $latestTag ...  $([math]::Round($e.BytesReceived/1MB)) MB"
})
$wc.add_DownloadFileCompleted({
    param($s, $e)
    $script:done = $true
    if ($e.Error)       { $script:failed = $e.Error.Message }
    elseif ($e.Cancelled) { $script:failed = 'cancelled' }
    $form.Close()
})
$cancel.add_Click({ $wc.CancelAsync() })
$form.add_Shown({ $wc.DownloadFileAsync([Uri]$url, $dest) })

[void]$form.ShowDialog()
$wc.Dispose()

if ($script:failed -eq 'cancelled') { Log 'update download cancelled'; exit 0 }

if ($script:failed) {
    Log ("update download failed: " + $script:failed)
    if (Ask "The download did not finish.`n`nOpen the download page in your browser instead?") { Start-Process $releaseUrl }
    exit 1
}

# Floor, not a size check: the real check is the exact byte count from the API,
# just below. Keep this well under what the installer actually weighs - the
# payload is downloaded, not bundled, so SatpudaCoreInstaller.exe is only about
# a megabyte and a 1 MB floor would reject a perfectly good build for being
# 40 KB light. This only has to catch an error page saved as an .exe.
if ((-not (Test-Path -LiteralPath $dest)) -or ((Get-Item -LiteralPath $dest).Length -lt 262144)) {
    Log 'downloaded updater too small'
    if (Ask "The downloaded file does not look right.`n`nOpen the download page in your browser instead?") { Start-Process $releaseUrl }
    exit 1
}
if ($size -and (Get-Item -LiteralPath $dest).Length -ne $size) {
    Log 'downloaded updater wrong size'
    if (Ask "The downloaded file is incomplete.`n`nOpen the download page in your browser instead?") { Start-Process $releaseUrl }
    exit 1
}

Log ("launching " + $dest)
Start-Process -FilePath $dest
exit 0
