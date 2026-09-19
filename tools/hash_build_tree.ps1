<#
  hash_build_tree.ps1 -- the Windows half of the build-drift gate.

  WHY THIS EXISTS
  The desktop is built on the Windows PC from D:\satpuda-build\mac2, which is an
  incremental copy of the Mac tree. Nothing ever compared the two. On 2026-09-14
  the copy was wrong in the worst possible direction: widgets\activation_dialog.py
  in the build repo was the 2026-08-07 Firebase-era file, a month OLDER than the
  Mac's, and that is the activation dialog every shop received. It calls
  mark_pending_bootstrap() and never calls ensure_online_store_link(), so
  activation never creates the store on the server -- "licence not found", with no
  way out. widgets\searchable_combo.py shipped stale the same way.

  WHAT IT DOES
  Prints a manifest -- sha256, size, last-write time (UTC epoch) and relative path
  -- of every file that goes into a build. It READS ONLY: Get-ChildItem and
  Get-FileHash, nothing else. It writes a file only if you pass -Out yourself.
  tools\verify_build_tree.py on the Mac runs this over ssh and refuses the build
  on any difference.

  HOW IT IS RUN
    from the Mac (normal):   python3 tools/verify_build_tree.py check
    on the PC (locally):     powershell -NoProfile -ExecutionPolicy Bypass -File tools\hash_build_tree.ps1
    a different tree:        $Root='D:\other\mac2'; .\tools\hash_build_tree.ps1
                             (or set SATPUDA_BUILD_ROOT)

  The selector block below MUST stay byte-identical to the one in
  tools/verify_build_tree.py -- the manifest carries a hash of it, and the Mac
  refuses to compare two manifests that were not selected by the same rules.
  tests/test_a_stale_build_tree_is_refused.py fails if the two drift apart.
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not $Root)     { $Root = $env:SATPUDA_BUILD_ROOT }
if (-not $Root)     { $Root = 'D:\satpuda-build\mac2' }
if (-not $SideName) { $SideName = 'build-repo' }

# ---------------------------------------------------------------- selector ---
# v2 (2026-09-16): see the SELECTOR block in tools/verify_build_tree.py -- the
# spec-imported root modules and the vendor's Drive folder id were outside v1.
$SelectorVersion = '2'
$Dirs       = @('bill_templates','core','desktop/src','installer','ui','widgets')
$Files      = @('build_release_filter.py','main.py','pyinstaller_extra_bundle.py','pyinstaller_tk_bundle.py','pyinstaller_win7_runtime.py','config/drive_backup_folder.dat')
$Globs      = @('*.spec')
$SkipDirs   = @('.git','.idea','.vscode','__MACOSX','__pycache__','build','dist','node_modules','target','venv','.venv')
$SkipExts   = @('.bak','.log','.orig','.pyc','.pyo','.rej','.swp','.tmp')
$SkipNames  = @('.DS_Store','Thumbs.db','desktop.ini')
$SkipPrefix = @('._','~$')
# Paths that live under a build-input directory but are output, not input.
$SkipPaths  = @('installer/Output')

$SelectorLines = @(
  ('selector-version=' + $SelectorVersion),
  ('dirs=' + ($Dirs -join ',')),
  ('files=' + ($Files -join ',')),
  ('globs=' + ($Globs -join ',')),
  ('skip-dirs=' + ($SkipDirs -join ',')),
  ('skip-exts=' + ($SkipExts -join ',')),
  ('skip-names=' + ($SkipNames -join ',')),
  ('skip-prefixes=' + ($SkipPrefix -join ',')),
  ('skip-paths=' + ($SkipPaths -join ','))
)
$SelectorText = $SelectorLines -join "`n"
$Sha = [System.Security.Cryptography.SHA256]::Create()
$SelectorId = ([System.BitConverter]::ToString(
    $Sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($SelectorText)))).Replace('-','').ToLower()

# ------------------------------------------------------------------- walk ----
if (-not (Test-Path -LiteralPath $Root)) {
  Write-Output '#satpuda-build-manifest-begin v1'
  Write-Output ('#selector ' + $SelectorId)
  Write-Output ('#side ' + $SideName)
  Write-Output ('#root ' + $Root)
  Write-Output ('#fatal build tree not found: ' + $Root)
  Write-Output '#satpuda-build-manifest-end 0'
  exit 0
}
$Root = (Get-Item -LiteralPath $Root).FullName.TrimEnd('\')
$RootLen = $Root.Length + 1
$Epoch = [datetime]::SpecifyKind([datetime]'1970-01-01T00:00:00', 'Utc')

function Test-Skipped([string]$rel) {
  $low = $rel.ToLower()
  foreach ($p in $SkipPaths) {
    $pl = $p.ToLower()
    if (($low -eq $pl) -or $low.StartsWith($pl + '/')) { return $true }
  }
  $parts = $rel.Split('/')
  for ($i = 0; $i -lt ($parts.Length - 1); $i++) {
    if ($SkipDirs -contains $parts[$i]) { return $true }
  }
  $name = $parts[$parts.Length - 1]
  foreach ($p in $SkipPrefix) { if ($name.StartsWith($p)) { return $true } }
  if ($SkipNames -contains $name) { return $true }
  $ext = [System.IO.Path]::GetExtension($name)
  if ($ext -and ($SkipExts -contains $ext.ToLower())) { return $true }
  return $false
}

$Rows = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]

function Add-File($fullName, $length, $lastWriteUtc) {
  $rel = $fullName.Substring($RootLen).Replace('\','/')
  if (Test-Skipped $rel) { return }
  $hash = (Get-FileHash -LiteralPath $fullName -Algorithm SHA256).Hash.ToLower()
  $mtime = [int64][math]::Floor(($lastWriteUtc - $Epoch).TotalSeconds)
  $Rows.Add($hash + "`t" + $length + "`t" + $mtime + "`t" + $rel)
}

foreach ($d in $Dirs) {
  $dirPath = Join-Path $Root ($d -replace '/','\')
  if (-not (Test-Path -LiteralPath $dirPath)) {
    $Warnings.Add('missing-dir ' + $d)
    continue
  }
  Get-ChildItem -LiteralPath $dirPath -Recurse -File -Force | ForEach-Object {
    Add-File $_.FullName $_.Length $_.LastWriteTimeUtc
  }
}
foreach ($f in $Files) {
  $filePath = Join-Path $Root ($f -replace '/','\')
  if (Test-Path -LiteralPath $filePath -PathType Leaf) {
    $item = Get-Item -LiteralPath $filePath -Force
    Add-File $item.FullName $item.Length $item.LastWriteTimeUtc
  } else {
    $Warnings.Add('missing-file ' + $f)
  }
}
foreach ($g in $Globs) {
  Get-ChildItem -LiteralPath $Root -File -Force -Filter $g | ForEach-Object {
    Add-File $_.FullName $_.Length $_.LastWriteTimeUtc
  }
}

# ------------------------------------------------------------------ print ----
$Sorted = $Rows | Sort-Object -CaseSensitive { $_.Substring($_.LastIndexOf("`t") + 1) }
$Lines = New-Object System.Collections.Generic.List[string]
$Lines.Add('#satpuda-build-manifest-begin v1')
$Lines.Add('#selector ' + $SelectorId)
$Lines.Add('#side ' + $SideName)
$Lines.Add('#root ' + $Root)
$Lines.Add('#generated ' + (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))
foreach ($w in $Warnings) { $Lines.Add('#warn ' + $w) }
foreach ($r in $Sorted) { $Lines.Add($r) }
$Lines.Add('#satpuda-build-manifest-end ' + $Rows.Count)

if ($Out) {
  # Only ever taken when the operator asks for it by name. The Mac gate never does.
  Set-Content -LiteralPath $Out -Value $Lines -Encoding UTF8
  Write-Output ('#wrote ' + $Out + ' (' + $Rows.Count + ' files)')
} else {
  foreach ($l in $Lines) { Write-Output $l }
}
