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
     OK|                finished

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
    [string] $OutFile   = ''                               # where -CheckOnly writes its answer
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # harmless here; see the header note

if ($Segments -lt 1)  { $Segments = 1 }
if ($Segments -gt 32) { $Segments = 32 }

# --------------------------------------------------------------- CheckOnly --
#
# The maintenance page asks "is there anything newer on GitHub?" before the
# shopkeeper picks Update, Repair or Remove. That is one short API call and it
# must never hold the wizard open: a tight timeout, no retries, and silence on
# failure - an offline shop still gets a working maintenance page.
if ($CheckOnly) {
    $tag = ''
    try {
        [Net.ServicePointManager]::SecurityProtocol =
            [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 12288 } catch { }
    } catch { }
    try {
        $r = [System.Net.HttpWebRequest]::Create("https://api.github.com/repos/$Repo/releases/latest")
        $r.UserAgent        = 'SatpudaCoreInstaller'
        $r.Accept           = 'application/vnd.github+json'
        $r.Timeout          = 4000
        $r.ReadWriteTimeout = 4000
        $resp = $r.GetResponse()
        try {
            $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
            try { $tag = ($sr.ReadToEnd() | ConvertFrom-Json).tag_name } finally { $sr.Dispose() }
        } finally { $resp.Close() }
    } catch { $tag = '' }
    if ($OutFile) {
        try { [System.IO.File]::WriteAllText($OutFile, ("LATEST|" + $tag)) } catch { }
    }
    [Console]::Out.WriteLine("LATEST|" + $tag)
    exit 0
}

# ---------------------------------------------------------------- plumbing --

function Emit([string]$line) {
    # [Console]::Out + explicit Flush: Write-Output buffers, and a buffered
    # progress line is a progress bar that does not move.
    [Console]::Out.WriteLine($line)
    [Console]::Out.Flush()
    if ($script:LogWriter) {
        try { $script:LogWriter.WriteLine(('{0:HH:mm:ss}  {1}' -f (Get-Date), $line)); $script:LogWriter.Flush() } catch { }
    }
}
function Step([string]$t)            { Emit ("S|" + $t) }
function Warn([string]$t)            { Emit ("W|" + $t) }
function Dbg ([string]$t)            { Emit ("D|" + $t) }
function Fail([string]$code,[string]$t) { Emit ("E|" + $code + "|" + $t); Exit-Clean 2 }

$script:LastPct = -1
function Progress([int]$pct, [string]$t) {
    if ($pct -lt 0) { $pct = 0 } elseif ($pct -gt 100) { $pct = 100 }
    if ($pct -ne $script:LastPct) {          # only on change: ExecAndLogOutput caps total output
        $script:LastPct = $pct
        Emit ("P|" + $pct + "|" + $t)
    }
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
    if ($code -ne 0) {
        # A staging folder is never a valid install. Leaving one behind is what
        # makes a broken install look finished, so it always goes.
        try { if ($script:Staging -and (Test-Path -LiteralPath $script:Staging)) { [void](Remove-Tree $script:Staging) } } catch { }
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

# GitHub is TLS 1.2+ only. PowerShell 5.1 inherits the .NET default, which on
# an un-updated Windows 10 can still be TLS 1.0 -> every download dies before it
# starts.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 12288 } catch { }  # TLS 1.3 when present
} catch { Warn "Could not raise the TLS level; download may fail on this PC." }
[Net.ServicePointManager]::Expect100Continue = $false
# .NET queues connections to the same host above this limit, so a 16-way
# segmented download with the old value of 8 would have run 8 at a time.
[Net.ServicePointManager]::DefaultConnectionLimit = [Math]::Max(32, $Segments * 2)

Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null

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

Step 'Looking up the download'
Progress 0 'Contacting GitHub'

$Url          = $DirectUrl
$ExpectedSize = 0
$ExpectedHash = ''

function Invoke-Api([string]$u) {
    $r = [System.Net.HttpWebRequest]::Create($u)
    $r.UserAgent = $UserAgent
    $r.Accept    = 'application/vnd.github+json'
    $r.Timeout   = 30000
    $r.ReadWriteTimeout = 30000
    $resp = $r.GetResponse()
    try {
        $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
        try { return $sr.ReadToEnd() } finally { $sr.Dispose() }
    } finally { $resp.Close() }
}

try {
    $json = Invoke-Api ("https://api.github.com/repos/$Repo/releases/tags/$Tag")
    $rel  = $json | ConvertFrom-Json
    foreach ($a in $rel.assets) {
        if ($a.name -eq $AssetName)             { $Url = $a.browser_download_url; $ExpectedSize = [int64]$a.size }
        elseif ($a.name -eq ($AssetName + '.sha256')) { $ShaAssetUrl = $a.browser_download_url }
    }
    if ($Url) { Dbg ("asset resolved, size=" + $ExpectedSize) }

    # Optional checksum sidecar. Publishing one is what upgrades verification
    # from "right number of bytes" to "byte-for-byte correct".
    if ($ShaAssetUrl) {
        try {
            $txt = Invoke-Api $ShaAssetUrl
            if ($txt -match '([0-9a-fA-F]{64})') { $ExpectedHash = $Matches[1].ToLower(); Dbg 'checksum sidecar found' }
        } catch { Dbg 'checksum sidecar could not be read' }
    }
} catch {
    # Rate limited, offline, or the release went private. We are not dead yet -
    # the direct asset URL may still work. Size then comes from the range probe.
    Dbg ("release API failed: " + $_.Exception.Message)
    Warn 'Could not read the release details from GitHub; falling back to the direct link.'
}

if (-not $Url) {
    Fail 'NOASSET' ("Could not find " + $AssetName + " on release " + $Tag + ".")
}

Assert-NotCancelled

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
    $info = @{ Ranges = $false; Total = [int64]0 }
    $req = $null; $resp = $null
    try {
        $req = [System.Net.HttpWebRequest]::Create($u)
        $req.UserAgent         = $UserAgent
        $req.Timeout           = 30000
        $req.ReadWriteTimeout  = 30000
        $req.AllowAutoRedirect = $true
        $req.AddRange([int64]0, [int64]0)
        $resp = $req.GetResponse()
        if ([int]$resp.StatusCode -eq 206) {
            $cr = $resp.Headers['Content-Range']         # "bytes 0-0/127827033"
            if ($cr -and ($cr -match '/(\d+)\s*$')) {
                $info.Ranges = $true
                $info.Total  = [int64]$Matches[1]
            }
        }
    } catch { Dbg ('range probe failed: ' + $_.Exception.Message) }
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
            }
            finally {
                $Sync['req' + $c] = $null
                if ($outs) { try { $outs.Dispose() } catch { } }
                if ($ins)  { try { $ins.Dispose()  } catch { } }
                if ($resp) { try { $resp.Close()   } catch { } }
            }

            if ($Sync['Cancel']) { return }

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
    $prefix = Join-Path $CacheDir ($AssetName + '.' + $total + '.c' + $ChunkBytes + '.seg')
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
function Invoke-SingleDownload([string]$u) {
    $maxAttempts    = 6
    $backoff        = @(2, 5, 10, 20, 30, 30)
    $noProgressRuns = 0

    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        Assert-NotCancelled
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
                    Fail 'NET' 'The download is not moving forward at all. The internet connection looks down.'
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

            if ($we -and $we.Response) {
                $code = [int]$we.Response.StatusCode
                if ($code -eq 416) {
                    # Range past end: our part file is bogus. Wipe and retry.
                    Dbg '416 - discarding part file'
                    if (Test-Path -LiteralPath $PartFile) { Remove-Item -LiteralPath $PartFile -Force }
                    continue
                }
                if ($code -eq 404) { Fail 'GONE' 'The download link no longer exists on GitHub.' }
                if ($code -eq 403) { Fail 'FORBID' 'GitHub refused the download. The release may not be public.' }
            }

            if ($attempt -ge $maxAttempts) {
                Fail 'NET' ('Download failed after ' + $maxAttempts + ' tries: ' + $msg)
            }
            if ($gained -eq 0) { $noProgressRuns++ } else { $noProgressRuns = 0 }
            if ($noProgressRuns -ge 3) {
                Fail 'NET' 'The download is not moving forward at all. The internet connection looks down.'
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

# A complete, verified zip from an earlier run: reuse it instead of pulling
# 122 MB down the shop's connection a second time.
#
# The cached file sat on a shop PC's disk between runs, so "it was correct when
# we wrote it" is not the same as "it is correct now" - a failing disk or a
# half-restored backup can change it underneath us. When a checksum is
# published, re-check it here too. In Repair mode the whole point of the run is
# that something on this PC is damaged, so a cache that cannot be PROVED good
# (no published checksum) is thrown away rather than trusted.
$script:ExpectedSize = $ExpectedSize
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

if (-not $haveDone) {
    Step 'Downloading Satpuda Core'

    $probe = Get-RangeSupport $Url
    if ($script:ExpectedSize -eq 0 -and $probe.Total -gt 0) {
        $script:ExpectedSize = $probe.Total
        Dbg ('size from range probe: ' + $script:ExpectedSize)
    }
    Assert-NotCancelled

    $useSegments = ($Segments -gt 1) -and $probe.Ranges -and ($script:ExpectedSize -gt 4194304) -and
                   ($probe.Total -eq 0 -or $probe.Total -eq $script:ExpectedSize)

    $ok = $false
    if ($useSegments) {
        # An old single-stream .part is not part of this layout and would only
        # confuse the size checks below.
        if (Test-Path -LiteralPath $PartFile) { Remove-Item -LiteralPath $PartFile -Force -ErrorAction SilentlyContinue }
        Dbg ('segmented download: ' + $Segments + ' connections')
        $ok = Invoke-SegmentedDownload $Url $script:ExpectedSize $Segments 4
        if ($ok) { Join-Segments $script:ExpectedSize }
        else {
            Warn 'The parallel download did not finish; falling back to a single connection.'
            Dbg 'segmented download failed; falling back to single stream'
            # Whatever the segments managed is still on disk and will be reused
            # if the shopkeeper runs the installer again with a better line.
        }
    } else {
        Dbg 'single-stream download (server does not support ranges, or size unknown)'
    }

    if (-not $ok) {
        if (-not (Invoke-SingleDownload $Url)) {
            Fail 'NET' 'The download could not be completed.'
        }
    }

    Assert-NotCancelled

    # ------------------------------------------------------- 3. verify --

    Step 'Checking the download'
    Progress 62 'Checking the downloaded file'

    if (-not (Test-Path -LiteralPath $PartFile)) { Fail 'NET' 'The download did not produce a file.' }
    $actual = Get-FileLength $PartFile

    if ($script:ExpectedSize -gt 0 -and $actual -lt $script:ExpectedSize) {
        # Short, not corrupt. Keep it: the next run resumes from here rather
        # than pulling the whole 122 MB down again.
        Fail 'NET' ('The download is incomplete (' + [math]::Round($actual / 1MB) + ' of ' +
                    [math]::Round($script:ExpectedSize / 1MB) + ' MB). Run the installer again to carry on from here.')
    }
    if ($script:ExpectedSize -gt 0 -and $actual -gt $script:ExpectedSize) {
        # Longer than the real asset means the file is genuinely wrong, so it
        # is worth throwing away.
        Remove-Item -LiteralPath $PartFile -Force -ErrorAction SilentlyContinue
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
            Remove-Item -LiteralPath $PartFile -Force -ErrorAction SilentlyContinue
            Fail 'HASH' 'The downloaded file is damaged (checksum did not match).'
        }
        Dbg 'checksum ok'
    } else {
        Dbg 'no checksum published; verified by size and zip structure only'
    }

    if (Test-Path -LiteralPath $DoneFile) { Remove-Item -LiteralPath $DoneFile -Force -ErrorAction SilentlyContinue }
    [System.IO.File]::Move((Get-LongPath $PartFile), (Get-LongPath $DoneFile))
}

Assert-NotCancelled

# ------------------------------------------------------- 4. read the zip --

Step 'Opening the package'
Progress 65 'Opening the package'

$zip = $null
try { $zip = [System.IO.Compression.ZipFile]::OpenRead((Get-LongPath $DoneFile)) }
catch {
    Remove-Item -LiteralPath $DoneFile -Force -ErrorAction SilentlyContinue
    Fail 'ZIP' 'The downloaded package is damaged and could not be opened.'
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

    $totalBytes = 0
    foreach ($e in $entries) { $totalBytes += $e.Length }
    if ($totalBytes -le 0) { $totalBytes = 1 }
    Dbg ("entries=" + $entries.Count + " uncompressed=" + $totalBytes)

    # ------------------------------------------------ 5. extract to staging --

    Step 'Installing files'
    New-Dir $script:Staging
    $stagingFull = [System.IO.Path]::GetFullPath($script:Staging)
    # Compared against with the separator attached, so an entry that resolves to
    # a SIBLING whose name merely starts with "app.staging" cannot slip through
    # a bare StartsWith test.
    $stagingPrefix = $stagingFull.TrimEnd('\') + '\'

    $done  = 0
    $copyBuf = New-Object byte[] 262144
    foreach ($e in $entries) {
        # Every entry, not every 25th: a single .pyd in this payload is tens of
        # megabytes, and one of them is the whole gap between a Stop click and
        # anything happening.
        if (Test-Cancel) { Exit-Clean 3 }

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

        if ($e.Name -eq '' -or $rel.EndsWith('\')) { New-Dir $normalized; continue }   # directory entry

        New-Dir ([System.IO.Path]::GetDirectoryName($normalized))

        # Per-file retry: antivirus and indexers grab freshly written files and
        # hold them for a moment. One sharing violation should not kill a
        # 122 MB install.
        $written = $false
        for ($try = 1; $try -le 3; $try++) {
            $cancelledMidFile = $false
            try {
                $src = $e.Open()
                try {
                    $dst = [System.IO.File]::Open((Get-LongPath $normalized), [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
                    try {
                        # Hand-rolled instead of Stream.CopyTo: CopyTo does not
                        # come back until the whole file is written, and on a big
                        # entry that is a Stop click that appears to do nothing.
                        while (($n = $src.Read($copyBuf, 0, $copyBuf.Length)) -gt 0) {
                            $dst.Write($copyBuf, 0, $n)
                            if (Test-Cancel) { $cancelledMidFile = $true; break }
                        }
                    } finally { $dst.Dispose() }
                } finally { $src.Dispose() }
                if ($cancelledMidFile) {
                    # The half-written file is inside staging, and staging is
                    # deleted on the way out, so nothing survives this.
                    Exit-Clean 3
                }
                $written = $true
                break
            } catch {
                if (Test-Cancel) { Exit-Clean 3 }
                if ($try -eq 3) { Fail 'EXTRACT' ('Could not write ' + $rel + ': ' + $_.Exception.Message) }
                Start-Sleep -Milliseconds 350
            }
        }
        if (-not $written) { Fail 'EXTRACT' ('Could not write ' + $rel) }

        $done += $e.Length
        Progress (65 + [int](($done / $totalBytes) * 30)) ('Installing files (' + [math]::Round($done / 1MB) + ' of ' + [math]::Round($totalBytes / 1MB) + ' MB)')
    }
}
finally { if ($zip) { $zip.Dispose() } }

Assert-NotCancelled

# --------------------------------------------- 6. prove it before swapping --

Progress 95 'Checking the installed files'
$sentinel = Join-Path $script:Staging $SentinelExe
if (-not (Test-Path -LiteralPath $sentinel)) {
    Fail 'LAYOUT' ($SentinelExe + ' was not found in the package. The published zip may have a different layout.')
}
$engine = Join-Path $script:Staging 'engine\SatpudaEngine.exe'
if (-not (Test-Path -LiteralPath $engine)) { Warn 'The data engine was not found in the package.' }

# Written last, inside staging: its presence is the only proof the extraction
# ran to the end. The installer and the update check both read the version out
# of here, so it is the authoritative record of WHICH payload is on this disk -
# the installer's own AppVersion only says which installer put it there.
$marker = @{
    tag        = $Tag
    asset      = $AssetName
    bytes      = $script:ExpectedSize
    sha256     = $ExpectedHash
    installedAt = (Get-Date).ToString('s')
} | ConvertTo-Json
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
