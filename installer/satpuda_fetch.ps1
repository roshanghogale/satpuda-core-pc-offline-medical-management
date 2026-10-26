<#
  satpuda_fetch.ps1 - payload worker for the Satpuda Core installer.

  Runs as a SEPARATE PROCESS launched by the Inno Setup script, so all the slow
  work (a ~122 MB download and a ~895 MB extraction) happens off the installer's
  UI thread. The installer stays responsive and its Stop button keeps working.

  Talks to the installer over stdout, one ASCII line per message:
     PID|<n>            our process id, first line, so the installer can kill us
     P|<0-100>|<text>   progress
     S|<text>           step changed
     W|<text>           warning (install continues)
     E|<CODE>|<text>    fatal, CODE lets the installer show a nicer sentence
     D|<text>           debug, goes to the install log only
     UNVERIFIED|        -LocalZip only: no published checksum could be read, so
                        the chosen zip was checked by its contents alone
     AV|<names>         the antivirus products Windows knows of (for AVBLOCK)
     PORT|<text>        the app's local port 8765 is taken or reserved; the
                        installer says so on its last page
     OK|                finished

  Codes added for other PCs (Oct 2026): DISK (not enough free space), LONGPATH
  (install folder too deep for Windows' 260-character limit), AVBLOCK / AVZIP
  (antivirus removed files right after they were written), CLOCK (the PC's date
  is far off, so HTTPS certificates fail), TLS (HTTPS trust failed although the
  clock is right), DOTNET (System.IO.Compression missing), INTERNAL (anything
  unexpected - the message and line number, instead of a silent exit 1). The
  installer starts this worker with -Command and dot-sources it, so a Group
  Policy execution policy (AllSigned / Restricted) cannot block it; see
  WorkerParams in SatpudaCore.iss. Under Constrained Language Mode the
  installer's own bootstrap answers E|CLM| before this file is read.

  Exit codes: 0 = success, 2 = failed, 3 = cancelled by the user.

  Everything written to stdout is plain ASCII on purpose: the pipe between
  PowerShell and Inno Setup is not a reliable place for non-ASCII text. The
  friendly, translated wording lives in the .iss file.

  WHY THE DOWNLOAD IS SEGMENTED
  -----------------------------
  Measured on the shop's own line against the real 121.9 MB release asset:

      1 connection    0.15 - 0.19 MB/s      (11 minutes)
      4 connections   0.53 - 0.75 MB/s
      8 connections   0.81 MB/s
     16 connections   1.41 - 1.67 MB/s      (about 80 seconds)
     24 connections   2.28 MB/s
     32 connections   2.96 MB/s

  The link itself is 100 Mbps, so nothing about the wire is slow. A single TCP
  stream to GitHub's release-asset CDN is what is slow, and the only cure that
  works is more streams. This is NOT the PowerShell progress-bar problem: this
  script has never used Invoke-WebRequest, and $ProgressPreference has been
  'SilentlyContinue' throughout. Setting it changes nothing here; it is left in
  place because it costs nothing.

  Each segment lands in its own .part file whose LENGTH is its resume state, so
  a cancelled or crashed run costs nothing but the bytes still in flight.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $AppRoot,      # install dir; we manage <AppRoot>\app
    [Parameter(Mandatory = $true)] [string] $CacheDir,     # resumable download cache (survives a failed run)
    [Parameter(Mandatory = $true)] [string] $CancelFile,   # installer creates this to ask us to stop
    [string] $Repo      = 'roshanghogale/exes-for-satpuda-core',
    [string] $Tag       = 'v1.0.1',
    [string] $AssetName = 'SatpudaCore_Desktop_Win10.zip',
    [string] $DirectUrl = '',                              # used when the API is unreachable/rate limited
    [string] $SentinelExe = 'SatpudaCore_Desktop.exe',     # must exist after extraction or we refuse to swap
    [string] $LogFile   = '',
    [int]    $Segments  = 16,                              # parallel range connections
    [switch] $Repair,                                      # distrust the cached zip; this run exists to fix corruption
    [switch] $CheckOnly,                                   # just report the newest published tag, then quit
    [string] $OutFile   = '',                              # where -CheckOnly writes its answer
    # The owner publishes new builds INTO THE SAME TAG, and "gh release upload
    # --clobber" deletes the old asset about a minute before the new one has
    # finished uploading. For that long the release has no zip (or no .sha256).
    # A run that lands in that window waits for GitHub instead of failing.
    [int]    $UpdateWaitSecs = 180,                        # how long to wait for a release that is being re-published
    [int]    $UpdatePollSecs = 15,                         # how often to look again while waiting
    # A zip the shopkeeper downloaded in the browser, used instead of downloading
    # (a PC whose antivirus breaks this worker's downloads while the browser works).
    # Checked against the release's published .sha256 when it can be read, then
    # installed by exactly the same extract / swap / record steps as a download.
    [string] $LocalZip  = '',
    [string] $ApiBase   = 'https://api.github.com',        # only ever changed by the tests (a local fake GitHub)
    # Antivirus that deletes a file it dislikes does it a moment AFTER the file
    # is written (Net Protector, Oct 2026). The unpacked files are counted again
    # after this many seconds, before anything is swapped in.
    [int]    $AvSettleSecs = 5,
    # A plain-HTTP address whose Date header says what time it really is, read
    # only after an HTTPS failure (a PC whose clock is years off fails every
    # certificate check). Changed only by the tests.
    [string] $ClockUrl  = 'http://github.com/',
    [int]    $EnginePort = 8765,                           # the app's own local port, checked and reported
    [int64]  $AssumeFreeMB = -1                            # tests only: pretend every drive has this much free
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # harmless here; see the header note

if ($Segments -lt 1)  { $Segments = 1 }
if ($Segments -gt 32) { $Segments = 32 }
if ($UpdateWaitSecs -lt 0)   { $UpdateWaitSecs = 0 }
if ($UpdateWaitSecs -gt 900) { $UpdateWaitSecs = 900 }
if ($UpdatePollSecs -lt 1)   { $UpdatePollSecs = 1 }
if ($AvSettleSecs -lt 0)     { $AvSettleSecs = 0 }
if ($AvSettleSecs -gt 60)    { $AvSettleSecs = 60 }
$ApiBase = $ApiBase.TrimEnd('/')

# GitHub is TLS 1.2+ only. PowerShell 5.1 inherits the .NET default, which on
# an un-updated Windows 10 (no SchUseStrongCrypto) is still SSL3/TLS 1.0, so
# every call dies before it starts. Set by NUMBER, not by name: on a .NET whose
# enum has no Tls12 member the name throws, while 3072 still switches TLS 1.2 on
# wherever the OS has it. TLS 1.3 (12288) is added where the OS knows it.
# Returns '' or why it could not be done.
function Set-StrongTls {
    $why = ''
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType](3072 -bor 768 -bor 192)
    } catch {
        $why = $_.Exception.Message
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]3072; $why = '' } catch { }
    }
    # TLS 1.3 only where Windows' own SChannel has a TLS 1.3 client (Windows 11
    # / Server 2022, build 20348+). Asking for it on Windows 10 gains nothing
    # (GitHub speaks 1.2) and can make the handshake itself fail there - the
    # owner's PC is Windows 11, so it would never show up on it.
    $build = 0
    try { $build = [Environment]::OSVersion.Version.Build } catch { }
    if ($build -ge 20348) {
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]12288 } catch { }
    }
    # A shop behind an office proxy that wants the Windows login (HTTP 407):
    # .NET does not send it unless asked to. Harmless when there is no proxy.
    try {
        $px = [Net.WebRequest]::DefaultWebProxy
        if ($px) { $px.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials }
    } catch { }
    return $why
}

# --------------------------------------------------------------- CheckOnly --
#
# The maintenance page asks "is there anything newer on GitHub?" before the
# shopkeeper picks Update, Repair or Remove. That is one short API call and it
# must never hold the wizard open: a tight timeout, no retries, and silence on
# failure - an offline shop still gets a working maintenance page.
if ($CheckOnly) {
    $tag = ''
    [void](Set-StrongTls)
    $latestRel = $null
    try {
        $r = [System.Net.HttpWebRequest]::Create("$ApiBase/repos/$Repo/releases/latest")
        $r.UserAgent        = 'SatpudaCoreInstaller'
        $r.Accept           = 'application/vnd.github+json'
        $r.Timeout          = 4000
        $r.ReadWriteTimeout = 4000
        $resp = $r.GetResponse()
        try {
            $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
            try { $latestRel = $sr.ReadToEnd() | ConvertFrom-Json; $tag = $latestRel.tag_name } finally { $sr.Dispose() }
        } finally { $resp.Close() }
    } catch { $tag = '' }

    # The checksum of the LATEST release's zip, from its .sha256 sidecar. The
    # owner republishes builds into the same tag, so "same tag" no longer means
    # "same program": the maintenance page compares this with the sha256 in the
    # install marker and offers "Update to the newest build" when they differ.
    # Same 4-second ceiling and the same silence on failure: '' means "cannot
    # tell", and the page then behaves exactly as it did before.
    $sha = ''
    try {
        $shaUrl = ''
        if ($latestRel) {
            foreach ($a in @($latestRel.assets)) {
                if ($a.name -eq ($AssetName + '.sha256')) { $shaUrl = [string]$a.browser_download_url }
            }
        }
        if ($shaUrl) {
            # The checksum sits behind a redirect to GitHub's file host, which can take
            # ~21 s when an ISP cannot reach one of GitHub's addresses (25 Oct 2026), so it gets room to fail over.
            $r = [System.Net.HttpWebRequest]::Create($shaUrl)
            $r.UserAgent        = 'SatpudaCoreInstaller'
            $r.Timeout          = 25000
            $r.ReadWriteTimeout = 25000
            $resp = $r.GetResponse()
            try {
                $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
                try { $txt = $sr.ReadToEnd() } finally { $sr.Dispose() }
            } finally { $resp.Close() }
            if ($txt -match '([0-9a-fA-F]{64})') { $sha = $Matches[1].ToLower() }
        }
    } catch { $sha = '' }

    # The newest PRE-RELEASE, for the administrator's "install the test
    # version" choice. releases/latest never returns one, so this is a second
    # call, to the release list. Same 4-second ceiling, same silence on
    # failure. A draft is never chosen, and neither is a pre-release that does
    # not carry the asset the installer downloads. The tag goes onto a command
    # line and into a URL, so anything that is not a plain v1.2.3[-beta.4] tag
    # is dropped here as well as in the .iss (IsSafeTag).
    #
    # The list is NOT newest first in practice: GitHub orders it by the tag's
    # commit date, and every beta tag here points at the same commit, so a just
    # published beta.10 sat below beta.6 (20 Oct 2026). So every qualifying
    # pre-release is read and the HIGHEST version wins: v1.1.0-beta.10 over
    # v1.1.0-beta.9, and a final v1.1.0 over any v1.1.0-beta.N.
    $pre = ''
    $preKey = $null
    try {
        $r = [System.Net.HttpWebRequest]::Create("$ApiBase/repos/$Repo/releases?per_page=100")
        $r.UserAgent        = 'SatpudaCoreInstaller'
        $r.Accept           = 'application/vnd.github+json'
        $r.Timeout          = 4000
        $r.ReadWriteTimeout = 4000
        $resp = $r.GetResponse()
        try {
            $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
            try { $list = $sr.ReadToEnd() | ConvertFrom-Json } finally { $sr.Dispose() }
        } finally { $resp.Close() }
        foreach ($rel in @($list)) {
            if ($rel.draft -ne $false)     { continue }
            if ($rel.prerelease -ne $true) { continue }
            $hasAsset = $false
            foreach ($a in @($rel.assets)) { if ($a.name -eq $AssetName) { $hasAsset = $true; break } }
            if (-not $hasAsset) { continue }
            $t = [string]$rel.tag_name
            if (($t.Length -gt 40) -or ($t -cnotmatch '^v[0-9]+(\.[0-9]+)*(-[A-Za-z0-9]+(\.[A-Za-z0-9]+)*)?$')) { continue }
            if ($t -notmatch '^v([0-9]+)\.([0-9]+)\.([0-9]+)(?:-[A-Za-z]+\.?([0-9]+))?') { continue }
            # major, minor, patch, then a final release above all its betas, then the beta number
            $isFinal = if ($Matches[4]) { 0 } else { 1 }
            $betaNo  = if ($Matches[4]) { [int]$Matches[4] } else { 0 }
            $key = @([int]$Matches[1], [int]$Matches[2], [int]$Matches[3], $isFinal, $betaNo)
            $better = $false
            if ($null -eq $preKey) { $better = $true }
            else {
                for ($i = 0; $i -lt 5; $i++) {
                    if ($key[$i] -gt $preKey[$i]) { $better = $true; break }
                    if ($key[$i] -lt $preKey[$i]) { break }
                }
            }
            if ($better) { $pre = $t; $preKey = $key }
        }
    } catch { $pre = '' }

    if ($OutFile) {
        try { [System.IO.File]::WriteAllText($OutFile, ("LATEST|" + $tag + "`r`nPRE|" + $pre + "`r`nSHA|" + $sha)) } catch { }
    }
    [Console]::Out.WriteLine("LATEST|" + $tag)
    [Console]::Out.WriteLine("PRE|" + $pre)
    [Console]::Out.WriteLine("SHA|" + $sha)
    exit 0
}

# ---------------------------------------------------------------- plumbing --

function Emit([string]$line) {
    # [Console]::Out + explicit Flush: Write-Output buffers, and a buffered
    # progress line is a progress bar that does not move.
    #
    # ASCII only on the pipe, enforced here rather than hoped for: a path on a
    # PC whose Windows user name is in Marathi or Hindi (C:\Users\<Devanagari>)
    # reaches the installer in the console code page as garbage, and a stray
    # byte there can split one message into two. The install log below gets
    # the real text (UTF-8).
    $ascii = [regex]::Replace([string]$line, '[^\x20-\x7E]', '?')
    [Console]::Out.WriteLine($ascii)
    [Console]::Out.Flush()
    if ($script:LogWriter) {
        try { $script:LogWriter.WriteLine(('{0:HH:mm:ss}  {1}' -f (Get-Date), $line)); $script:LogWriter.Flush() } catch { }
    }
}
# Anything this script did not expect. Before this, an unexpected error ended
# PowerShell with exit code 1 and nothing on the pipe, and the installer could
# only say "The installation could not be completed" - with no clue in the log.
# Now it is a failure like any other, with the message and the line it came
# from. (A trap covers the whole scope it is in, wherever it is written.)
trap {
    $m = ''
    try { $m = [string]$_.Exception.Message + ' (line ' + $_.InvocationInfo.ScriptLineNumber + ')' } catch { $m = 'unexpected error' }
    try { Emit ('E|INTERNAL|' + ($m -replace '[\r\n|]+', ' ')) } catch { [Console]::Out.WriteLine('E|INTERNAL|unexpected error') }
    try { Exit-Clean 2 } catch { exit 2 }
}

function Step([string]$t)            { Write-ConnectNotes; Emit ("S|" + $t) }
function Warn([string]$t)            { Emit ("W|" + $t) }
function Dbg ([string]$t)            { Emit ("D|" + $t) }
function Fail([string]$code,[string]$t) { Write-ConnectNotes; Emit ("E|" + $code + "|" + $t); Exit-Clean 2 }

# What the fast-connect helper (see "connecting quickly" below) noticed, into
# the log. It runs on .NET's own threads, so it queues notes and this - on the
# main thread - writes them out.
$script:FastConnect = $false
function Write-ConnectNotes {
    if (-not $script:FastConnect) { return }
    try { foreach ($n in [SatpudaNet.FastConnect]::TakeNotes()) { Dbg ('connect: ' + $n) } } catch { }
}

$script:LastPct = -1
function Progress([int]$pct, [string]$t) {
    Write-ConnectNotes
    if ($pct -lt 0) { $pct = 0 } elseif ($pct -gt 100) { $pct = 100 }
    if ($pct -ne $script:LastPct) {          # only on change: ExecAndLogOutput caps total output
        $script:LastPct = $pct
        Emit ("P|" + $pct + "|" + $t)
    }
}
# The same line even when the percentage has not moved - for a countdown, whose
# text is the only thing that changes.
function Say-Progress([int]$pct, [string]$t) {
    if ($pct -lt 0) { $pct = 0 } elseif ($pct -gt 100) { $pct = 100 }
    $script:LastPct = $pct
    Emit ("P|" + $pct + "|" + $t)
}

# ------------------------------------------------------------------ cancel --
#
# The previous version polled the cancel flag ONLY from inside the network read
# loop and once every 25 zip entries. Both of those can block for a long time:
# a stalled socket sits in Read() for the whole ReadWriteTimeout, and
# Stream.CopyTo() on one big file inside the payload never comes up for air at
# all. That is what made Cancel look dead. Everything slow in this script now
# goes through Test-Cancel, and every wait goes through Wait-Cancellable.
#
# Once the flag is seen it is remembered: the installer deletes the flag file
# when it gives up waiting for us, and we must not "un-cancel" ourselves.

$script:CancelSeen  = $false
$script:LastPoll    = [DateTime]::MinValue
function Test-Cancel {
    if ($script:CancelSeen) { return $true }
    if (([DateTime]::UtcNow - $script:LastPoll).TotalMilliseconds -lt 250) { return $false }
    $script:LastPoll = [DateTime]::UtcNow
    if ([System.IO.File]::Exists($CancelFile)) { $script:CancelSeen = $true }
    return $script:CancelSeen
}
function Assert-NotCancelled { if (Test-Cancel) { Emit 'D|cancel flag seen'; Exit-Clean 3 } }

# Sleep that can be interrupted. Never sleep longer than 200 ms in one go.
function Wait-Cancellable([int]$milliseconds) {
    $end = [DateTime]::UtcNow.AddMilliseconds($milliseconds)
    while ([DateTime]::UtcNow -lt $end) {
        if (Test-Cancel) { return $true }
        $left = ($end - [DateTime]::UtcNow).TotalMilliseconds
        if ($left -le 0) { break }
        Start-Sleep -Milliseconds ([Math]::Min(200, [int]$left))
    }
    return (Test-Cancel)
}

function Exit-Clean([int]$code) {
    # A failure found while the package is still open (it is judged before it
    # is unpacked): let go of it first, or our copy of a chosen zip cannot be
    # deleted below.
    try { if ($script:zip) { $script:zip.Dispose(); $script:zip = $null } } catch { }
    if ($code -ne 0) {
        # A staging folder is never a valid install. Leaving one behind is what
        # makes a broken install look finished, so it always goes.
        try { if ($script:Staging -and (Test-Path -LiteralPath $script:Staging)) { [void](Remove-Tree $script:Staging) } } catch { }
        # Our copy of a chosen zip (-LocalZip). Never the shopkeeper's own file.
        try { if ($script:LocalCopy -and (Test-Path -LiteralPath $script:LocalCopy)) { Remove-Item -LiteralPath $script:LocalCopy -Force } } catch { }
        # If we created the install folder and then failed, do not leave an
        # empty "Satpuda Core" sitting in Programs looking like a half install.
        try {
            if ($AppRoot -and (Test-Path -LiteralPath $AppRoot) -and
                -not (Get-ChildItem -LiteralPath $AppRoot -Force -ErrorAction SilentlyContinue)) {
                [System.IO.Directory]::Delete((Get-LongPath $AppRoot), $false)
            }
        } catch { }
    }
    if ($script:LogWriter) { try { $script:LogWriter.Flush(); $script:LogWriter.Dispose() } catch { } }
    exit $code
}

# Long-path form. LongPathsEnabled is 0 on plenty of shop PCs (it is 0 on the
# build machine), so we do not ask the OS to be nice - we prefix every path.
function Get-LongPath([string]$p) {
    if ([string]::IsNullOrEmpty($p))    { return $p }
    if ($p.StartsWith('\\?\'))          { return $p }
    if ($p.StartsWith('\\'))            { return '\\?\UNC\' + $p.Substring(2) }
    return '\\?\' + $p
}

function Remove-Tree([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $true }
    for ($i = 1; $i -le 3; $i++) {
        try { [System.IO.Directory]::Delete((Get-LongPath $path), $true); return $true }
        catch {
            if ($i -eq 3) { Dbg ("could not delete " + $path + ": " + $_.Exception.Message); return $false }
            Start-Sleep -Milliseconds 400
        }
    }
    return $false
}

function New-Dir([string]$path) { [void][System.IO.Directory]::CreateDirectory((Get-LongPath $path)) }

# Schedule a delete for the next reboot - the escape hatch when a file is
# locked by a process we are not allowed to kill.
$script:MoveFileExDefined = $false
function Remove-OnReboot([string]$path) {
    try {
        if (-not $script:MoveFileExDefined) {
            Add-Type -Namespace SatpudaNative -Name Win32 -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true, CharSet=System.Runtime.InteropServices.CharSet.Unicode)]
public static extern bool MoveFileEx(string lpExistingFileName, string lpNewFileName, int dwFlags);
'@
            $script:MoveFileExDefined = $true
        }
        [void][SatpudaNative.Win32]::MoveFileEx($path, $null, 4)  # MOVEFILE_DELAY_UNTIL_REBOOT
    } catch { Dbg ("MoveFileEx failed: " + $_.Exception.Message) }
}

# ----------------------------------------------------- compatibility check --
#
# The first block of every run in install.log: what this PC is, in one place,
# so a shop can send ONE file and the cause is in it. Every probe is on its own
# and silent on failure - this report must never be why an install fails - and
# none of it changes anything on the PC.

# A registry value from a given view (Registry64 reads the real 64-bit keys
# even from a 32-bit PowerShell). $null when it is not there.
function Get-RegValue([string]$hive, [string]$view, [string]$sub, [string]$name) {
    try {
        $v = [Microsoft.Win32.RegistryView]::$view
        if (($view -eq 'Registry64') -and -not [Environment]::Is64BitOperatingSystem) { $v = [Microsoft.Win32.RegistryView]::Default }
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::$hive, $v)
        try {
            $k = $base.OpenSubKey($sub)
            if ($k) { try { return $k.GetValue($name) } finally { $k.Close() } }
        } finally { $base.Close() }
    } catch { }
    return $null
}

# The real System32, also from a 32-bit PowerShell on 64-bit Windows (where
# "System32" is quietly SysWOW64).
function Get-NativeSystem32 {
    $win = $env:windir
    if (-not $win) { $win = 'C:\Windows' }
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) { return (Join-Path $win 'Sysnative') }
    return (Join-Path $win 'System32')
}

# Free megabytes on the drive that holds $path; -1 when that cannot be told
# (a network path, a drive that is not there yet).
function Get-FreeMB([string]$path) {
    if ($AssumeFreeMB -ge 0) { return [int64]$AssumeFreeMB }
    try {
        $full = [System.IO.Path]::GetFullPath($path)
        $root = [System.IO.Path]::GetPathRoot($full)
        if ((-not $root) -or $root.StartsWith('\\')) { return [int64]-1 }
        return [int64][Math]::Floor((New-Object System.IO.DriveInfo($root)).AvailableFreeSpace / 1MB)
    } catch { return [int64]-1 }
}

function Get-DriveLabel([string]$path) {
    try { return ([System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($path))).TrimEnd('\') } catch { return $path }
}

# Fails with DISK, before the work starts, when the drive of $path cannot hold
# $needBytes more. Before this a full disk showed up half way through as a
# write error ("could not be unpacked") or a short download.
function Assert-FreeSpace([string]$path, [int64]$needBytes, [string]$what) {
    $free = Get-FreeMB $path
    $need = [int64][Math]::Ceiling($needBytes / 1MB)
    $drive = Get-DriveLabel $path
    Dbg ('free space on ' + $drive + ' for ' + $what + ': ' + $free + ' MB, needs about ' + $need + ' MB')
    if (($free -ge 0) -and ($free -lt $need)) {
        Fail 'DISK' ('Not enough free space on drive ' + $drive + ' to ' + $what + ': about ' + $need +
                     ' MB is needed and only ' + $free + ' MB is free.')
    }
}

# The antivirus products Windows Security Center knows of, as objects with a
# Name and a State. Read once; an empty list on Windows Server (no Security
# Center) or when WMI is broken.
$script:AvList = $null
function Get-AvList {
    if ($null -ne $script:AvList) { return $script:AvList }
    $list = @()
    try {
        $items = $null
        try {
            $items = Get-CimInstance -Namespace 'root/SecurityCenter2' -ClassName 'AntiVirusProduct' -OperationTimeoutSec 10 -ErrorAction Stop
        } catch {
            $items = Get-WmiObject -Namespace 'root\SecurityCenter2' -Class 'AntiVirusProduct' -ErrorAction Stop
        }
        foreach ($i in @($items)) {
            if (-not $i) { continue }
            $n = [string]$i.displayName
            if (-not $n) { continue }
            $st = 0
            try { $st = [int]$i.productState } catch { }
            $list += [pscustomobject]@{ Name = $n; State = $st }
        }
    } catch { Dbg ('compat: antivirus list could not be read: ' + $_.Exception.Message) }
    $script:AvList = $list
    return $list
}

# Product names for a sentence: other products before Windows' own, which is
# switched to passive mode as soon as another antivirus is installed.
function Get-AvNamesText {
    $own = @(); $other = @()
    foreach ($a in @(Get-AvList)) {
        if ($a.Name -match 'Windows Defender|Microsoft Defender') { $own += $a.Name } else { $other += $a.Name }
    }
    $all = @($other) + @($own)
    return (($all | Select-Object -Unique) -join ', ')
}

# Who holds the app's own local port, and whether Windows has reserved it.
# Returns sentences for PORT| lines; none when all is well. The app's own
# engine on that port is not a problem (it is just running).
function Get-PortProblems {
    $out = @()
    $pids = @()
    $asked = $false
    try {
        if (Get-Command -Name 'Get-NetTCPConnection' -ErrorAction SilentlyContinue) {
            $asked = $true
            foreach ($c in @(Get-NetTCPConnection -LocalPort $EnginePort -State Listen -ErrorAction SilentlyContinue)) {
                if ($c -and $c.OwningProcess) { $pids += [int]$c.OwningProcess }
            }
        }
    } catch { $asked = $false }
    if (-not $asked) {
        # netstat's state column is translated on some Windows languages, so a
        # listener is told by its remote address instead (0.0.0.0:0 / [::]:0).
        try {
            foreach ($l in @(& netstat.exe -ano 2>$null)) {
                $parts = ([string]$l).Trim() -split '\s+'
                if (($parts.Count -ge 5) -and ($parts[0] -eq 'TCP') -and ($parts[1] -match (':' + $EnginePort + '$')) -and
                    ($parts[2] -match ':0$') -and ($parts[-1] -match '^\d+$')) { $pids += [int]$parts[-1] }
            }
        } catch { }
    }
    foreach ($p in ($pids | Select-Object -Unique)) {
        $name = '?'
        try { $name = (Get-Process -Id $p -ErrorAction Stop).ProcessName } catch { }
        Dbg ('compat: port ' + $EnginePort + ' is listened on by ' + $name + ' (pid ' + $p + ')')
        if ($name -notmatch '^(SatpudaEngine|SatpudaCore_Desktop)$') {
            $out += ('Port ' + $EnginePort + ', which Satpuda Core uses inside this computer, is already in use by ' +
                     $name + '.exe (process ' + $p + '). Satpuda Core may not start, or may close that program when it starts.')
        }
    }
    try {
        foreach ($l in @(& netsh.exe interface ipv4 show excludedportrange protocol=tcp 2>$null)) {
            if ([string]$l -match '^\s*(\d+)\s+(\d+)') {
                $a = [int]$Matches[1]; $b = [int]$Matches[2]
                if (($EnginePort -ge $a) -and ($EnginePort -le $b)) {
                    Dbg ('compat: port ' + $EnginePort + ' is inside the reserved range ' + $a + '-' + $b)
                    $out += ('Windows has reserved ports ' + $a + '-' + $b + ' (usually for Hyper-V, WSL or Docker), and Satpuda Core needs port ' +
                             $EnginePort + ' in that range. Restart the computer; if it stays, an administrator can run "net stop winnat" and "net start winnat".')
                }
            }
        }
    } catch { }
    return $out
}

function Get-DotNetText {
    $rel = Get-RegValue 'LocalMachine' 'Registry64' 'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' 'Release'
    if ($null -eq $rel) { return 'no .NET 4.5+ found' }
    $r = [int]$rel
    $name = if ($r -ge 533320) { '4.8.1' } elseif ($r -ge 528040) { '4.8' } elseif ($r -ge 461808) { '4.7.2' } elseif ($r -ge 461308) { '4.7.1' } elseif ($r -ge 460798) { '4.7' } elseif ($r -ge 394802) { '4.6.2' } elseif ($r -ge 394254) { '4.6.1' } elseif ($r -ge 393295) { '4.6' } elseif ($r -ge 379893) { '4.5.2' } else { '4.5.x' }
    return ($name + ' (release ' + $r + ')')
}

# WebView2 Runtime, where the Evergreen installer records it: per machine in
# the 32-bit view, per user in HKCU. '' when absent. A version whose
# msedgewebview2.exe is not on the disk is reported as broken.
function Get-WebView2Text {
    $g = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    $found = @()
    foreach ($s in @(@('LocalMachine', 'Registry32', 'machine'), @('LocalMachine', 'Registry64', 'machine'),
                     @('CurrentUser', 'Registry64', 'user'), @('CurrentUser', 'Registry32', 'user'))) {
        $pv = Get-RegValue $s[0] $s[1] ('SOFTWARE\Microsoft\EdgeUpdate\Clients\' + $g) 'pv'
        if ($pv -and ([string]$pv -ne '0.0.0.0')) {
            $root = if ($s[2] -eq 'user') { $env:LOCALAPPDATA } else { ${env:ProgramFiles(x86)} }
            if (-not $root) { $root = $env:ProgramFiles }
            $exe = Join-Path $root ('Microsoft\EdgeWebView\Application\' + $pv + '\msedgewebview2.exe')
            $ok = Test-Path -LiteralPath $exe
            $found += ([string]$pv + ' (' + $s[2] + $(if ($ok) { '' } else { ', BROKEN: msedgewebview2.exe missing' }) + ')')
        }
    }
    if (-not $found) { return 'NOT INSTALLED' }
    return (($found | Select-Object -Unique) -join '; ')
}

function Write-CompatReport {
    Dbg ('compat: ===== compatibility check, worker started ' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' =====')
    $nt = 'SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    try {
        $build = [string](Get-RegValue 'LocalMachine' 'Registry64' $nt 'CurrentBuildNumber')
        $ubr   = [string](Get-RegValue 'LocalMachine' 'Registry64' $nt 'UBR')
        $prod  = [string](Get-RegValue 'LocalMachine' 'Registry64' $nt 'ProductName')
        $disp  = [string](Get-RegValue 'LocalMachine' 'Registry64' $nt 'DisplayVersion')
        if (-not $disp) { $disp = [string](Get-RegValue 'LocalMachine' 'Registry64' $nt 'ReleaseId') }
        $ed    = [string](Get-RegValue 'LocalMachine' 'Registry64' $nt 'EditionID')
        # ProductName still says "Windows 10" on Windows 11.
        if (($build -as [int]) -ge 22000) { $prod = $prod -replace 'Windows 10', 'Windows 11' }
        Dbg ('compat: windows: ' + $prod + ' ' + $disp + ', build ' + $build + '.' + $ubr + ', edition ' + $ed + ' (' + [Environment]::OSVersion.VersionString + ')')
    } catch { }
    try {
        Dbg ('compat: arch: OS 64-bit=' + [Environment]::Is64BitOperatingSystem + ', PowerShell 64-bit=' + [Environment]::Is64BitProcess +
             ', PROCESSOR_ARCHITECTURE=' + $env:PROCESSOR_ARCHITECTURE + $(if ($env:PROCESSOR_ARCHITEW6432) { ', ARCHITEW6432=' + $env:PROCESSOR_ARCHITEW6432 } else { '' }))
    } catch { }
    try {
        $pol = (Get-ExecutionPolicy -List | ForEach-Object { [string]$_.Scope + '=' + [string]$_.ExecutionPolicy }) -join ' '
        Dbg ('compat: powershell ' + $PSVersionTable.PSVersion + ', CLR ' + $PSVersionTable.CLRVersion + ', language ' +
             $ExecutionContext.SessionState.LanguageMode + ', policy ' + $pol)
    } catch { }
    try { Dbg ('compat: .NET ' + (Get-DotNetText)) } catch { }
    try {
        $k = 'SOFTWARE\Microsoft\.NETFramework\v4.0.30319'
        $sc = 'SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\TLS 1.2\Client'
        Dbg ('compat: tls: in use=' + [Net.ServicePointManager]::SecurityProtocol +
             ', SchUseStrongCrypto=' + (Get-RegValue 'LocalMachine' 'Registry64' $k 'SchUseStrongCrypto') +
             '/' + (Get-RegValue 'LocalMachine' 'Registry32' $k 'SchUseStrongCrypto') +
             ', SystemDefaultTlsVersions=' + (Get-RegValue 'LocalMachine' 'Registry64' $k 'SystemDefaultTlsVersions') +
             ', SCHANNEL TLS1.2 client Enabled=' + (Get-RegValue 'LocalMachine' 'Registry64' $sc 'Enabled') +
             ' DisabledByDefault=' + (Get-RegValue 'LocalMachine' 'Registry64' $sc 'DisabledByDefault'))
    } catch { }
    try {
        $now = Get-Date
        Dbg ('compat: clock: local ' + $now.ToString('yyyy-MM-dd HH:mm:ss') + ', UTC ' + $now.ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') +
             ', zone ' + [TimeZoneInfo]::Local.Id)
    } catch { }
    try {
        $prof = [string]$env:USERPROFILE
        Dbg ('compat: user profile ' + $prof + ' (space=' + ($prof -match ' ') + ', non-ASCII=' + ($prof -match '[^\x00-\x7F]') + ')' +
             ', LOCALAPPDATA=' + $env:LOCALAPPDATA + ', TEMP=' + $env:TEMP + $(if ($env:OneDrive) { ', OneDrive=' + $env:OneDrive } else { '' }))
        $desk = Get-RegValue 'CurrentUser' 'Registry64' 'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders' 'Desktop'
        Dbg ('compat: desktop folder ' + $desk)
        Dbg ('compat: install folder ' + $AppRoot + ' (' + $AppRoot.Length + ' chars), cache ' + $CacheDir +
             ', LongPathsEnabled=' + (Get-RegValue 'LocalMachine' 'Registry64' 'SYSTEM\CurrentControlSet\Control\FileSystem' 'LongPathsEnabled'))
    } catch { }
    try {
        Dbg ('compat: free space: ' + (Get-DriveLabel $AppRoot) + ' ' + (Get-FreeMB $AppRoot) + ' MB (install), ' +
             (Get-DriveLabel $CacheDir) + ' ' + (Get-FreeMB $CacheDir) + ' MB (download cache)')
    } catch { }
    try { Dbg ('compat: WebView2 Runtime ' + (Get-WebView2Text)) } catch { }
    try {
        $vc = Get-RegValue 'LocalMachine' 'Registry64' 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' 'Version'
        $mfc = Test-Path -LiteralPath (Join-Path (Get-NativeSystem32) 'mfc140u.dll')
        Dbg ('compat: VC++ 2015-2022 x64 runtime ' + $(if ($vc) { $vc } else { 'not registered' }) + ', mfc140u.dll in System32=' + $mfc)
    } catch { }
    try {
        $av = @(Get-AvList)
        if ($av.Count -eq 0) { Dbg 'compat: antivirus: none reported by Windows Security Center' }
        foreach ($a in $av) { Dbg ('compat: antivirus: ' + $a.Name + ' (state 0x' + ('{0:X6}' -f $a.State) + ')') }
        $cfa = Get-RegValue 'LocalMachine' 'Registry64' 'SOFTWARE\Microsoft\Windows Defender\Windows Defender Exploit Guard\Controlled Folder Access' 'EnableControlledFolderAccess'
        if ($null -ne $cfa) { Dbg ('compat: Controlled Folder Access=' + $cfa) }
        $names = Get-AvNamesText
        if ($names) { Emit ('AV|' + $names) }
    } catch { }
    try {
        $is = 'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        $pe = Get-RegValue 'CurrentUser' 'Registry64' $is 'ProxyEnable'
        $ps = Get-RegValue 'CurrentUser' 'Registry64' $is 'ProxyServer'
        $pac = Get-RegValue 'CurrentUser' 'Registry64' $is 'AutoConfigURL'
        $via = 'direct'
        try {
            $px = [Net.WebRequest]::DefaultWebProxy
            if ($px) { $u = $px.GetProxy([Uri]'https://api.github.com/'); if ($u -and ($u.Host -ne 'api.github.com')) { $via = [string]$u } }
        } catch { }
        $wh = ''
        try { $wh = ((@(& netsh.exe winhttp show proxy 2>$null) | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ }) -join ' / ') } catch { }
        Dbg ('compat: proxy: ProxyEnable=' + $pe + ' ProxyServer=' + $ps + ' AutoConfigURL=' + $pac + ', .NET uses ' + $via + ', WinHTTP: ' + $wh)
    } catch { }
    try {
        $running = @(Get-Process -Name 'SatpudaCore_Desktop', 'SatpudaEngine' -ErrorAction SilentlyContinue | ForEach-Object { $_.ProcessName + '(' + $_.Id + ')' })
        if ($running) { Dbg ('compat: running now: ' + ($running -join ', ')) }
    } catch { }
    try { foreach ($p in @(Get-PortProblems)) { Emit ('PORT|' + $p) } } catch { }
    Dbg 'compat: ===== end of compatibility check ====='
}

# ------------------------------------------------------------------- setup --

if ($LogFile) {
    try {
        New-Dir ([System.IO.Path]::GetDirectoryName($LogFile))
        $script:LogWriter = New-Object System.IO.StreamWriter($LogFile, $true, (New-Object System.Text.UTF8Encoding($false)))
    } catch { $script:LogWriter = $null }
}

# First line out, before anything can go wrong: if this worker ever stops
# answering, the installer kills this pid (and its children) rather than
# leaving a 122 MB download running behind a closed wizard.
Emit ("PID|" + $PID)

# GitHub is TLS 1.2+ only; see Set-StrongTls at the top.
$tlsWhy = Set-StrongTls
if ($tlsWhy) { Warn ("Could not raise the TLS level; download may fail on this PC: " + $tlsWhy) }
[Net.ServicePointManager]::Expect100Continue = $false
# .NET queues connections to the same host above this limit, so a 16-way
# segmented download with the old value of 8 would have run 8 at a time.
[Net.ServicePointManager]::DefaultConnectionLimit = [Math]::Max(32, $Segments * 2)

# ------------------------------------------------------ connecting quickly --
#
# GitHub's file host has four IPv4 addresses (185.199.108-111.133). From some
# Indian ISPs one of them, 185.199.109.133, does not answer at all (25 Oct
# 2026). .NET tries a host's addresses one after another and gives each one the
# whole Windows TCP connect time - about 21 s - so every connection that drew
# the dead address stalled for 21 s before trying the next, and the 20 s chunk
# watchdog cut many of them off just before they would have moved on.
#
# HttpWebRequest has no connect timeout of its own, but every ServicePoint asks
# its BindIPEndPointDelegate before each address it tries, and an exception
# thrown there makes .NET skip that address and go straight to the next one.
# So the helper below, before an address is used for the first time, knocks on
# all of the host's addresses at once (a bare TCP connect, 3 s at most) and
# remembers the answers for 10 minutes. Then:
#   * an IPv6 address is skipped when an IPv4 address of the host answers
#     (prefer IPv4: IPv6 is the other thing that is broken on some lines);
#   * an address that did not answer is skipped when ANOTHER address answers;
#   * otherwise .NET connects exactly as before - a slow line that answers no
#     knock is never made worse than it was.
# Nothing else about the requests changes: the same 16 connections, the same
# hosts, TLS and certificate checks untouched. If the helper cannot be compiled
# (no csc, policy), the worker carries on without it.
$FastConnectSource = @'
using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;

namespace SatpudaNet
{
    public class SkipAddressException : Exception
    {
        public SkipAddressException(string m) : base(m) { }
    }

    public static class FastConnect
    {
        public static int ProbeMs = 3000;
        static readonly object Gate = new object();
        static readonly Dictionary<string, bool> Good = new Dictionary<string, bool>();
        static readonly Dictionary<string, DateTime> When = new Dictionary<string, DateTime>();
        static readonly Dictionary<string, object> HostGates = new Dictionary<string, object>();
        static readonly Dictionary<string, bool> Told = new Dictionary<string, bool>();
        // Strong references: ServicePointManager only holds ServicePoints weakly,
        // and a recreated one would come back without the delegate.
        static readonly List<ServicePoint> Kept = new List<ServicePoint>();
        static readonly Queue<string> Notes = new Queue<string>();

        public static void Hook(string url)
        {
            Uri u;
            if (!Uri.TryCreate(url, UriKind.Absolute, out u)) return;
            ServicePoint sp = ServicePointManager.FindServicePoint(u);
            lock (Gate)
            {
                if (Kept.Contains(sp)) return;
                Kept.Add(sp);
            }
            sp.BindIPEndPointDelegate = new BindIPEndPoint(Bind);
        }

        public static string[] TakeNotes()
        {
            lock (Gate) { string[] a = Notes.ToArray(); Notes.Clear(); return a; }
        }

        static void Note(string s)
        {
            lock (Gate) { if (Notes.Count < 500) Notes.Enqueue(s); }
        }

        static void NoteOnce(string s)
        {
            lock (Gate) { if (Told.ContainsKey(s)) return; Told[s] = true; }
            Note(s);
        }

        static string Key(IPAddress a, int port)
        {
            return (a.AddressFamily == AddressFamily.InterNetworkV6 ? "[" + a + "]" : a.ToString()) + ":" + port;
        }

        // 1 = answered, 0 = did not, -1 = not knocked on in the last 10 minutes
        static int Known(string k)
        {
            lock (Gate)
            {
                DateTime t;
                if (When.TryGetValue(k, out t) && (DateTime.UtcNow - t).TotalMinutes < 10) return Good[k] ? 1 : 0;
                return -1;
            }
        }

        static void Remember(string k, bool ok)
        {
            lock (Gate) { Good[k] = ok; When[k] = DateTime.UtcNow; }
        }

        // Knock on every address without a fresh answer, all at once, one
        // deadline. Stops early once the choice is clear: the address .NET is
        // about to use answered (IPv4), or another address answered first - then
        // the one about to be used is at best slower, and is passed over.
        static void ProbeAll(IList<IPAddress> addrs, int port, IPAddress remote)
        {
            string rk = Key(remote, port);
            bool v6 = remote.AddressFamily == AddressFamily.InterNetworkV6;
            List<Socket> socks = new List<Socket>();
            List<IAsyncResult> res = new List<IAsyncResult>();
            List<string> keys = new List<string>();
            foreach (IPAddress a in addrs)
            {
                string k = Key(a, port);
                if (Known(k) >= 0 || keys.Contains(k)) continue;
                Socket s = null;
                try
                {
                    s = new Socket(a.AddressFamily, SocketType.Stream, ProtocolType.Tcp);
                    IAsyncResult r = s.BeginConnect(new IPEndPoint(a, port), null, null);
                    socks.Add(s); res.Add(r); keys.Add(k);
                }
                catch (Exception e)
                {
                    if (s != null) { try { s.Close(); } catch { } }
                    Remember(k, false);
                    Note("cannot reach " + k + " (" + e.Message + ")");
                }
            }
            bool[] done = new bool[res.Count];
            DateTime end = DateTime.UtcNow.AddMilliseconds(ProbeMs);
            while (true)
            {
                bool pending = false, remoteGood = false, otherGood = false;
                for (int i = 0; i < res.Count; i++)
                {
                    if (!done[i] && res[i].IsCompleted)
                    {
                        bool ok = false;
                        try { socks[i].EndConnect(res[i]); ok = true; } catch { ok = false; }
                        try { socks[i].Close(); } catch { }
                        done[i] = true;
                        Remember(keys[i], ok);
                        Note((ok ? "answers: " : "refused: ") + keys[i]);
                    }
                    if (!done[i]) pending = true;
                }
                foreach (IPAddress a in addrs)
                {
                    if (Known(Key(a, port)) != 1) continue;
                    if (a.Equals(remote)) remoteGood = true; else otherGood = true;
                }
                if (!pending || (remoteGood && !v6) || otherGood) break;
                if (DateTime.UtcNow >= end) break;
                System.Threading.Thread.Sleep(15);
            }
            bool late = DateTime.UtcNow >= end;
            for (int i = 0; i < res.Count; i++)
            {
                if (done[i]) continue;
                try { socks[i].Close(); } catch { }
                if (late)
                {
                    Remember(keys[i], false);
                    Note("no answer within " + ProbeMs + " ms: " + keys[i]);
                }
                else if (keys[i] == rk)
                {
                    Remember(keys[i], false);
                    Note("slower than another address: " + keys[i]);
                }
                // any other address still knocking is simply asked again later
            }
        }

        static IPAddress[] Resolve(string host)
        {
            IPAddress lit;
            if (IPAddress.TryParse(host.Trim('[', ']'), out lit)) return new IPAddress[] { lit };
            try { return Dns.GetHostAddresses(host); } catch { return new IPAddress[0]; }
        }

        static object HostGate(string h)
        {
            lock (Gate)
            {
                object o;
                if (!HostGates.TryGetValue(h, out o)) { o = new object(); HostGates[h] = o; }
                return o;
            }
        }

        static IPEndPoint Bind(ServicePoint sp, IPEndPoint remote, int retryCount)
        {
            if (retryCount > 0) return null;
            try { Decide(sp, remote); }
            catch (SkipAddressException) { throw; }
            catch { }   // anything unexpected: connect the normal way
            return null;
        }

        static void Decide(ServicePoint sp, IPEndPoint remote)
        {
            string host = sp.Address.Host;
            int port = remote.Port;
            string rk = Key(remote.Address, port);
            bool v6 = remote.AddressFamily == AddressFamily.InterNetworkV6;
            // An IPv4 address that answered recently: nothing to decide.
            if (!v6 && Known(rk) == 1) return;

            lock (HostGate(host))
            {
                List<IPAddress> all = new List<IPAddress>(Resolve(host));
                bool listed = false;
                foreach (IPAddress a in all) if (a.Equals(remote.Address)) listed = true;
                if (!listed) all.Add(remote.Address);
                ProbeAll(all, port, remote.Address);

                bool otherGood = false, v4Good = false;
                foreach (IPAddress a in all)
                {
                    if (Known(Key(a, port)) != 1) continue;
                    if (a.AddressFamily == AddressFamily.InterNetwork) v4Good = true;
                    if (!a.Equals(remote.Address)) otherGood = true;
                }
                if (v6 && v4Good)
                {
                    NoteOnce("prefer IPv4: skipping " + rk + " for " + host);
                    throw new SkipAddressException("prefer IPv4");
                }
                if (Known(rk) == 1) return;
                if (otherGood)
                {
                    NoteOnce("skipping " + rk + " for " + host + ": it does not answer, another address does");
                    throw new SkipAddressException("no answer");
                }
            }
        }
    }
}
'@

function Enable-FastConnect {
    try {
        Add-Type -TypeDefinition $FastConnectSource -Language CSharp -ErrorAction Stop
        $script:FastConnect = $true
    } catch {
        Dbg ('fast connect helper not available, connecting the normal way: ' + $_.Exception.Message)
        return
    }
    foreach ($u in @('https://github.com/', 'https://api.github.com/',
                     'https://release-assets.githubusercontent.com/', 'https://objects.githubusercontent.com/',
                     $ApiBase, $DirectUrl)) {
        Use-FastConnect $u
    }
    Dbg 'fast connect: addresses that do not answer within 3 s are skipped; IPv4 preferred'
}

function Use-FastConnect([string]$u) {
    if ((-not $script:FastConnect) -or (-not $u)) { return }
    try { [SatpudaNet.FastConnect]::Hook($u) } catch { }
}

Write-CompatReport

# The zip reader is part of .NET 4.5 and newer, which every Windows 10 has. A
# PC whose .NET is damaged gets a sentence that says so - not a bare exit 1.
try {
    Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop | Out-Null
} catch {
    Fail 'DOTNET' ('The .NET zip reader (System.IO.Compression, .NET 4.5 or newer) could not be loaded: ' + $_.Exception.Message)
}

Enable-FastConnect

$script:Staging = Join-Path $AppRoot 'app.staging'
$LiveDir        = Join-Path $AppRoot 'app'
$UserAgent      = 'SatpudaCoreInstaller'

New-Dir $AppRoot
New-Dir $CacheDir

# A staging folder left over from a previous failed run is dead weight, and it
# is also the thing that must never be mistaken for an install.
if (Test-Path -LiteralPath $script:Staging) {
    Dbg 'removing staging folder left by a previous run'
    [void](Remove-Tree $script:Staging)
}

Assert-NotCancelled

# ------------------------------------------------------- 1. resolve the asset --
#
# Only functions here; they are called from the download loop further down,
# once everything they use has been defined.
#
# SAME TAG, NEW BUILD. The owner publishes new builds into the SAME release tag
# (v1.0.11 had its files replaced several times), and "gh release upload
# --clobber" deletes the old asset about a minute before the new one finishes
# uploading. A shop that runs the installer in that minute used to be told the
# download "is no longer on the release page". Now:
#
#   * no zip on the release (or the direct link answers 404): wait and look
#     again every $UpdatePollSecs, for up to $UpdateWaitSecs, showing
#     "GitHub is updating this release - trying again in N s...". Only then
#     fail with the old message.
#   * the zip is there but its .sha256 is not, and the zip was replaced only
#     minutes ago: wait for the sidecar the same way, so a half-updated release
#     is not installed with an unchecked file. After the wait, carry on without
#     it exactly as before. (A release that simply never had a sidecar is not
#     waited for: its zip is not fresh.)
#   * the file changes while it is downloading (404 on a chunk, a different
#     total size, or a checksum/size mismatch at the end while the release now
#     shows a different asset or checksum): start again ONCE from the top.

$Url          = $DirectUrl
$ExpectedSize = 0
$ExpectedHash = ''
$script:Used   = $null
$script:ApiNow = [DateTime]::MinValue      # GitHub's own clock, from the last API answer

function ConvertFrom-HttpDate([string]$s) {
    return [DateTime]::Parse($s, [Globalization.CultureInfo]::InvariantCulture,
        ([Globalization.DateTimeStyles]::AdjustToUniversal -bor [Globalization.DateTimeStyles]::AssumeUniversal))
}

function Invoke-Api([string]$u) {
    $r = [System.Net.HttpWebRequest]::Create($u)
    $r.UserAgent = $UserAgent
    $r.Accept    = 'application/vnd.github+json'
    $r.Timeout   = 30000
    $r.ReadWriteTimeout = 30000
    $resp = $r.GetResponse()
    try {
        try {
            $d = $resp.Headers['Date']
            if ($d) {
                $script:ApiNow = ConvertFrom-HttpDate $d
                if (-not $script:SkewLogged) {
                    $script:SkewLogged = $true
                    Dbg ('clock: this PC is ' + [int]([DateTime]::UtcNow - $script:ApiNow).TotalSeconds + ' s off GitHub''s clock (+ = ahead)')
                }
            }
        } catch { }
        $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
        try { return $sr.ReadToEnd() } finally { $sr.Dispose() }
    } finally { $resp.Close() }
}

# The HTTP status of a failed call, 0 when no HTTP answer came back at all.
# PowerShell wraps .NET exceptions (MethodInvocationException), so walk down.
function Get-HttpStatus($ex) {
    $e = $ex
    for ($i = 0; ($i -lt 5) -and $e; $i++) {
        $we = $e -as [System.Net.WebException]
        if ($we) {
            if ($we.Response) { try { return [int]$we.Response.StatusCode } catch { return 0 } }
            return 0
        }
        $e = $e.InnerException
    }
    return 0
}

# What kind of failure the last network error was (a WebExceptionStatus such as
# TrustFailure, NameResolutionFailure, ConnectFailure) and its innermost message.
$script:NetStatus = ''
$script:NetDetail = ''
$script:SkewLogged = $false
function Save-NetError($ex) {
    $e = $ex
    for ($i = 0; ($i -lt 6) -and $e; $i++) {
        $we = $e -as [System.Net.WebException]
        if ($we) {
            if ($we.Response) { return }           # an HTTP answer: the line itself works
            $script:NetStatus = [string]$we.Status
            $inner = $we
            while ($inner.InnerException) { $inner = $inner.InnerException }
            $script:NetDetail = ([string]$inner.Message) -replace '[\r\n|]+', ' '
            return
        }
        $e = $e.InnerException
    }
}

# Seconds this PC's clock is ahead (+) or behind (-) the internet's, from the
# Date header of a PLAIN-HTTP answer - no certificate in the way, so it works
# exactly when HTTPS does not. $null when nothing answered.
function Get-ClockSkew {
    foreach ($u in @($ClockUrl, 'http://www.msftconnecttest.com/connecttest.txt')) {
        if (-not $u) { continue }
        $resp = $null
        try {
            $r = [System.Net.HttpWebRequest]::Create($u)
            $r.Method            = 'HEAD'
            $r.AllowAutoRedirect = $false
            $r.Timeout           = 8000
            $r.ReadWriteTimeout  = 8000
            $r.UserAgent         = $UserAgent
            try { $resp = $r.GetResponse() }
            catch {
                $we = $_.Exception -as [System.Net.WebException]
                if (-not $we -and $_.Exception.InnerException) { $we = $_.Exception.InnerException -as [System.Net.WebException] }
                if ($we) { $resp = $we.Response }
            }
            if ($resp) {
                $d = $resp.Headers['Date']
                if ($d) { return ([DateTime]::UtcNow - (ConvertFrom-HttpDate $d)).TotalSeconds }
            }
        } catch { }
        finally { if ($resp) { try { $resp.Close() } catch { } } }
    }
    return $null
}

# Every "could not be downloaded" ends here. A PC whose clock is years off (a
# flat CMOS battery) fails EVERY certificate check, and one whose HTTPS is being
# intercepted fails them too; both used to be told to check the internet. Now
# CLOCK and TLS say what is really wrong; anything else is NET with its kind.
function Fail-Net([string]$t) {
    Write-ConnectNotes
    $st    = $script:NetStatus
    $trust = ($st -eq 'TrustFailure') -or ($st -eq 'SecureChannelFailure')
    Dbg ('network failure kind: ' + $(if ($st) { $st } else { 'none recorded' }) + $(if ($script:NetDetail) { ' (' + $script:NetDetail + ')' } else { '' }))
    $skew = Get-ClockSkew
    if ($null -ne $skew) {
        Dbg ('clock: this PC is ' + [int64]$skew + ' s off the internet''s clock (+ = ahead)')
        $abs = [Math]::Abs($skew)
        if (($trust -and ($abs -gt 6 * 3600)) -or ($abs -gt 30 * 86400)) {
            $inv  = [Globalization.CultureInfo]::InvariantCulture
            $pc   = (Get-Date).ToString('dd MMM yyyy, HH:mm', $inv)
            $real = [DateTime]::UtcNow.AddSeconds(-$skew).ToLocalTime().ToString('dd MMM yyyy, HH:mm', $inv)
            Fail 'CLOCK' ('The date and time on this computer are wrong: it says ' + $pc + ', the internet says ' + $real + '.')
        }
    } else { Dbg 'clock: could not be compared (no plain-HTTP answer either)' }
    if ($trust) {
        Fail 'TLS' ('The secure (HTTPS) connection to GitHub was not trusted on this computer (' + $st + ': ' + $script:NetDetail + ').')
    }
    if ($st -eq 'NameResolutionFailure')          { $t += ' github.com could not be found: no internet, or DNS is not working.' }
    elseif ($st -eq 'ProxyNameResolutionFailure') { $t += ' The proxy server set in Windows could not be found.' }
    elseif ($st)                                  { $t += ' (' + $st + $(if ($script:NetDetail) { ': ' + $script:NetDetail } else { '' }) + ')' }
    Fail 'NET' $t
}

# One look at the release. Never throws.
function Read-Release {
    $info = @{ ApiOk = $false; Url = ''; Size = [int64]0; Id = ''; Updated = ''; ShaListed = $false; Hash = '' }
    $rel = $null
    try {
        $rel = (Invoke-Api ("$ApiBase/repos/$Repo/releases/tags/$Tag")) | ConvertFrom-Json
        $info.ApiOk = $true
    } catch {
        Save-NetError $_.Exception
        Dbg ("release API failed: " + $_.Exception.Message)
        return $info
    }
    $shaUrl = ''
    foreach ($a in @($rel.assets)) {
        # An asset GitHub is still receiving is listed with a state other than
        # "uploaded". It is not a file yet.
        if ($a.state -and ([string]$a.state -ne 'uploaded')) { continue }
        if ($a.name -eq $AssetName) {
            $info.Url     = [string]$a.browser_download_url
            $info.Size    = [int64]$a.size
            $info.Id      = [string]$a.id
            $info.Updated = [string]$a.updated_at
        }
        elseif ($a.name -eq ($AssetName + '.sha256')) { $shaUrl = [string]$a.browser_download_url }
    }
    # Optional checksum sidecar. Publishing one is what upgrades verification
    # from "right number of bytes" to "byte-for-byte correct".
    if ($shaUrl) {
        $info.ShaListed = $true
        try {
            $txt = Invoke-Api $shaUrl
            if ($txt -match '([0-9a-fA-F]{64})') { $info.Hash = $Matches[1].ToLower() }
            else { Dbg 'checksum sidecar has no sha256 in it' }
        } catch {
            Dbg ('checksum sidecar could not be read: ' + $_.Exception.Message)
            # Deleted between the list and the read: it is being replaced.
            if ((Get-HttpStatus $_.Exception) -eq 404) { $info.ShaListed = $false }
        }
    }
    return $info
}

# Was the zip put on the release only minutes ago? Measured on GitHub's clock
# (the API's Date header), not the shop PC's, which is often wrong.
function Test-FreshAsset([string]$updated) {
    if (-not $updated) { return $false }
    try {
        $u   = ConvertFrom-HttpDate $updated
        $now = if ($script:ApiNow -ne [DateTime]::MinValue) { $script:ApiNow } else { [DateTime]::UtcNow }
        return (($now - $u).TotalMinutes -lt 15)
    } catch { return $false }
}

# One wait between two looks at a release that is being re-published. Returns
# $false, without waiting, once the deadline has passed. Stop works throughout.
function Wait-Republish([DateTime]$deadline, [string]$why) {
    $left = [int][Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalSeconds)
    if ($left -le 0) { return $false }
    $wait = [Math]::Min($UpdatePollSecs, $left)
    Dbg ('release ' + $Tag + ' is not complete (' + $why + '); looking again in ' + $wait + ' s')
    Step 'GitHub is updating this release'
    $end = [DateTime]::UtcNow.AddSeconds($wait)
    while ($true) {
        $s = [int][Math]::Ceiling(($end - [DateTime]::UtcNow).TotalSeconds)
        if ($s -le 0) { break }
        Say-Progress 0 ('GitHub is updating this release - trying again in ' + $s + ' s...')
        # A line every 5 seconds is enough of a countdown, and keeps the
        # installer's log short over a three-minute wait.
        $chunk = $s % 5
        if ($chunk -eq 0) { $chunk = 5 }
        if (Wait-Cancellable ($chunk * 1000)) { Exit-Clean 3 }
    }
    return $true
}

# Sets $script:Url, $script:ExpectedSize, $script:ExpectedHash and
# $script:Used (what the release looked like), waiting for a release that is
# half way through being re-published. Fails only after the wait.
function Resolve-Asset {
    Step 'Looking up the download'
    Progress 0 'Contacting GitHub'
    $deadline = [DateTime]::UtcNow.AddSeconds($UpdateWaitSecs)
    $warned   = $false
    while ($true) {
        Assert-NotCancelled
        $r = Read-Release
        if ($r.ApiOk) {
            if (-not $r.Url) {
                if (Wait-Republish $deadline ('no ' + $AssetName + ' on it')) { continue }
                Fail 'NOASSET' ("Could not find " + $AssetName + " on release " + $Tag + ".")
            }
            if ((-not $r.ShaListed) -and (Test-FreshAsset $r.Updated)) {
                if (Wait-Republish $deadline ('the zip was replaced at ' + $r.Updated + ' and its .sha256 is not there yet')) { continue }
                Dbg 'the zip is fresh but no checksum sidecar appeared; carrying on without one'
            }
            $script:Url          = $r.Url
            $script:ExpectedSize = $r.Size
            $script:ExpectedHash = $r.Hash
            $script:Used         = $r
            Dbg ("asset resolved, id=" + $r.Id + " size=" + $r.Size + " updated=" + $r.Updated)
            if ($r.Hash) { Dbg 'checksum sidecar found' }
            return
        }

        # Rate limited, offline, or the release went private. We are not dead
        # yet - the direct asset URL may still work. Size then comes from the
        # range probe. A 404 THERE is the same half-published release.
        if (-not $warned) {
            Warn 'Could not read the release details from GitHub; falling back to the direct link.'
            $warned = $true
        }
        if (-not $DirectUrl) {
            if (Wait-Republish $deadline 'the release API did not answer') { continue }
            Fail 'NOASSET' ("Could not find " + $AssetName + " on release " + $Tag + ".")
        }
        $probe = Get-RangeSupport $DirectUrl
        if ($probe.Status -eq 404) {
            if (Wait-Republish $deadline 'the direct link answers 404') { continue }
            Fail 'GONE' 'The download link no longer exists on GitHub.'
        }
        $script:Url          = $DirectUrl
        $script:ExpectedSize = [int64]0
        $script:ExpectedHash = ''
        $script:Used         = $null
        return
    }
}

# After a download that came out wrong: does the release now show a different
# file (or none, or a different checksum) from the one this run started with?
# $false when that cannot be told - then the old failure stands.
function Test-ReleaseChanged {
    $r = Read-Release
    if (-not $r.ApiOk) { return $false }
    $u = $script:Used
    if ($null -eq $u) { return [bool](-not $r.Url) }
    if (-not $r.Url)                             { Dbg 'the zip is no longer on the release'; return $true }
    if ($r.Id -and $u.Id -and ($r.Id -ne $u.Id)) { Dbg ('the zip was replaced (asset ' + $u.Id + ' -> ' + $r.Id + ')'); return $true }
    if ($r.Size -ne $u.Size)                     { Dbg ('the zip changed size (' + $u.Size + ' -> ' + $r.Size + ')'); return $true }
    # Both sides must have READ a checksum: a sidecar that could not be read
    # at the start (antivirus, a dropped connection) is not a change.
    if ($r.Hash -and $u.Hash -and ($r.Hash -ne $u.Hash)) { Dbg 'the checksum sidecar changed'; return $true }
    if ((-not $r.ShaListed) -and $u.ShaListed)   { Dbg 'the checksum sidecar is being replaced'; return $true }
    return $false
}

# --------------------------------------------------- 2. resumable download --

$PartFile = Join-Path $CacheDir ($AssetName + '.part')
$DoneFile = Join-Path $CacheDir $AssetName

# SHA-256 over a 122 MB file takes a couple of seconds; do it in chunks so
# Stop still answers while it runs.
function Get-Sha256([string]$path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $fs  = [System.IO.File]::OpenRead((Get-LongPath $path))
    try {
        $buf = New-Object byte[] 1048576
        while (($n = $fs.Read($buf, 0, $buf.Length)) -gt 0) {
            [void]$sha.TransformBlock($buf, 0, $n, $null, 0)
            if (Test-Cancel) { return '' }
        }
        [void]$sha.TransformFinalBlock($buf, 0, 0)
        return ([BitConverter]::ToString($sha.Hash) -replace '-', '').ToLower()
    }
    finally { $fs.Dispose(); $sha.Dispose() }
}

function Get-FileLength([string]$path) {
    if ([System.IO.File]::Exists((Get-LongPath $path))) {
        return (New-Object System.IO.FileInfo((Get-LongPath $path))).Length
    }
    return [int64]0
}

# Ask the server for one byte. A 206 with a Content-Range proves the CDN honours
# Range (so the file can be pulled in parallel) and tells us the true total size
# even when the release API was unreachable.
function Get-RangeSupport([string]$u) {
    $info = @{ Ranges = $false; Total = [int64]0; Status = 0 }
    $req = $null; $resp = $null
    try {
        $req = [System.Net.HttpWebRequest]::Create($u)
        $req.UserAgent         = $UserAgent
        $req.Timeout           = 30000
        $req.ReadWriteTimeout  = 30000
        $req.AllowAutoRedirect = $true
        $req.AddRange([int64]0, [int64]0)
        $resp = $req.GetResponse()
        $info.Status = [int]$resp.StatusCode
        # The host the link redirected to is the one the 16 connections use.
        try { Use-FastConnect $resp.ResponseUri.AbsoluteUri } catch { }
        if ([int]$resp.StatusCode -eq 206) {
            $cr = $resp.Headers['Content-Range']         # "bytes 0-0/127827033"
            if ($cr -and ($cr -match '/(\d+)\s*$')) {
                $info.Ranges = $true
                $info.Total  = [int64]$Matches[1]
            }
        }
    } catch {
        $info.Status = Get-HttpStatus $_.Exception
        Save-NetError $_.Exception
        Dbg ('range probe failed: ' + $_.Exception.Message)
    }
    finally {
        if ($resp) { try { $resp.Close() } catch { } }
    }
    return $info
}

# ---- the segment worker, run once per connection inside a runspace --------
#
# It writes ONLY to its own .part file and its own slots in $Sync, so no lock is
# needed beyond the synchronized hashtable itself. It publishes its live
# HttpWebRequest so the parent can Abort() it - that is what turns a Stop click
# into a stopped download instead of a 45-second wait on a blocked socket.

# The file is cut into small chunks (2 MB) held in a shared queue, and each
# connection keeps taking the next chunk until the queue is empty. Fixed
# one-piece-per-connection splits made the LAST piece decide the finish: one
# slow or half-dead connection held the bar near the end for minutes while the
# other fifteen sat idle. With a queue, the tail is at most one small chunk.
# Each chunk is fetched with its own HttpWebRequest so the parent can Abort()
# it - that is what turns a Stop click into a stopped download, and what lets
# the watchdog below cut off a connection that has stopped delivering.

$ChunkBytes     = [int64]2097152   # 2 MB
$ChunkStallSecs = 20               # a chunk with no new bytes for this long is aborted and retried
$TailSlowSecs   = 15               # at the tail, a chunk needing longer than this at its current speed is reconnected
$TailMaxCuts    = 3                # ...at most this many times per chunk

$SegmentWorker = {
    param($Url, $Prefix, $Bounds, $Queue, $Sync, $UserAgent, $MaxTries)

    function LongPath([string]$p) {
        if ($p.StartsWith('\\?\')) { return $p }
        if ($p.StartsWith('\\'))   { return '\\?\UNC\' + $p.Substring(2) }
        return '\\?\' + $p
    }

    while ($true) {
        if ($Sync['Cancel']) { return }
        $c = $null
        try { $c = [int]$Queue.Dequeue() } catch { return }   # queue empty: this connection is done

        $Start = [int64]$Bounds[$c][0]
        $End   = [int64]$Bounds[$c][1]
        $lp    = LongPath ($Prefix + $c)
        $want  = $End - $Start + 1
        $try   = 0

        while ($true) {
            if ($Sync['Cancel']) { return }
            $try++

            $have = 0
            if ([System.IO.File]::Exists($lp)) { $have = (New-Object System.IO.FileInfo($lp)).Length }
            if ($have -gt $want) {                       # bogus leftover, start this chunk again
                try { [System.IO.File]::Delete($lp) } catch { }
                $have = 0
            }
            $Sync['done' + $c] = $have
            if ($have -ge $want) { break }               # chunk already complete

            $req = $null; $resp = $null; $ins = $null; $outs = $null
            try {
                $req = [System.Net.HttpWebRequest]::Create($Url)
                $req.UserAgent         = $UserAgent
                $req.Timeout           = 45000
                $req.ReadWriteTimeout  = 45000
                $req.AllowAutoRedirect = $true
                $req.KeepAlive         = $true
                $req.AddRange([int64]($Start + $have), [int64]$End)
                $Sync['grow' + $c] = [DateTime]::UtcNow.Ticks
                $Sync['t0' + $c]   = [DateTime]::UtcNow.Ticks
                $Sync['b0' + $c]   = $have
                $Sync['req' + $c]  = $req

                $resp = $req.GetResponse()
                if ([int]$resp.StatusCode -ne 206) { throw 'server ignored the Range header' }
                # The total after the "/" is the size of the file GitHub is
                # serving NOW. If it is not the size this layout was cut for,
                # the asset was replaced mid-download: stop, rather than glue
                # bytes of two different builds together.
                $cr = $resp.Headers['Content-Range']
                if ($cr -and ($cr -match '/(\d+)\s*$') -and ([int64]$Matches[1] -ne ([int64]$Bounds[$Bounds.Count - 1][1] + 1))) {
                    $Sync['lasterr' + $c] = ('the file changed size on the server (' + $cr + ')')
                    $Sync['changed'] = $true
                    return
                }

                $ins  = $resp.GetResponseStream()
                $outs = New-Object System.IO.FileStream($lp, [System.IO.FileMode]::Append,
                            [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read, 262144)
                $buf = New-Object byte[] 262144
                while (($n = $ins.Read($buf, 0, $buf.Length)) -gt 0) {
                    $outs.Write($buf, 0, $n)
                    $have += $n
                    $Sync['done' + $c] = $have
                    $Sync['grow' + $c] = [DateTime]::UtcNow.Ticks
                    if ($Sync['Cancel']) { break }
                    if ($have -ge $want) { break }
                }
                $outs.Flush()
            }
            catch {
                if (-not $Sync['Cancel']) { $Sync['lasterr' + $c] = $_.Exception.Message }
                # 404: the asset was deleted (a new build is being uploaded in
                # its place). 416: our range is past the end of a now-smaller
                # file. Either way retrying this chunk cannot help.
                $ex = $_.Exception; $sc = 0
                for ($k = 0; ($k -lt 5) -and $ex; $k++) {
                    $we = $ex -as [System.Net.WebException]
                    if ($we) { if ($we.Response) { try { $sc = [int]$we.Response.StatusCode } catch { } }; break }
                    $ex = $ex.InnerException
                }
                if ($sc -eq 404) { $Sync['gone'] = $true }
                if ($sc -eq 416) { $Sync['changed'] = $true }
            }
            finally {
                $Sync['req' + $c] = $null
                if ($outs) { try { $outs.Dispose() } catch { } }
                if ($ins)  { try { $ins.Dispose()  } catch { } }
                if ($resp) { try { $resp.Close()   } catch { } }
            }

            if ($Sync['Cancel']) { return }
            if ($Sync['gone'] -or $Sync['changed']) { return }

            $have = 0
            if ([System.IO.File]::Exists($lp)) { $have = (New-Object System.IO.FileInfo($lp)).Length }
            $Sync['done' + $c] = $have
            if ($have -ge $want) { break }               # done, take the next chunk

            if ($try -ge $MaxTries) {
                # Leave it short; the next pass queues it again.
                $Sync['err' + $c] = ('chunk ' + $c + ' stopped at ' + $have + ' of ' + $want +
                                     ' (' + [string]$Sync['lasterr' + $c] + ')')
                break
            }
            # Interruptible backoff: 0.5s, 1s, 2s, 4s ... capped at 8s.
            $wait = [Math]::Min(8000, 500 * [Math]::Pow(2, $try - 1))
            $end2 = [DateTime]::UtcNow.AddMilliseconds($wait)
            while ([DateTime]::UtcNow -lt $end2) {
                if ($Sync['Cancel']) { return }
                Start-Sleep -Milliseconds 100
            }
        }
    }
}

# Stop every connection as fast as the OS allows: kill the sockets first (Abort
# unblocks a thread parked in Read), then let the runspaces unwind.
function Stop-Segments($pool, $handles, $Sync, [int]$Chunks) {
    $Sync['Cancel'] = $true
    for ($i = 0; $i -lt $Chunks; $i++) {
        $r = $Sync['req' + $i]
        if ($r) { try { $r.Abort() } catch { } }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(4)
    while ([DateTime]::UtcNow -lt $deadline) {
        $busy = 0
        foreach ($h in $handles) { if (-not $h.Handle.IsCompleted) { $busy++ } }
        if ($busy -eq 0) { break }
        Start-Sleep -Milliseconds 100
    }
    foreach ($h in $handles) {
        try { [void]$h.PS.Stop() } catch { }
        try { $h.PS.Dispose() }   catch { }
    }
    try { $pool.Close() }   catch { }
    try { $pool.Dispose() } catch { }
}

# The layout is baked into the names, so a release whose size changed can never
# be resumed from the wrong pieces. The connection count is NOT in the name: any
# number of connections can resume the same chunks.
function Get-ChunkLayout([int64]$total) {
    # The asset id is in the name too: two builds of the same SIZE (same tag,
    # republished) must never share chunk files.
    $prefix = Join-Path $CacheDir ($AssetName + '.' + $total + '.' + (Get-CacheIdentity) + '.c' + $ChunkBytes + '.seg')
    $bounds = @()
    $count  = [int][Math]::Ceiling($total / [double]$ChunkBytes)
    for ($i = 0; $i -lt $count; $i++) {
        $start = [int64]($i * $ChunkBytes)
        $end   = [int64][Math]::Min($total - 1, $start + $ChunkBytes - 1)
        $bounds += ,@($start, $end)
    }
    return @{ Prefix = $prefix; Bounds = $bounds; Count = $count }
}

# Returns $true when every chunk file is complete.
function Invoke-SegmentedDownload([string]$u, [int64]$total, [int]$segs, [int]$passes) {

    $layout = Get-ChunkLayout $total
    $prefix = $layout.Prefix; $bounds = $layout.Bounds; $count = $layout.Count

    # Sweep away chunk/segment files from any OTHER layout (older installers too).
    Get-ChildItem -LiteralPath $CacheDir -Filter ($AssetName + '.*.seg*') -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike ($prefix + '*') } |
        ForEach-Object { try { Remove-Item -LiteralPath $_.FullName -Force } catch { } }

    for ($pass = 1; $pass -le $passes; $pass++) {
        if (Test-Cancel) { Exit-Clean 3 }

        $Sync  = [hashtable]::Synchronized(@{})
        $Sync['Cancel'] = $false
        $Queue = [System.Collections.Queue]::Synchronized((New-Object System.Collections.Queue))
        $todo  = 0
        for ($i = 0; $i -lt $count; $i++) {
            $len = Get-FileLength ($prefix + $i)
            $Sync['done' + $i] = $len
            if ($len -lt ($bounds[$i][1] - $bounds[$i][0] + 1)) { $Queue.Enqueue($i); $todo++ }
        }
        if ($todo -eq 0) { return $true }

        $conns = [Math]::Max(1, [Math]::Min($segs, $todo))
        $pool = [runspacefactory]::CreateRunspacePool(1, $conns)
        $pool.ApartmentState = 'MTA'
        $pool.Open()

        $handles = @()
        for ($w = 0; $w -lt $conns; $w++) {
            $ps = [powershell]::Create()
            $ps.RunspacePool = $pool
            [void]$ps.AddScript($SegmentWorker).
                      AddArgument($u).AddArgument($prefix).AddArgument($bounds).
                      AddArgument($Queue).AddArgument($Sync).AddArgument($UserAgent).AddArgument(6)
            $handles += [pscustomobject]@{ PS = $ps; Handle = $ps.BeginInvoke() }
        }
        Dbg ("pass " + $pass + ": " + $todo + " of " + $count + " chunks of " + [math]::Round($ChunkBytes / 1MB) + " MB over " + $conns + " connections")

        $lastGot  = -1
        $lastGrow = [DateTime]::UtcNow
        $stalled  = $false
        $cutoffs  = 0

        while ($true) {
            if (Test-Cancel) {
                Dbg 'cancel during segmented download'
                Stop-Segments $pool $handles $Sync $count
                Exit-Clean 3
            }

            # The release's file was deleted or replaced under us (a new build
            # going into the same tag). Every connection stops; the caller
            # looks at the release again and starts over.
            if ($Sync['gone'] -or $Sync['changed']) {
                Dbg ('segmented download: the file on GitHub was ' + $(if ($Sync['gone']) { 'deleted (404)' } else { 'replaced (different size)' }) + ' mid-download')
                Stop-Segments $pool $handles $Sync $count
                $script:Replaced = $true
                return $false
            }

            $got = [int64]0
            for ($i = 0; $i -lt $count; $i++) { $got += [int64]$Sync['done' + $i] }

            if ($got -gt $lastGot) { $lastGot = $got; $lastGrow = [DateTime]::UtcNow }
            elseif (([DateTime]::UtcNow - $lastGrow).TotalSeconds -ge 120) { $stalled = $true }

            # Watchdog: a connection that stopped delivering is cut off at once
            # instead of waiting out its 45 s socket timeout. Its worker retries
            # the chunk from where it got to.
            $nowTicks = [DateTime]::UtcNow.Ticks
            for ($i = 0; $i -lt $count; $i++) {
                $r = $Sync['req' + $i]
                if ($r -and $Sync['grow' + $i] -and
                    (($nowTicks - [int64]$Sync['grow' + $i]) / 10000000) -ge $ChunkStallSecs) {
                    $Sync['grow' + $i] = $nowTicks
                    try { $r.Abort() } catch { }
                    $cutoffs++
                    Dbg ('chunk ' + $i + ': no data for ' + $ChunkStallSecs + 's, reconnecting')
                }
            }

            # Tail: once every chunk has been handed out, nothing is waiting for a
            # free connection, so a connection that is merely SLOW (still
            # delivering, so the watchdog above never fires) is what sets the
            # finish time. Reconnect it - a fresh connection usually lands on a
            # faster path - a few times at most.
            if ($Queue.Count -eq 0) {
                for ($i = 0; $i -lt $count; $i++) {
                    $r = $Sync['req' + $i]
                    if (-not $r -or -not $Sync['t0' + $i]) { continue }
                    $el = ($nowTicks - [int64]$Sync['t0' + $i]) / 10000000
                    if ($el -lt 10) { continue }
                    $cuts = [int]$Sync['tailcut' + $i]
                    if ($cuts -ge $TailMaxCuts) { continue }
                    $gotNow = [int64]$Sync['done' + $i]
                    $rate   = ($gotNow - [int64]$Sync['b0' + $i]) / $el
                    $left   = ($bounds[$i][1] - $bounds[$i][0] + 1) - $gotNow
                    if ($rate -le 0 -or ($left / $rate) -gt $TailSlowSecs) {
                        $Sync['tailcut' + $i] = $cuts + 1
                        $Sync['t0' + $i] = $nowTicks
                        try { $r.Abort() } catch { }
                        $cutoffs++
                        Dbg ('chunk ' + $i + ': slow at the tail (' + [math]::Round($rate / 1KB) + ' KB/s), reconnecting')
                    }
                }
            }

            Progress ([int](($got / $total) * 55)) `
                     ('Downloading ' + [math]::Round($got / 1MB) + ' of ' + [math]::Round($total / 1MB) + ' MB')

            $busy = 0
            foreach ($h in $handles) { if (-not $h.Handle.IsCompleted) { $busy++ } }
            if ($busy -eq 0 -or $stalled) { break }

            Start-Sleep -Milliseconds 250
        }

        if ($stalled) { Dbg 'no bytes for 120s; restarting the pass' }
        Stop-Segments $pool $handles $Sync $count
        if ($Sync['gone'] -or $Sync['changed']) {
            Dbg 'segmented download: the file on GitHub was deleted or replaced mid-download'
            $script:Replaced = $true
            return $false
        }

        # How much really landed on disk (Sync is a hint; the files are the truth).
        $onDisk = [int64]0
        $short  = 0
        $why    = ''
        for ($i = 0; $i -lt $count; $i++) {
            $len  = Get-FileLength ($prefix + $i)
            $want = $bounds[$i][1] - $bounds[$i][0] + 1
            $onDisk += $len
            if ($len -lt $want) {
                $short++
                if ($why -eq '' -and $Sync['err' + $i]) { $why = [string]$Sync['err' + $i] }
            }
        }
        Dbg ("pass " + $pass + " finished: " + $onDisk + " of " + $total + " bytes, " + $short + " short chunk(s), " + $cutoffs + " slow or stalled connection(s) reconnected")

        if ($short -eq 0) { return $true }
        if ($pass -ge $passes) {
            Dbg ('giving up after ' + $passes + ' passes: ' + $why)
            return $false
        }
        Step ('Connection dropped - retrying (try ' + ($pass + 1) + ' of ' + $passes + ')')
        if (Wait-Cancellable 3000) { Exit-Clean 3 }
    }
    return $false
}

# Glue the chunks into one file. Cheap next to the download, and it makes the
# resume state on disk self-describing: a chunk file's LENGTH is how far it
# got, with nothing to keep in step on the side.
function Join-Segments([int64]$total) {
    $layout = Get-ChunkLayout $total
    $prefix = $layout.Prefix; $count = $layout.Count
    Step 'Putting the download together'
    if (Test-Path -LiteralPath $PartFile) { Remove-Item -LiteralPath $PartFile -Force -ErrorAction SilentlyContinue }

    $out = New-Object System.IO.FileStream((Get-LongPath $PartFile), [System.IO.FileMode]::Create,
               [System.IO.FileAccess]::Write, [System.IO.FileShare]::None, 1048576)
    try {
        $buf  = New-Object byte[] 1048576
        $done = [int64]0
        for ($i = 0; $i -lt $count; $i++) {
            $src = New-Object System.IO.FileStream((Get-LongPath ($prefix + $i)), [System.IO.FileMode]::Open,
                       [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read, 1048576)
            try {
                while (($n = $src.Read($buf, 0, $buf.Length)) -gt 0) {
                    $out.Write($buf, 0, $n)
                    $done += $n
                    if (Test-Cancel) { $out.Dispose(); Exit-Clean 3 }
                    Progress (55 + [int](($done / $total) * 6)) 'Putting the download together'
                }
            } finally { $src.Dispose() }
        }
        $out.Flush()
    } finally { try { $out.Dispose() } catch { } }

    for ($i = 0; $i -lt $count; $i++) {
        try { Remove-Item -LiteralPath ($prefix + $i) -Force -ErrorAction SilentlyContinue } catch { }
    }
}

# The original one-connection loop. Still here because it is the only thing that
# works when the server refuses Range requests, and because it is what resumes a
# .part file left by an older version of this installer.
#
# -NoRange: the whole file in one plain GET, every attempt from byte 0, no
# Range header at all. Used when range answers looked wrong although the
# release had NOT changed - an HTTPS-scanning antivirus or proxy (seen on a
# shop PC with Net Protector) can answer Range requests with 416, drop or alter
# Content-Range, or hand back 200. A plain GET is the one thing they all pass.
function Invoke-SingleDownload([string]$u, [switch]$NoRange) {
    $maxAttempts    = 6
    $backoff        = @(2, 5, 10, 20, 30, 30)
    $noProgressRuns = 0
    $last404        = $false

    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        Assert-NotCancelled
        if ($NoRange -and (Test-Path -LiteralPath $PartFile)) { Remove-Item -LiteralPath $PartFile -Force -ErrorAction SilentlyContinue }
        $have = Get-FileLength $PartFile

        if ($script:ExpectedSize -gt 0 -and $have -ge $script:ExpectedSize) { Dbg 'part file already complete'; return $true }

        $req = $null; $resp = $null; $inStream = $null; $outStream = $null
        $gained = 0
        try {
            $req = [System.Net.HttpWebRequest]::Create($u)
            $req.UserAgent         = $UserAgent
            $req.Timeout           = 60000
            $req.ReadWriteTimeout  = 60000
            $req.AllowAutoRedirect = $true      # github.com -> release-assets.githubusercontent.com
            $req.KeepAlive         = $true
            if ($have -gt 0) { $req.AddRange([int64]$have) }
            $script:LiveRequest = $req

            $resp   = $req.GetResponse()
            $status = [int]$resp.StatusCode
            $len    = $resp.ContentLength

            $resuming = ($have -gt 0 -and $status -eq 206)
            if ($have -gt 0 -and $status -eq 200) {
                # Server ignored the Range header - start over rather than
                # append and silently corrupt the file.
                Dbg 'server ignored Range; restarting download'
                $have = 0
                if (Test-Path -LiteralPath $PartFile) { Remove-Item -LiteralPath $PartFile -Force }
            }

            # The size of the file GitHub is serving NOW. Not the size the
            # release listed when this run started: the asset was replaced
            # mid-download, and appending to the old bytes would only make a
            # damaged file. The caller looks at the release again.
            $serverTotal = [int64]0
            if ($status -eq 206) {
                $cr = $resp.Headers['Content-Range']
                if ($cr -and ($cr -match '/(\d+)\s*$')) { $serverTotal = [int64]$Matches[1] }
            } elseif ($len -gt 0) { $serverTotal = [int64]$len }
            if ((-not $NoRange) -and $script:ExpectedSize -gt 0 -and $serverTotal -gt 0 -and $serverTotal -ne $script:ExpectedSize) {
                Dbg ('the file on GitHub is now ' + $serverTotal + ' bytes, not ' + $script:ExpectedSize + ': it was replaced')
                $script:Replaced = $true
                return $false
            }

            $total = if ($script:ExpectedSize -gt 0) { $script:ExpectedSize } elseif ($len -gt 0) { $have + $len } else { 0 }
            if ($script:ExpectedSize -eq 0 -and $total -gt 0) { $script:ExpectedSize = $total; Dbg ("size from Content-Length: " + $total) }

            if ($resuming) { Step ('Resuming download at ' + [math]::Round($have / 1MB) + ' MB') }

            $inStream  = $resp.GetResponseStream()
            $outStream = New-Object System.IO.FileStream((Get-LongPath $PartFile), [System.IO.FileMode]::Append,
                             [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read, 1048576)

            $buf   = New-Object byte[] 1048576
            $got   = $have
            $since = 0
            while (($n = $inStream.Read($buf, 0, $buf.Length)) -gt 0) {
                $outStream.Write($buf, 0, $n)
                $got    += $n
                $gained += $n
                $since  += $n
                if (Test-Cancel) { $outStream.Flush(); throw [System.OperationCanceledException]::new('cancelled') }
                if ($since -ge 4194304) {
                    $since = 0
                    if ($total -gt 0) {
                        Progress ([int](($got / $total) * 61)) ('Downloading ' + [math]::Round($got / 1MB) + ' of ' + [math]::Round($total / 1MB) + ' MB')
                    } else {
                        Progress 30 ('Downloading ' + [math]::Round($got / 1MB) + ' MB')
                    }
                }
            }
            $outStream.Flush()
            Dbg ("attempt " + $attempt + " transferred " + $gained + " bytes")

            # A dropped connection does not always raise - sometimes the stream
            # just ends early. Treat a short file as a retry (which resumes),
            # never as a finished download.
            if ($total -gt 0 -and $got -lt $total) {
                if ($attempt -ge $maxAttempts) {
                    Fail 'NET' ('The download kept stopping early (' + [math]::Round($got / 1MB) + ' of ' + [math]::Round($total / 1MB) + ' MB).')
                }
                if ($gained -eq 0) { $noProgressRuns++ } else { $noProgressRuns = 0 }
                if ($noProgressRuns -ge 3) {
                    Fail-Net 'The download is not moving forward at all. The internet connection looks down.'
                }
                $wait = $backoff[$attempt - 1]
                Step ('Connection dropped - retrying in ' + $wait + 's (try ' + ($attempt + 1) + ' of ' + $maxAttempts + ')')
                if (Wait-Cancellable ($wait * 1000)) { Exit-Clean 3 }
                continue
            }
            return $true   # stream ended cleanly and the file is complete
        }
        catch [System.OperationCanceledException] { Exit-Clean 3 }
        catch {
            if (Test-Cancel) { Exit-Clean 3 }
            $msg = $_.Exception.Message

            # PowerShell wraps anything thrown by a .NET method call in a
            # MethodInvocationException, so $_.Exception is NOT the WebException
            # and casting it straight away silently yields $null - which would
            # make every 416/404/403 branch below dead code. Unwrap first.
            $we = $_.Exception -as [System.Net.WebException]
            if (-not $we -and $_.Exception.InnerException) {
                $we = $_.Exception.InnerException -as [System.Net.WebException]
            }

            # A certificate that is not trusted stays untrusted however often
            # it is asked again (a wrong clock, HTTPS interception): say so now
            # instead of after a minute of retries.
            Save-NetError $_.Exception
            if ($we -and (-not $we.Response) -and ([string]$we.Status -eq 'TrustFailure')) {
                Fail-Net ('Download failed: ' + $msg)
            }

            if ($we -and $we.Response) {
                $code = [int]$we.Response.StatusCode
                if ($code -eq 416) {
                    # Range past end: our part file is bogus. Wipe and retry.
                    Dbg '416 - discarding part file'
                    if (Test-Path -LiteralPath $PartFile) { Remove-Item -LiteralPath $PartFile -Force }
                    continue
                }
                if ($code -eq 404) {
                    # Deleted under us - usually a new build going into the
                    # same tag. The caller waits for it and starts again. In
                    # -NoRange mode the release is asked first: unchanged means
                    # this 404 came from something between us and GitHub, and
                    # it is retried like any other network error.
                    Dbg '404 - the file is not there'
                    if ((-not $NoRange) -or (Test-ReleaseChanged)) {
                        $script:Replaced = $true
                        $script:Gone     = $true
                        return $false
                    }
                    $last404 = $true
                }
                if ($code -eq 403) { Fail 'FORBID' 'GitHub refused the download. The release may not be public.' }
            }

            if ($attempt -ge $maxAttempts) {
                if ($last404) { Fail 'GONE' 'The download link no longer exists on GitHub.' }
                Fail-Net ('Download failed after ' + $maxAttempts + ' tries: ' + $msg)
            }
            if ($gained -eq 0) { $noProgressRuns++ } else { $noProgressRuns = 0 }
            if ($noProgressRuns -ge 3) {
                Fail-Net 'The download is not moving forward at all. The internet connection looks down.'
            }
            $wait = $backoff[$attempt - 1]
            Step ('Connection problem - retrying in ' + $wait + 's (try ' + ($attempt + 1) + ' of ' + $maxAttempts + ')')
            if (Wait-Cancellable ($wait * 1000)) { Exit-Clean 3 }
        }
        finally {
            $script:LiveRequest = $null
            if ($outStream) { try { $outStream.Dispose() } catch { } }
            if ($inStream)  { try { $inStream.Dispose()  } catch { } }
            if ($resp)      { try { $resp.Close()        } catch { } }
        }
    }
    return $false
}

# Everything a download left in the cache: the single-stream .part and every
# chunk file (the finished zip is handled by the cache check, which can prove
# it good or bad by its checksum).
function Clear-DownloadCache {
    try { if (Test-Path -LiteralPath $PartFile) { Remove-Item -LiteralPath $PartFile -Force -ErrorAction SilentlyContinue } } catch { }
    Get-ChildItem -LiteralPath $CacheDir -Filter ($AssetName + '.*.seg*') -Force -ErrorAction SilentlyContinue |
        ForEach-Object { try { Remove-Item -LiteralPath $_.FullName -Force } catch { } }
}

# Which file the cache is for: the asset id GitHub gave it ('direct' when the
# API could not be read). Part of every chunk file's name.
function Get-CacheIdentity {
    if ($script:Used -and $script:Used.Id) { return ('a' + $script:Used.Id) }
    return 'direct'
}

# Before anything else in a round: a cache left by ANOTHER build (another
# size or asset id - same tag republished, or an older installer whose files
# carry no id) is thrown away, so no byte of it can ever be resumed into this
# download. The record is a small text file next to the cache.
function Initialize-Cache {
    $keyFile = Join-Path $CacheDir ($AssetName + '.build')
    $want    = ([string]$script:ExpectedSize + '|' + (Get-CacheIdentity))
    $have    = ''
    try { if (Test-Path -LiteralPath $keyFile) { $have = ([System.IO.File]::ReadAllText($keyFile)).Trim() } } catch { }
    if ($have -ne $want) {
        Dbg ("download cache was for '" + $have + "', this download is '" + $want + "'; clearing it")
        Clear-DownloadCache
        # A finished zip is kept only when a published checksum will test it.
        if ((-not $ExpectedHash) -and (Test-Path -LiteralPath $DoneFile)) {
            Remove-Item -LiteralPath $DoneFile -Force -ErrorAction SilentlyContinue
        }
        try { [System.IO.File]::WriteAllText($keyFile, $want) } catch { }
    }
}

# A range answer looked wrong (404, 416, another total, ...). Only when the
# RELEASE itself now shows another file is that "GitHub replaced it"; then
# the round starts again (once) and, the second time, it is CHANGED. When the
# release is unchanged it is something between this PC and GitHub - an
# HTTPS-scanning antivirus or proxy - and the round carries on with one plain
# GET and no Range header. Returns 'restart' or 'plain'; fails on the last round.
function Resolve-RangeTrouble([bool]$lastRound, [string]$what) {
    if (Test-ReleaseChanged) {
        Dbg ($what + ', and the release now shows another file')
        Clear-DownloadCache
        if (-not $lastRound) { return 'restart' }
        Fail 'CHANGED' ('The file on release ' + $Tag + ' kept changing while it was being downloaded.')
    }
    Dbg ($what + ', but the release has NOT changed: treating it as a local network problem; one plain download without ranges')
    Clear-DownloadCache
    $script:Replaced = $false
    $script:Gone     = $false
    return 'plain'
}

# Round 1 is the normal run. Round 2 happens only when the release's file was
# deleted or replaced while this run was fetching it (a new build going into
# the same tag): the release is looked up again - waiting for the new file if
# it is still uploading - and the download starts over. Once, never more.
$MaxRounds = 2
$script:InstalledHash = ''
$script:HashVerified  = $false
$script:LocalCopy     = ''

# ------------------------------------------- 2b. or: a zip from the browser --
#
# -LocalZip: a shop PC whose antivirus breaks every download this worker makes
# (Net Protector, 25 Oct 2026) while its browser downloads the same file fine.
# The shopkeeper downloads the zip in the browser and the installer hands it
# here. It is COPIED into the cache first, so the checksum and the extraction
# read the same bytes, and nothing that goes wrong later can delete the
# shopkeeper's own file. Then it goes through exactly the steps a download does
# (open, extract to staging, sentinel check, marker, swap).
#
# Verified against the release's published .sha256 when GitHub answers; a zip
# that does not match is refused. When no checksum can be read it is checked by
# its contents only (it must open as a zip and hold the sentinel exe - the same
# checks as below), and UNVERIFIED| tells the installer to say so.
function Copy-LocalZip([string]$src, [string]$dst, [int64]$total) {
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }
    $in  = New-Object System.IO.FileStream((Get-LongPath $src), [System.IO.FileMode]::Open,
               [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read, 1048576)
    try {
        $out = New-Object System.IO.FileStream((Get-LongPath $dst), [System.IO.FileMode]::Create,
                   [System.IO.FileAccess]::Write, [System.IO.FileShare]::None, 1048576)
        try {
            $buf  = New-Object byte[] 1048576
            $done = [int64]0
            while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                $out.Write($buf, 0, $n)
                $done += $n
                if (Test-Cancel) { $out.Dispose(); $in.Dispose(); Exit-Clean 3 }
                Progress (5 + [int](($done / [double]$total) * 50)) ('Copying the chosen zip (' + [math]::Round($done / 1MB) + ' of ' + [math]::Round($total / 1MB) + ' MB)')
            }
            $out.Flush()
        } finally { try { $out.Dispose() } catch { } }
    } finally { try { $in.Dispose() } catch { } }
}

# Every build ever published under this tag, one sha256 per line (<asset>.sha256s on the
# release). True when the chosen zip is one of them. Never throws.
function Test-EarlierBuild([string]$hash) {
    $u = 'https://github.com/' + $Repo + '/releases/download/' + $Tag + '/' + $AssetName + '.sha256s'
    if ($ApiBase -ne 'https://api.github.com') { $u = $ApiBase + '/download/' + $Tag + '/' + $AssetName + '.sha256s' }
    try { $txt = Invoke-Api $u } catch { Dbg ('list of earlier builds could not be read: ' + $_.Exception.Message); return $false }
    foreach ($m in [regex]::Matches([string]$txt, '[0-9a-fA-F]{64}')) {
        if ($m.Value.ToLower() -eq $hash.ToLower()) { return $true }
    }
    return $false
}

function Use-LocalZip {
    Step 'Checking the chosen zip'
    Progress 0 'Checking the chosen zip'
    $src = $LocalZip
    try { $src = [System.IO.Path]::GetFullPath($LocalZip) } catch { }
    if (-not [System.IO.File]::Exists((Get-LongPath $src))) {
        Fail 'LOCALZIP' ('The chosen file was not found: ' + $src)
    }
    $len = Get-FileLength $src
    Dbg ('installing from a chosen zip: ' + $src + ' (' + $len + ' bytes) as release ' + $Tag)
    if ($len -lt 1048576) { Fail 'LOCALZIP' ('The chosen file is far too small to be Satpuda Core (' + $len + ' bytes).') }

    # The published checksum: one look at the release, no waiting for a
    # re-publish. A PC that cannot download the zip can often still read this.
    Step 'Reading the published checksum'
    Progress 2 'Reading the published checksum from GitHub'
    $r = Read-Release
    Assert-NotCancelled

    $script:LocalCopy = Join-Path $CacheDir ($AssetName + '.chosen')
    $script:DoneFile  = $script:LocalCopy
    Step 'Copying the chosen zip'
    Assert-FreeSpace $CacheDir ($len + 64MB) 'copy the chosen zip'
    try { Copy-LocalZip $src $script:LocalCopy $len }
    catch { Fail 'LOCALZIP' ('The chosen file could not be read: ' + $_.Exception.Message) }

    Step 'Checking the chosen zip'
    Progress 56 'Working out the checksum of the chosen zip'
    $h = Get-Sha256 $script:LocalCopy
    Assert-NotCancelled
    $script:InstalledHash = $h

    if ($r.Hash) {
        if ($h -ne $r.Hash) {
            Dbg ('chosen zip sha256 ' + $h + ' is not the published ' + $r.Hash)
            # The same version is re-published with new files now and then, so a zip downloaded
            # in the browser yesterday is often an EARLIER build of this very tag. The release
            # lists every build it ever had (<asset>.sha256s); one of those is the real thing,
            # just not the newest - install it and say so, instead of installing nothing.
            if (Test-EarlierBuild $h) {
                Dbg 'chosen zip is an earlier published build of this version'
                Warn ('This zip is an earlier build of ' + $Tag + '. It is installed; download the zip again later to get the newest build.')
                Emit 'OLDERBUILD|'
                $script:HashVerified = $true
                $script:Used         = $r
                return
            }
            Fail 'LOCALWRONG' ('The chosen zip is not the published build of ' + $Tag + ' (checksum did not match).')
        }
        Dbg 'chosen zip matches the published checksum'
        $script:HashVerified = $true
        $script:Used         = $r
        return
    }
    # No checksum to compare with. A size the release DID list still has to match.
    if ($r.ApiOk -and $r.Url -and ($r.Size -gt 0) -and ($r.Size -ne $len)) {
        Fail 'LOCALWRONG' ('The chosen zip is not the published build of ' + $Tag + ' (' + $len + ' bytes, the release has ' + $r.Size + ').')
    }
    Warn 'The published checksum could not be read from GitHub; the chosen zip is checked by its contents only (it must open and contain the program).'
    Emit 'UNVERIFIED|'
}

if ($LocalZip) {
    Use-LocalZip
    $MaxRounds = 0      # no download rounds; straight on to opening the package
}

for ($round = 1; $round -le $MaxRounds; $round++) {
    $script:Replaced = $false
    $script:Gone     = $false
    $lastRound = ($round -ge $MaxRounds)

    if ($round -gt 1) {
        Step 'GitHub replaced the file while it was downloading - starting again'
        Dbg ('round ' + $round + ': looking the release up again')
    }
    Resolve-Asset
    Assert-NotCancelled
    Initialize-Cache

    # A complete, verified zip from an earlier run: reuse it instead of pulling
    # 122 MB down the shop's connection a second time.
    #
    # The cached file sat on a shop PC's disk between runs, so "it was correct when
    # we wrote it" is not the same as "it is correct now" - a failing disk or a
    # half-restored backup can change it underneath us. When a checksum is
    # published, re-check it here too. In Repair mode the whole point of the run is
    # that something on this PC is damaged, so a cache that cannot be PROVED good
    # (no published checksum) is thrown away rather than trusted.
    $haveDone = $false
    if (Test-Path -LiteralPath $DoneFile) {
        $dl = Get-FileLength $DoneFile
        if ($script:ExpectedSize -eq 0 -or $dl -eq $script:ExpectedSize) {
            $haveDone = $true
            if ($ExpectedHash) {
                Progress 5 'Checking the saved download'
                try {
                    $h = Get-Sha256 $DoneFile
                    Assert-NotCancelled
                    if ($h -ne $ExpectedHash) {
                        Dbg 'cached zip failed its checksum; downloading again'
                        $haveDone = $false
                    } else {
                        $script:InstalledHash = $h
                        $script:HashVerified  = $true
                    }
                } catch { Dbg ('could not checksum the cached zip: ' + $_.Exception.Message); $haveDone = $false }
            }
            elseif ($Repair) {
                Dbg 'repair run with no published checksum: not trusting the cached zip'
                $haveDone = $false
            }
            if ($haveDone) { Dbg 'reusing previously downloaded zip' }
            else { Remove-Item -LiteralPath $DoneFile -Force -ErrorAction SilentlyContinue }
        }
        else { Remove-Item -LiteralPath $DoneFile -Force -ErrorAction SilentlyContinue }
    }

    if ($haveDone) { break }

    # The 2 MB chunks and the joined file are on the disk together for a
    # moment, so the cache drive needs the zip twice over, less what an
    # earlier run already left there for this very build.
    $zipBytes = if ($script:ExpectedSize -gt 0) { [int64]$script:ExpectedSize } else { [int64]150MB }
    $cached = [int64]0
    Get-ChildItem -LiteralPath $CacheDir -Force -ErrorAction SilentlyContinue |
        Where-Object { (-not $_.PSIsContainer) -and (($_.Name -like ($AssetName + '.*.seg*')) -or ($_.Name -eq ($AssetName + '.part'))) } |
        ForEach-Object { $cached += $_.Length }
    Assert-FreeSpace $CacheDir ([Math]::Max([int64]0, 2 * $zipBytes - $cached) + 64MB) 'download Satpuda Core'

    Step 'Downloading Satpuda Core'

    $plain = $false     # one plain GET, no Range header (see Resolve-RangeTrouble)
    $probe = Get-RangeSupport $Url
    if (($probe.Status -eq 404) -or ($probe.Status -eq 416) -or
        ($script:ExpectedSize -gt 0 -and $probe.Total -gt 0 -and $probe.Total -ne $script:ExpectedSize)) {
        $what = ('range probe answered ' + $probe.Status + ', size ' + $probe.Total + ' (release said ' + $script:ExpectedSize + ')')
        if ((Resolve-RangeTrouble $lastRound $what) -eq 'restart') { continue }
        $plain = $true
    }
    if ((-not $plain) -and $script:ExpectedSize -eq 0 -and $probe.Total -gt 0) {
        $script:ExpectedSize = $probe.Total
        Dbg ('size from range probe: ' + $script:ExpectedSize)
    }
    Assert-NotCancelled

    $useSegments = (-not $plain) -and ($Segments -gt 1) -and $probe.Ranges -and ($script:ExpectedSize -gt 4194304) -and
                   ($probe.Total -eq 0 -or $probe.Total -eq $script:ExpectedSize)

    $ok = $false
    if ($useSegments) {
        # An old single-stream .part is not part of this layout and would only
        # confuse the size checks below.
        if (Test-Path -LiteralPath $PartFile) { Remove-Item -LiteralPath $PartFile -Force -ErrorAction SilentlyContinue }
        Dbg ('segmented download: ' + $Segments + ' connections')
        $ok = Invoke-SegmentedDownload $Url $script:ExpectedSize $Segments 4
        if ($ok) { Join-Segments $script:ExpectedSize }
        elseif ($script:Replaced) {
            if ((Resolve-RangeTrouble $lastRound 'a chunk answered 404/416 or another total size') -eq 'restart') { continue }
            $plain = $true
            Warn 'The parallel download got answers that do not add up; downloading in one piece instead.'
        }
        else {
            Warn 'The parallel download did not finish; falling back to a single connection.'
            Dbg 'segmented download failed; falling back to single stream'
            # Whatever the segments managed is still on disk and will be reused
            # if the shopkeeper runs the installer again with a better line.
        }
    } elseif ($plain) {
        Dbg 'single plain download without ranges'
    } else {
        Dbg 'single-stream download (server does not support ranges, or size unknown)'
    }

    if (-not $ok) {
        $ok = Invoke-SingleDownload $Url -NoRange:$plain
        if ((-not $ok) -and $script:Replaced -and (-not $plain)) {
            # A resumed single stream hit 404 or another total size.
            if ((Resolve-RangeTrouble $lastRound 'the single download answered 404 or another total size') -eq 'restart') { continue }
            $plain = $true
            $ok = Invoke-SingleDownload $Url -NoRange
        }
        if (-not $ok) {
            if ($script:Replaced) {
                # -NoRange already asked the release: it really changed.
                Clear-DownloadCache
                if (-not $lastRound) { continue }
                if ($script:Gone) { Fail 'GONE' 'The download link no longer exists on GitHub.' }
                Fail 'CHANGED' ('The file on release ' + $Tag + ' kept changing while it was being downloaded.')
            }
            Fail-Net 'The download could not be completed.'
        }
    }

    Assert-NotCancelled

    # ------------------------------------------------------- 3. verify --

    Step 'Checking the download'
    Progress 62 'Checking the downloaded file'

    if (-not (Test-Path -LiteralPath $PartFile)) { Fail 'NET' 'The download did not produce a file.' }
    $actual = Get-FileLength $PartFile

    if ($script:ExpectedSize -gt 0 -and $actual -lt $script:ExpectedSize) {
        if ((-not $lastRound) -and (Test-ReleaseChanged)) { Clear-DownloadCache; continue }
        # Short, not corrupt. Keep it: the next run resumes from here rather
        # than pulling the whole 122 MB down again.
        Fail 'NET' ('The download is incomplete (' + [math]::Round($actual / 1MB) + ' of ' +
                    [math]::Round($script:ExpectedSize / 1MB) + ' MB). Run the installer again to carry on from here.')
    }
    if ($script:ExpectedSize -gt 0 -and $actual -gt $script:ExpectedSize) {
        # Longer than the real asset means the file is genuinely wrong, so it
        # is worth throwing away.
        Remove-Item -LiteralPath $PartFile -Force -ErrorAction SilentlyContinue
        if ((-not $lastRound) -and (Test-ReleaseChanged)) { Clear-DownloadCache; continue }
        Fail 'SIZE' ('The downloaded file is the wrong size (' + $actual + ' bytes, expected ' + $script:ExpectedSize + ').')
    }
    if ($actual -lt 1048576) {
        Remove-Item -LiteralPath $PartFile -Force -ErrorAction SilentlyContinue
        Fail 'SIZE' 'The downloaded file is far too small to be Satpuda Core.'
    }

    if ($ExpectedHash) {
        Progress 63 'Verifying the checksum'
        $hash = Get-Sha256 $PartFile
        Assert-NotCancelled
        if ($hash -ne $ExpectedHash) {
            if ((-not $lastRound) -and (Test-ReleaseChanged)) {
                # The checksum on the release changed while we downloaded: the
                # file we have may well BE the new build (zip uploaded, sidecar
                # replaced a moment later). Keep it where the next round's
                # cache check tests it against the new checksum - a match
                # costs no second download.
                Dbg 'checksum did not match, but the release changed; checking the file against the new checksum'
                if (Test-Path -LiteralPath $DoneFile) { Remove-Item -LiteralPath $DoneFile -Force -ErrorAction SilentlyContinue }
                try { [System.IO.File]::Move((Get-LongPath $PartFile), (Get-LongPath $DoneFile)) } catch { Clear-DownloadCache }
                continue
            }
            Remove-Item -LiteralPath $PartFile -Force -ErrorAction SilentlyContinue
            Fail 'HASH' 'The downloaded file is damaged (checksum did not match).'
        }
        Dbg 'checksum ok'
        $script:InstalledHash = $hash
        $script:HashVerified  = $true
    } else {
        Dbg 'no checksum published; verified by size and zip structure only'
    }

    if (Test-Path -LiteralPath $DoneFile) { Remove-Item -LiteralPath $DoneFile -Force -ErrorAction SilentlyContinue }
    [System.IO.File]::Move((Get-LongPath $PartFile), (Get-LongPath $DoneFile))
    break
}

# What was installed, by content. With no published checksum the hash is still
# worked out here, so the record can be compared with a checksum published
# later for the same tag (a couple of seconds on 130 MB).
if (-not $script:InstalledHash) {
    Progress 64 'Recording the build'
    try { $script:InstalledHash = Get-Sha256 $DoneFile } catch { $script:InstalledHash = '' }
    Assert-NotCancelled
}

Assert-NotCancelled

# ------------------------------------------------------- 4. read the zip --

Step 'Opening the package'
Progress 65 'Opening the package'

# The zip was there a moment ago. Antivirus that quarantines a downloaded file
# does it in exactly this gap - that is not a damaged download, and saying
# "damaged, run it again" would only send the shop round the same loop.
if (-not [System.IO.File]::Exists((Get-LongPath $DoneFile))) {
    Fail 'AVZIP' ('The downloaded package was removed from ' + [System.IO.Path]::GetDirectoryName($DoneFile) +
                  ' right after it was saved, most likely by antivirus (' + $(if (Get-AvNamesText) { Get-AvNamesText } else { 'none reported' }) + ').')
}

$zip = $null
try { $zip = [System.IO.Compression.ZipFile]::OpenRead((Get-LongPath $DoneFile)) }
catch {
    Remove-Item -LiteralPath $DoneFile -Force -ErrorAction SilentlyContinue
    Fail 'ZIP' 'The downloaded package is damaged and could not be opened.'
}

# One entry out of the zip onto the disk. Per-file retry: antivirus and
# indexers grab freshly written files and hold them for a moment, and one
# sharing violation should not kill a 122 MB install. -Quiet: report failure
# by returning $false (the antivirus re-check below) instead of failing.
$script:CopyBuf = New-Object byte[] 262144
function Write-ZipEntry($e, [string]$path, [string]$rel, [switch]$Quiet) {
    for ($try = 1; $try -le 3; $try++) {
        $cancelledMidFile = $false
        try {
            $src = $e.Open()
            try {
                $dst = [System.IO.File]::Open((Get-LongPath $path), [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
                try {
                    # Hand-rolled instead of Stream.CopyTo: CopyTo does not
                    # come back until the whole file is written, and on a big
                    # entry that is a Stop click that appears to do nothing.
                    while (($n = $src.Read($script:CopyBuf, 0, $script:CopyBuf.Length)) -gt 0) {
                        $dst.Write($script:CopyBuf, 0, $n)
                        if (Test-Cancel) { $cancelledMidFile = $true; break }
                    }
                } finally { $dst.Dispose() }
            } finally { $src.Dispose() }
            if ($cancelledMidFile) {
                # The half-written file is inside staging, and staging is
                # deleted on the way out, so nothing survives this.
                Exit-Clean 3
            }
            return $true
        } catch {
            if (Test-Cancel) { Exit-Clean 3 }
            if ($try -eq 3) {
                if ($Quiet) { Dbg ('could not write ' + $rel + ' again: ' + $_.Exception.Message); return $false }
                Fail 'EXTRACT' ('Could not write ' + $rel + ': ' + $_.Exception.Message)
            }
            Start-Sleep -Milliseconds 350
        }
    }
    return $false
}

# The files of the package that are not on the disk at their full size.
function Get-MissingFiles($files) {
    $miss = New-Object System.Collections.Generic.List[object]
    foreach ($f in $files) {
        $ok = $false
        try {
            $fi = New-Object System.IO.FileInfo((Get-LongPath $f.Path))
            $ok = $fi.Exists -and ($fi.Length -eq $f.Len)
        } catch { $ok = $false }
        if (-not $ok) { $miss.Add($f) }
    }
    return ,$miss
}

function Write-MissingList($miss, [string]$when) {
    Dbg ([string]$miss.Count + ' file(s) missing ' + $when + ':')
    $i = 0
    foreach ($f in $miss) {
        if ($i -ge 20) { Dbg ('  ... and ' + ($miss.Count - 20) + ' more'); break }
        Dbg ('  ' + $f.Rel)
        $i++
    }
}

try {
    $entries = @($zip.Entries)
    if ($entries.Count -eq 0) { Fail 'ZIP' 'The downloaded package is empty.' }

    # The published zip wraps everything in one folder
    # (SatpudaCore_Desktop_Win10/...). Strip it, but only when every entry
    # really shares that one root - otherwise we would eat real files.
    $roots = @{}
    foreach ($e in $entries) {
        $fn = $e.FullName -replace '\\', '/'
        $i  = $fn.IndexOf('/')
        if ($i -gt 0) { $roots[$fn.Substring(0, $i)] = $true } else { $roots['']  = $true }
    }
    $strip = ''
    if ($roots.Count -eq 1 -and -not $roots.ContainsKey('')) {
        $strip = (@($roots.Keys)[0]) + '/'
        Dbg ("stripping top-level folder: " + $strip)
    }

    # ------------------------------------------- 5. extract to staging --
    #
    # First the whole list - where every file goes and how big it is - so the
    # package can be judged BEFORE anything is written: a zip without the
    # program, a folder too deep for Windows, a drive too full.

    $stagingFull = [System.IO.Path]::GetFullPath($script:Staging)
    # Compared against with the separator attached, so an entry that resolves to
    # a SIBLING whose name merely starts with "app.staging" cannot slip through
    # a bare StartsWith test.
    $stagingPrefix = $stagingFull.TrimEnd('\') + '\'
    $liveFull      = [System.IO.Path]::GetFullPath($LiveDir).TrimEnd('\')

    $files = New-Object System.Collections.Generic.List[object]
    $dirs  = New-Object System.Collections.Generic.List[string]
    $totalBytes = [int64]0
    $longest = ''
    foreach ($e in $entries) {
        $rel = $e.FullName -replace '\\', '/'
        if ($strip -and $rel.StartsWith($strip)) { $rel = $rel.Substring($strip.Length) }
        if ([string]::IsNullOrEmpty($rel)) { continue }
        $rel = $rel -replace '/', '\'

        $target = [System.IO.Path]::Combine($stagingFull, $rel)

        # Zip-slip guard: an entry named ..\..\something must not write outside
        # staging.
        $normalized = [System.IO.Path]::GetFullPath($target)
        if (-not $normalized.StartsWith($stagingPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            Fail 'ZIP' ('The package contains an unsafe path: ' + $e.FullName)
        }

        if ($e.Name -eq '' -or $rel.EndsWith('\')) { $dirs.Add($normalized); continue }   # directory entry

        $files.Add([pscustomobject]@{ Entry = $e; Rel = $rel.TrimStart('\'); Path = $normalized; Len = [int64]$e.Length })
        $totalBytes += [int64]$e.Length
        if ($rel.Length -gt $longest.Length) { $longest = $rel.TrimStart('\') }
    }
    if ($totalBytes -le 0) { $totalBytes = 1 }
    Dbg ("entries=" + $entries.Count + " files=" + $files.Count + " uncompressed=" + $totalBytes)

    # The program must be IN the package. Checked here, before ~900 MB is
    # written, rather than by looking for it afterwards - which is also what
    # tells "the zip has another shape" (LAYOUT) apart from "antivirus took
    # it off the disk" (AVBLOCK, below).
    $inZip = @{}
    foreach ($f in $files) { $inZip[$f.Rel.ToLowerInvariant()] = $true }
    if (-not $inZip.ContainsKey($SentinelExe.ToLowerInvariant())) {
        Fail 'LAYOUT' ($SentinelExe + ' was not found in the package. The published zip may have a different layout.')
    }
    $engineRel = 'engine\SatpudaEngine.exe'
    if (-not $inZip.ContainsKey($engineRel.ToLowerInvariant())) { Warn 'The data engine was not found in the package.' }

    # Windows' 260-character limit. The worker itself writes through \\?\ and
    # could unpack anything, but the program could not run from there: Python
    # cannot load a module from a path that long unless LongPathsEnabled is on
    # (it is off on most PCs, the build machine included).
    $longestLive = $liveFull.Length + 1 + $longest.Length
    $lpe = Get-RegValue 'LocalMachine' 'Registry64' 'SYSTEM\CurrentControlSet\Control\FileSystem' 'LongPathsEnabled'
    Dbg ('longest path once installed: ' + $longestLive + ' characters (' + $longest + '), LongPathsEnabled=' + $lpe)
    if (($longestLive -gt 259) -and ([int]$lpe -ne 1)) {
        Fail 'LONGPATH' ('The install folder is too deep for Windows: one of its files would have a path ' + $longestLive +
                         ' characters long, and Windows allows 260. Install into a shorter folder, for example C:\Satpuda Core.')
    }

    Assert-FreeSpace $AppRoot ($totalBytes + 64MB) 'unpack Satpuda Core'

    Step 'Installing files'
    New-Dir $script:Staging
    foreach ($d in $dirs) { New-Dir $d }

    $done = [int64]0
    foreach ($f in $files) {
        # Every entry, not every 25th: a single .pyd in this payload is tens of
        # megabytes, and one of them is the whole gap between a Stop click and
        # anything happening.
        if (Test-Cancel) { Exit-Clean 3 }
        New-Dir ([System.IO.Path]::GetDirectoryName($f.Path))
        [void](Write-ZipEntry $f.Entry $f.Path $f.Rel)
        $done += $f.Len
        Progress (65 + [int](($done / $totalBytes) * 29)) ('Installing files (' + [math]::Round($done / 1MB) + ' of ' + [math]::Round($totalBytes / 1MB) + ' MB)')
    }

    # ----------------------------------- 5b. did antivirus leave them? --
    #
    # Net Protector (Oct 2026) let SatpudaCore_Desktop.exe be written and then
    # deleted it, and the old check could only say "the package did not contain
    # Satpuda Core where it was expected" - wrong, and no help. Now every file
    # is counted right after unpacking AND again a few seconds later (that is
    # when antivirus strikes). Anything missing is unpacked once more; if it
    # goes again, the shop is told which antivirus to set an exclusion in, and
    # for which folder.
    Progress 94 'Checking that the files stayed in place'
    $miss = Get-MissingFiles $files
    if (($miss.Count -eq 0) -and ($AvSettleSecs -gt 0)) {
        Progress 95 'Checking that antivirus left the files in place'
        if (Wait-Cancellable ($AvSettleSecs * 1000)) { Exit-Clean 3 }
        $miss = Get-MissingFiles $files
    }
    if ($miss.Count -gt 0) {
        Write-MissingList $miss 'after unpacking (removed by antivirus?)'
        Warn ([string]$miss.Count + ' file(s) disappeared right after they were unpacked (antivirus?); unpacking them once more.')
        Step 'Unpacking removed files again'
        if (Wait-Cancellable 1500) { Exit-Clean 3 }
        foreach ($f in $miss) {
            if (Test-Cancel) { Exit-Clean 3 }
            try { New-Dir ([System.IO.Path]::GetDirectoryName($f.Path)) } catch { }
            [void](Write-ZipEntry $f.Entry $f.Path $f.Rel -Quiet)
        }
        if (Wait-Cancellable ([Math]::Max(2, $AvSettleSecs) * 1000)) { Exit-Clean 3 }
        $miss = Get-MissingFiles $files
        if ($miss.Count -gt 0) {
            Write-MissingList $miss 'after unpacking them again'
            $names = @($miss | Select-Object -First 3 | ForEach-Object { $_.Rel }) -join ', '
            $av = Get-AvNamesText
            Fail 'AVBLOCK' ('Antivirus' + $(if ($av) { ' (' + $av + ')' } else { '' }) + ' removed ' + $miss.Count +
                            ' file(s) right after they were unpacked, twice, including ' + $names +
                            '. Add this folder to its exclusions: ' + $AppRoot)
        }
        Dbg 'the files unpacked again stayed in place'
    }

    # Mark of the Web. Files this worker writes never carry one, but anything
    # that did ("downloaded from the internet") makes SmartScreen stop the
    # program on first start, so the programs are unblocked here regardless.
    foreach ($f in $files) {
        if ($f.Rel -match '\.(exe|dll|pyd|ps1|vbs|cmd|bat)$') {
            try { [System.IO.File]::Delete((Get-LongPath $f.Path) + ':Zone.Identifier') } catch { }
        }
    }
}
finally { if ($zip) { $zip.Dispose() } }

Assert-NotCancelled

# --------------------------------------------- 6. prove it before swapping --

Progress 95 'Checking the installed files'
$sentinel = Join-Path $script:Staging $SentinelExe
if (-not [System.IO.File]::Exists((Get-LongPath $sentinel))) {
    Fail 'AVBLOCK' ('Antivirus removed ' + $SentinelExe + ' right after it was unpacked. Add this folder to its exclusions: ' + $AppRoot)
}

# Written last, inside staging: its presence is the only proof the extraction
# ran to the end. The installer and the update check both read the version out
# of here, so it is the authoritative record of WHICH payload is on this disk -
# the installer's own AppVersion only says which installer put it there.
#
# It is also the record of WHICH BUILD is on this disk. The owner republishes
# builds into the same tag, so the tag alone cannot tell two builds apart; the
# sha256 of the zip can. The maintenance page (via -CheckOnly's SHA| line), the
# logon update check and the app's own "Check for updates" all compare it with
# the release's .sha256 and offer "the newest build of vX" when they differ.
# sha256 is the published checksum when there was one (sha256Verified true),
# otherwise the hash of the zip as downloaded. Because the record lives INSIDE
# the payload folder it is swapped in with it: it can never describe a payload
# that is not there.
$markerInfo = @{
    tag            = $Tag
    asset          = $AssetName
    bytes          = (Get-FileLength $DoneFile)
    sha256         = $script:InstalledHash
    sha256Verified = [bool]$script:HashVerified
    assetId        = $(if ($script:Used) { [string]$script:Used.Id } else { '' })
    assetUpdatedAt = $(if ($script:Used) { [string]$script:Used.Updated } else { '' })
    installedAt    = (Get-Date).ToString('s')
}
# A zip chosen in the browser says so; a downloaded one keeps the record as it was.
if ($LocalZip) { $markerInfo.source = 'local zip' }
$marker = $markerInfo | ConvertTo-Json
[System.IO.File]::WriteAllText((Get-LongPath (Join-Path $script:Staging '.satpuda-install.json')), $marker)

# ------------------------------------------------------------ 7. the swap --
#
# Past this point cancelling is refused: the old folder has been renamed aside
# and stopping half way is the one thing that WOULD leave the shop with nothing.
# It takes a second or two.

Step 'Finishing the installation'
Progress 96 'Putting the new files in place'
Emit 'NOCANCEL|'

$old = Join-Path $AppRoot ('app.old-' + [DateTime]::UtcNow.Ticks)

if (Test-Path -LiteralPath $LiveDir) {
    # Renaming the folder aside, rather than writing over it, is what keeps a
    # failure survivable: until both renames succeed the old install is still
    # the one on disk.
    #
    # A directory rename usually succeeds even while an .exe inside it is
    # RUNNING, because Windows opens executable images with FILE_SHARE_DELETE.
    # It does NOT succeed if something holds a file in there with no sharing at
    # all (measured: Access Denied). So the retries below are for transient
    # holders - antivirus and the search indexer scanning files we just wrote,
    # which typically let go within a few seconds - and a genuine lock falls
    # through to a message telling the user what to close.
    $renamed = $false
    for ($i = 1; $i -le 6; $i++) {
        try { [System.IO.Directory]::Move((Get-LongPath $LiveDir), (Get-LongPath $old)); $renamed = $true; break }
        catch {
            Dbg ("rename attempt " + $i + " failed: " + $_.Exception.Message)
            if ($i -lt 6) { Start-Sleep -Milliseconds (400 * $i) }   # 0.4s .. 2.0s, ~6s total
        }
    }
    if (-not $renamed) {
        # The downloaded zip is deliberately NOT deleted on this path, so the
        # retry costs the shopkeeper minutes, not another 122 MB.
        Fail 'LOCKED' 'The existing Satpuda Core folder is in use. Close Satpuda Core and any Explorer window showing its folder, then run this installer again.'
    }
}

try { [System.IO.Directory]::Move((Get-LongPath $script:Staging), (Get-LongPath $LiveDir)) }
catch {
    # Put the old install back rather than leaving the shop with nothing.
    if (Test-Path -LiteralPath $old) { try { [System.IO.Directory]::Move((Get-LongPath $old), (Get-LongPath $LiveDir)) } catch { } }
    Fail 'SWAP' ('Could not put the new files in place: ' + $_.Exception.Message)
}

# Once more, after the rename: an antivirus that scans on rename takes the
# program here, and an install without its program must not be called
# finished. The previous version, when there was one, goes back in its place.
$liveExe = Join-Path $LiveDir $SentinelExe
if (-not [System.IO.File]::Exists((Get-LongPath $liveExe))) {
    Dbg ($SentinelExe + ' is gone from the installed folder right after the swap')
    $broken = Join-Path $AppRoot ('app.broken-' + [DateTime]::UtcNow.Ticks)
    try { [System.IO.Directory]::Move((Get-LongPath $LiveDir), (Get-LongPath $broken)) } catch { $broken = $LiveDir }
    if ((Test-Path -LiteralPath $old) -and -not (Test-Path -LiteralPath $LiveDir)) {
        try { [System.IO.Directory]::Move((Get-LongPath $old), (Get-LongPath $LiveDir)); Dbg 'the previous version was put back' } catch { }
    }
    [void](Remove-Tree $broken)
    $av = Get-AvNamesText
    Fail 'AVBLOCK' ('Antivirus' + $(if ($av) { ' (' + $av + ')' } else { '' }) + ' removed ' + $SentinelExe +
                    ' as soon as it was put in place. Add this folder to its exclusions: ' + $AppRoot)
}

# Old copy is now dead weight. If a still-running exe holds it, let the reboot
# take it - never fail the install over cleanup.
if (Test-Path -LiteralPath $old) {
    if (-not (Remove-Tree $old)) {
        Remove-OnReboot $old
        Warn 'The previous version could not be deleted yet; Windows will remove it after the next restart.'
    }
}

# Reclaim the 122 MB cache now that it is installed.
try { if (Test-Path -LiteralPath $DoneFile) { Remove-Item -LiteralPath $DoneFile -Force } } catch { }

# Tell the installer which payload actually landed, so Programs and Features and
# the wizard show the version of the APP rather than the version of the .exe
# that fetched it.
Emit ("V|" + $Tag)
Progress 100 'Done'
Emit 'OK|'
Exit-Clean 0
