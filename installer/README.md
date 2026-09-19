# Satpuda Core — Windows installer

Builds `SatpudaCoreInstaller.exe` for Windows 10 and 11 (64-bit).

The installer is **small (~2 MB)**. It does not carry the program inside it —
it downloads `SatpudaCore_Desktop_Win10.zip` (121.9 MB) from the GitHub release,
checks it, unpacks it, and puts it in Programs with a Start Menu and desktop
icon.

That means: **to ship a new version you publish a new zip, not a new installer.**

---

## This round: the VPS moved

The server changed address. `srv1892850.hstgr.cloud` is the old box and it dies
when that VPS lapses; `srv1970994.hstgr.cloud` is the new one. An already
installed shop has the **old** name saved in its own settings file, and the
saved value beats the program's default — so a new build alone would fix
nothing for it.

**The installer now corrects that file** on install, on update and on repair,
and only when what is saved is the known-dead host. → *The saved server address*

`MyAppVersion` is `1.0.2` for this. **Release `v1.0.2` must exist on GitHub with
`SatpudaCore_Desktop_Win10.zip` attached before this installer is handed to
anybody** — the download URL is built from the version, so a bump with no
matching release turns every install into a 404. → *Publishing a new version*

Everything below describes earlier rounds and is unchanged.

---

## Latest round: four changes

1. **No mode question.** The installer always activates the store **Online**.
   The wizard page asks one thing — the shop's name — and the handoff pins
   `sync_mode: "online"`. → *The one question, and the handoff to the app*
2. **An Administrator unlock** on that page puts the mode choice back for the
   owner, so he can set up an Offline shop himself. The password is stored as a
   salted, stretched hash, never in plaintext. → *The Administrator unlock*
3. **No "available stores", ever.** The installer never offers an existing shop
   from a list, and can no longer hand the app a handoff on a PC that already
   has one. → *No "available stores", ever*
4. **Modern UI.** `WizardStyle=modern` done properly — sizing, fonts, a real
   header logo, and both custom pages rebuilt so they measure their own text
   instead of guessing at 125% and 150% DPI. → *The wizard's look, and 125% /
   150% DPI*

Everything from the previous round — Cancel, the version display, the
maintenance page and the 16-segment download — is unchanged and still works.

---

## The previous round: the four faults the owner reported

### 1. Cancel did nothing

Not a flag-file problem. The flag file was never written, because the script's
`CancelButtonClick` **never ran**.

The download happens in `PrepareToInstall`, on a page made by
`CreateOutputProgressPage`. Inno gives that page the `psNoButtons` style
(`Setup.WizardForm.CustomPages.pas:806`), and `UpdateCurPageButtonState`
answers `psNoButtons` by hiding Back, Next and Cancel and greying the window's
X via `EnableMenuItem(..., SC_CLOSE, MF_GRAYED)`
(`Setup.WizardForm.pas:2250-2262`). Inno's own comment on that page says it
plainly: *"the user shouldn't be able to cancel or do anything else during this
time."* On top of that, `ShowPreparing` disables the Cancel button immediately
before calling the script's `PrepareToInstall` (`Setup.WizardForm.pas:1766`).

The old script tried to win that argument by poking `Visible` and `Enabled`
back on after `Show`. Nothing put the X back, so the X stayed dead, and the
whole cancel path still had to travel through `TMainForm.Close` and its
`CancelButton.CanFocus` guard before reaching script code.

**The owner's own install log proves the handler never ran.** A script `MsgBox`
goes through Inno's `LoggedMsgBox` (`Setup.ScriptFunc.pas:1276`), which writes
`Message box (Yes/No):` into the Setup log. His log has exactly one such
entry — the folder-already-exists prompt at the start — and none for *Stop
installing Satpuda Core?*. The log simply stops at 32 MB; he killed it.

Even if the flag *had* been written, the worker could not have answered
promptly: the download loop polled cancel only inside `$inStream.Read`, which
blocks for the whole `ReadWriteTimeout` on a stalled line; extraction polled
only every 25 zip entries; and each file was written with
`Stream.CopyTo`, which does not return until the whole file is done — and this
payload contains single entries tens of megabytes long.

**Fix.** A real **Stop button on the progress page's own surface** — which is
exactly what Inno's own `TDownloadWizardPage` and `TExtractionWizardPage` do
(`Setup.WizardForm.CustomPages.pas:1028` and `:1242`), each creating its own
`FAbortButton` rather than trusting the wizard's Cancel button on a
`psNoButtons` page. The wizard Cancel button and the X are repaired as well, so
all three do the same thing. In the worker, every slow operation now polls a
cached 250 ms cancel check, every sleep is interruptible, `CopyTo` is replaced
by a chunked copy loop, and the parallel downloader `Abort()`s its live sockets
so a thread parked in `Read` comes back at once. The worker prints its `PID|` as
its first line, and if it has not exited six seconds after the flag it is
`taskkill /T /F`-ed; the installer then removes the staging folder itself,
because a killed worker cannot.

Measured on the PC: **0.41 s to stop during the download** (16 segments, 33
established connections) and **0.60 s during extraction** (741 files already
written). In both cases `app.staging` and every part-written file were gone
afterwards, the live `app` folder was untouched, and the resumable download
state was kept.

### 2. It never showed the installed version

Two separate causes.

* Nothing in the wizard ever read or displayed a version. There was no code
  for it at all.
* In Programs and Features, `UninstallDisplayName={#MyAppName}` **replaces**
  the `AppVerName` Inno would otherwise write as `DisplayName`
  (`Setup.Install.pas:301-308`), so the entry read plain "Satpuda Core" with
  the number only in the easily-missed `DisplayVersion` column — and that
  column carried the *installer's* version, never the version of the payload
  actually on disk.
* On the owner's PC the entry was **missing entirely**: version state was only
  ever written in `CurStepChanged(ssPostInstall)`, so a run stopped after the
  payload landed but before the wizard finished left app files with no version
  recorded anywhere. That is exactly the state his PC was in — `app\` present
  with a valid `.satpuda-install.json`, `HKCU\Software\Satpuda\SatpudaCore`
  absent, and no uninstall key.

**Fix.** `UninstallDisplayName` now carries the version; `DisplayVersion`,
`DisplayIcon`, `InstallLocation` and `Publisher` are rewritten in
`ssPostInstall` from the tag the worker reports on its new `V|` line. The
wizard's first page states the installed version outright, read in this order:
`app\.satpuda-install.json` → `HKCU ...\Tag` → `HKCU ...\Version` → the exe's
own file version. If none of them answer it says so in words. If nothing is
installed it says *"Satpuda Core is not installed on this computer yet"* rather
than showing a blank. `satpuda_update.ps1` was changed to read the same
sources in the same order, so the three places can never disagree.

An install is also now **found by its files** when the registry has nothing
(`FindInstalledPath`), which is what makes the owner's half-registered PC
repairable instead of producing a second copy.

### 3. No way to repair a corrupted install

**Fix.** A maintenance page, shown first whenever an install is detected:

* **Update to vX.Y.Z** — only when the release this installer carries is newer
  than what is on disk.
* **Repair / Reinstall** — re-downloads and replaces the program files, keeping
  everything in `%LOCALAPPDATA%\VeterinaryApp`. Passes `-Repair` to the worker,
  which then refuses to trust a cached zip it cannot *prove* is good: with a
  published checksum it verifies and reuses it, without one it re-downloads.
* **Remove** — hands over to the real uninstaller.

Update and Repair skip the directory page and the two shop questions and go
back into the folder the program already occupies.

### 4. The download was too slow

**It was not `Invoke-WebRequest`.** This worker has never used it, and
`$ProgressPreference = 'SilentlyContinue'` was already set — it downloads with
a raw `HttpWebRequest` and a 1 MB buffer. That hypothesis was tested and
rejected before anything was changed.

The real cause is **per-connection throttling between this line and GitHub's
release-asset CDN**. The PC's link is 100 Mbps. Measured with `curl.exe`, 30 s
per run, against the real 121.9 MB asset:

| Connections | Throughput |
|---|---|
| 1 | 0.15 – 0.19 MB/s |
| 4 | 0.53 – 0.75 MB/s |
| 8 | 0.81 MB/s |
| 16 | 1.41 – 1.67 MB/s |
| 24 | 2.28 MB/s |
| 32 | 2.96 MB/s |

Near-linear scaling, so the ceiling is per-stream, not the wire.

**Fix.** The worker splits the asset into **16 byte ranges** and pulls them
concurrently through a runspace pool, each segment into its own `.part` file
whose *length is its resume state*. `ServicePointManager.DefaultConnectionLimit`
was raised from 8 (which would have silently queued half the segments) to
`max(32, segments × 2)`. If the server refuses `Range`, or the size is unknown,
it falls back to the original single-stream loop. `/Segments=4` on the command
line for a shop whose router cannot hold many connections.

**Before and after, both against the real 121.9 MB zip on the owner's PC:**

| | Throughput | Time for 121.9 MB |
|---|---|---|
| Before (from his own install log: 8 MB per ~41 s, four times running) | **0.20 MB/s** | ~10–11 minutes |
| After (16 segments, full cold run) | **1.94 MB/s** | **63 seconds** |

A **9.7× speed-up**. End to end — API lookup, download, join, verify, extract
2,644 entries / 274 MB, swap — the whole install took **70 seconds**.

---

## Files here

| File | What it is |
|---|---|
| `SatpudaCore.iss` | The Inno Setup script. This is the installer. |
| `satpuda_fetch.ps1` | Worker. Downloads, verifies, extracts, swaps. Runs as its own process during install. |
| `satpuda_update.ps1` | Update check. Runs at logon and from the Start Menu. |
| `satpuda.ico` | Icon, used for the setup exe, the shortcuts and the Programs and Features entry. |
| `wizard_logo.bmp`, `wizard_logo_150.bmp`, `wizard_logo_200.bmp` | The logo in the wizard's page header, at 100%, 150% and 200%. Setup picks the size nearest the screen's DPI. **All three must be next to the .iss or the compile fails.** |
| `make_wizard_logo.py` | Regenerates those three from `satpuda.ico` (needs Pillow). Run it if the icon changes. |
| `build_installer.bat` | Finds ISCC and compiles. Tells you what to install if Inno Setup is missing, and checks the three .bmp files are present before it starts. |
| `Output/SatpudaCoreInstaller.exe` | The compiled result (created by ISCC). |

---

## How to compile

Inno Setup is **not currently installed** on the build PC (`192.168.31.74`).
Install it once:

1. Download **Inno Setup 6.3 or newer** from <https://jrsoftware.org/isdl.php>
   (file `innosetup-6.5.x.exe`). 6.3 is the minimum — the script uses
   `ArchitecturesAllowed=x64compatible`, which older versions reject.
2. Run it and accept the defaults. It lands in
   `C:\Program Files (x86)\Inno Setup 6\`.
3. Compile:

```bat
cd /d D:\path\to\installer
build_installer.bat
```

or directly:

```bat
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" SatpudaCore.iss
```

The result is `Output\SatpudaCoreInstaller.exe`.

You can also just open `SatpudaCore.iss` in the Inno Setup IDE and press F9.

---

## Publishing a new version

> **Since 1.0.3 the installer installs the newest release on GitHub by itself.**
> At start-up it asks `releases/latest`; if that tag is newer than the version it
> was built as, it downloads that release's `SatpudaCore_Desktop_Win10.zip`.
> Offline, or when GitHub cannot be reached, it installs the version it was built
> as. So a new release only needs its zip published; rebuilding the installer is
> needed only when the installer itself changes. `MyAppVersion` is now the
> fallback version, and every release must still carry the zip under the same
> asset name.


Two edits, one place each.

**1. In `SatpudaCore.iss`, change one line:**

```
#define MyAppVersion     "1.0.3"
```

Everything else follows from it — the release tag becomes `v1.0.3`, the download
URL becomes the `v1.0.3` asset, the Programs and Features version updates, and
the update check starts comparing against `1.0.3`.

**The two edits go together, in this order: release first, installer second.**
The version is what the download URL is built from, so an installer bumped ahead
of its release does not degrade — it 404s on every single install. The file
currently says `1.0.2`; `v1.0.2` must therefore already be published, with the
zip attached, before that installer is given to anyone.

**2. On GitHub, create release `v1.0.3`** in `roshanghogale/exes-for-satpuda-core`
and attach:

- `SatpudaCore_Desktop_Win10.zip` — the built folder, zipped
- `SatpudaCoreInstaller.exe` — the installer you just compiled

The asset **names must not change**. The installer looks them up by name.

### The release must be PUBLIC

There is no token in this installer and there must never be one — a token
committed to a repo or shipped inside an exe is a token that leaks. The update
check and the download both use the public GitHub API. If the repo or the
release is made private, every shop silently stops receiving updates. The
update check detects this case specifically and tells the user to ask you to
make the release public.

### Recommended: publish a checksum

If you also attach a file named `SatpudaCore_Desktop_Win10.zip.sha256`
containing the hash, the installer verifies the download byte-for-byte instead
of only checking the size. One line on the build PC:

```powershell
(Get-FileHash SatpudaCore_Desktop_Win10.zip -Algorithm SHA256).Hash.ToLower() |
    Set-Content SatpudaCore_Desktop_Win10.zip.sha256
```

Attach that file to the release. It is optional — without it the installer
still checks the exact byte count from the GitHub API and verifies that the zip
opens and contains the app, which catches almost everything.

---

## How the zip must be shaped

The published zip has one folder at the top:

```
SatpudaCore_Desktop_Win10/
    SatpudaCore_Desktop.exe
    engine/SatpudaEngine.exe
    engine/_internal/...
    tools/SumatraPDF64.exe
    README.txt
```

The installer strips that single top folder automatically, and it also works if
you zip the *contents* instead. What it will **not** silently accept is a zip
where `SatpudaCore_Desktop.exe` cannot be found after stripping — it refuses to
install rather than leave a broken folder, and tells the user the layout
changed.

Both of the things that used to need cleaning up by hand are now fixed in
`build_desktop_win10_folder.bat`, so the shape above is what the build produces:

- **No `engine/config/`.** That is where the desktop shell used to write
  `desktop_api.log` while the app ran. Launching the app once on the build
  machine before zipping put *your* log inside the folder shipped to every
  shop, and worse, the shell holds that file open for the engine's whole run,
  which is exactly what made an in-place update fail with Access Denied on a
  folder rename. The shell now logs to
  `%LOCALAPPDATA%\VeterinaryApp\logs\desktop_api.log`
  (`desktop/src-tauri/src/lib.rs`, `engine_log_path`) — outside the folder an
  update replaces — and the build script deletes any `engine\config` an older
  shell left behind.
- **No MSI.** `installer/Satpuda Core_0.1.0_x64_en-US.msi` was **229 MB** of a
  473 MB download: a Tauri installer riding inside the payload that this
  installer downloads. It is still built, to the sibling folder
  `dist\SatpudaCore_Desktop_Win10_MSI\`, so it can be published separately for
  anyone who wants the MSI route. Do not copy it back in.

---

## The one question, and the handoff to the app

The installer asks a shop **one** thing: what the shop is called. It then
activates it **Online**, on the Satpuda server, always. When the installer is
used it asks *before* the download, and the app's first launch then activates
with nothing typed at all.

The wizard page sits between "choose a folder" and "ready to install", so a name
the server would refuse is refused in the second it is typed, not after a 200 MB
download. The checks are the server's own (`cleanStoreName` in
`src/services/provisionService.js`): whitespace collapsed, control characters
out, 2 to 60 characters, at least one letter or digit in any script.

### Why the mode question is gone

It used to ask a second question — Online or Offline. It does not any more, and
the reason is not tidiness.

**An Offline shop's expiry date lives on the shop's own computer.** A file on a
shop's own computer is a file the shop can change. Online, the date is the
server's, the till has no copy of it to edit, and nothing on the shop's side
decides when the licence ends. That is the owner's decision about his own
product, and it is not a preference the person running the installer gets to
have.

There is a second consequence, and it is the one that matters for the section
below: **Offline is the mode that leaves a store the server has never seen.** An
Online activation goes to `/api/provision/trial`, which creates a store and
returns its `store_id`, and the app pins this PC to that id
(`live._record_store_adoption` in `core/trial_activation.py`). From then on the
shop's identity is a random id, not its name. An Offline activation skips all of
that, and the store it leaves behind is identified by its **display name** the
first time anyone connects it to the server — which is the one condition under
which a shop can be matched against stores that already exist.

The owner can still set up an Offline shop himself. See **The Administrator
unlock** below.

**The handoff is one small file.**

```
%LOCALAPPDATA%\VeterinaryApp\provision.json
{"store_name": "Roshan Medical", "sync_mode": "online"}
```

That folder is the app's own data directory — the same place it keeps the store
registry, the licence and the databases. On the first launch the app reads the
file, activates, and renames it to `provision.done`. So it runs exactly once: a
second launch, or a crash between reading and activating, cannot start a second
trial.

Why a file and not the alternatives:

- **Not a registry value.** Retiring the handoff has to be possible for the user
  who runs the app. A value written by an elevated installer under `HKLM` is
  exactly what a standard user may not be allowed to delete — and a handoff that
  cannot be consumed asks for a new trial on every single launch.
- **Not a command-line flag.** The app is started from the desktop shortcut, the
  Start Menu, the `.exe` itself and after every update. A flag would be missing
  on most of those launches and repeated on the rest.
- Nothing in the file is secret, so a readable file in the user's own profile
  costs nothing. There is nothing the installer *could* carry that would still
  be a secret once the installer is downloadable.

**A different Windows user opens the app.** `%LOCALAPPDATA%` is per-user, and so
is the whole of the app's data — a second Windows user has none of the first
user's stores, licence or books. He is a genuinely fresh install, and he gets the
app's own activation screen. That is correct rather than a gap: his books would
be separate whatever the installer did. (The server still allows only one trial
per computer, so if the first user has already taken it he will be told so, with
his name already filled in.)

The one case where the file could land in the wrong profile is an all-users
install, `SatpudaCoreInstaller.exe /ALLUSERS`, where Setup is elevated and
`{localappdata}` is the *administrator's* folder rather than the shopkeeper's.
For that case only, the installer also writes

```
%PROGRAMDATA%\SatpudaCore\provision.json
```

which the app reads when its own per-user file is absent. It is taken **once per
Windows user** — the app records that in the user's own folder before it tries to
delete the shared copy, because ProgramData is shared and a standard user often
cannot remove a file an administrator created.

**Silent installs** answer on the command line instead:

```
SatpudaCoreInstaller.exe /VERYSILENT /StoreName="Roshan Medical"
```

An unusable name there is dropped rather than argued with; the app then asks the
same question on screen, which is the same place an unattended install would
have had to ask anyway.

`/Mode=offline` **on its own is now ignored** and logged as ignored. It needs
`/AdminUser` and `/AdminPass` with it — see the next section.

**Re-installs and upgrades do not ask again.** The page is skipped when this
Windows user already has a shop here (`stores\`, `stores_registry.dat`,
`activation.dat` or `provision.done` in the data folder). The app refuses to
provision over an existing till in any case, so the question would be noise.

---

## The saved server address (new in 1.0.2)

The VPS moved. `srv1892850.hstgr.cloud` is the old box — today it is a Caddy
bridge forwarding to `srv1970994.hstgr.cloud`, and it stops answering the day
that VPS lapses.

The trouble is that a shop which has been running for a while has the **old name
written into its own settings file**:

```
%LOCALAPPDATA%\VeterinaryApp\server_api_prefs.json      key: "api_base"
```

and the saved value **beats** the program's built-in default (`load_prefs()` and
`configured_base()` in `core\server_api.py`). So shipping a new build on its own
changes nothing for that shop — the new `.exe` reads the old file and keeps
calling the old box. The installer corrects the file.

### The rule, and it is deliberately narrow

> Rewrite the **host**, only the host, and only when that host is **exactly**
> `srv1892850.hstgr.cloud`. Scheme, port, path and query are carried across
> untouched, and every other key in the file is left byte for byte as it was.

Left alone, every time:

| Saved value | What happens | Why |
|---|---|---|
| `https://srv1892850.hstgr.cloud` | → `https://srv1970994.hstgr.cloud` | the dead name |
| `https://srv1892850.hstgr.cloud:8443/api?x=1` | → `https://srv1970994.hstgr.cloud:8443/api?x=1` | host only; the rest is kept |
| `https://api.satpudacore.online` | **untouched** | the Cloudflare tunnel — slower (~460ms a call against ~85ms direct, measured from a shop in Maharashtra) but it reaches the same server, and it is the escape hatch a shop uses when the direct host will not answer |
| `http://192.168.31.74:5000`, `http://localhost:8000` | **untouched** | somebody typed that on purpose |
| `https://staging.srv1892850.hstgr.cloud` | **untouched** | the test is on the whole host, never a substring |
| `https://srv1892850-backup.hstgr.cloud` | **untouched** | same |
| `https://api.satpudacore.online/srv1892850.hstgr.cloud` | **untouched** | the old name is in the path, not the host |
| `srv1892850.hstgr.cloud` (no scheme) | **untouched** | `migrate_api_base()` in the program adds the `https://` as well as correcting the name; doing half of that here would leave a value neither side finishes |
| no file, or no `api_base` key | **nothing is created** | that computer has never run the program, and the new build already defaults to the new host |

**A dead name is corrected; a live choice is never overruled.**

### When it runs

In `CurStepChanged(ssPostInstall)` — the one step a first **install**, an
**update** and a **repair** all pass through, and after the payload is on the
disk. **Remove** never reaches it.

It also writes the same two keys the program writes when it does this itself
(`_persist_base_migration`), so a file corrected by the installer and one
corrected by the app read the same:

```json
{
  "api_base": "https://srv1970994.hstgr.cloud",
  "api_base_migrated_from": "https://srv1892850.hstgr.cloud",
  "api_base_migrated_on": "2026-09-10",
  "admin_username": "admin",
  "admin_password": "..."
}
```

They are skipped if they are already there, rather than written twice.

### Why the version had to be bumped

The maintenance page decides there is something to apply by comparing this
installer's tag with the tag on the disk (`UpdateIsAvailable`). At 1.0.1 a shop
already on 1.0.1 would have been told *"this is the newest version"* while its PC
was still calling a box about to go dark.

**Repair does the whole job at the same version** — that path is deliberately
kept working, and the maintenance page now says so in as many words when the old
address is found:

> This computer is still set to the old server address. Installing, updating or
> repairing from here corrects it.

The bump is what makes the wizard *offer* the update instead of making the
shopkeeper find Repair.

### If it cannot be written

**The install still succeeds.** The correction is written to a temporary file
beside the original, read back, and only then swapped in — a half-written
settings file would cost the shop its saved user name and password too. If any
step fails the original is left exactly as it was, the setup log says which step
and why, and the Finished page says:

> Satpuda Core is installed, but the server address saved on this computer could
> not be updated: … The program corrects this itself the next time it starts, so
> there is nothing to do now.

That last sentence is true: `migrate_api_base()` runs on every launch.

### Two things it will not risk

- **A file this machine's code page cannot carry losslessly.** The bytes are read,
  widened, narrowed back and compared; if they are not identical the file is left
  alone. Fail closed.
- **A backslash escape inside the saved address.** Nothing that writes this file
  produces one, so a backslash means the file is not the shape this code
  understands — and a file it does not understand is a file it does not touch.

### /ALLUSERS

An all-users install runs elevated, so `%LOCALAPPDATA%` is the *administrator's*
folder, not the shopkeeper's — the same problem `WriteProvisionFile` has. There
is no ProgramData trick available here, because the thing being corrected is a
file that already exists inside one particular profile, so the installer walks
the profiles instead. It creates nothing: a profile that never ran Satpuda Core
has no `VeterinaryApp` folder and is skipped.

---

## The Administrator unlock

The shop-name page carries an **Administrator** button under a separator line.
Pressing it opens a user name and a password. Entering the owner's credentials
puts the mode question back on the page, so he can set up an Offline shop
himself. Nothing else about the wizard changes, and nothing but the mode
question is behind it.

Wrong credentials say so on the page, clear the password box and leave the
wizard where it is — a shopkeeper who pressed the button out of curiosity still
has an install to finish. Enter in either box means *Unlock*, and pressing
*Next* with something typed in the row tries the unlock rather than silently
skipping past it.

The same credentials work on the command line, and are the only way
`/Mode=offline` is honoured:

```
SatpudaCoreInstaller.exe /VERYSILENT /StoreName="Roshan Medical" /Mode=offline ^
    /AdminUser=... /AdminPass="..."
```

(Credentials on a command line are visible in that machine's process list while
Setup runs. The wizard page is the intended route; this exists for the owner's
own unattended installs.)

### What is actually stored, and what it is worth

**The credentials are not in the script.** What is in it is one line:

```
ADMIN_HASH = 'ee34ab1a…'
```

— a **salted, stretched SHA-256** of the user name and the password *together*:

```python
h = sha256((SALT + "|" + user.lower() + "|" + password).encode("ascii")).hexdigest()
for _ in range(20000):
    h = sha256((SALT + h).encode("ascii")).hexdigest()
```

`SALT` and the round count are the `ADMIN_SALT` and `ADMIN_ROUNDS` constants at
the top of `[Code]`, and that snippet is exactly what `AdminDigest` does in
Pascal. To rotate the password later, run those four lines and replace
`ADMIN_HASH`; nothing else changes. The password itself is unchanged and stays
unchanged — this is only a change in how the installer *checks* it.

The user name is hashed **into** the same digest rather than compared on its
own, because a plaintext user name sitting next to a hash gives away half the
answer for free.

**Be clear about what this buys.** An installer is a file anybody can download,
and an Inno Setup installer can be unpacked and its script read — the salt, the
round count and the digest all come out of it in a few minutes. The 20,000
rounds turn a dictionary attack from instant into slow. That is the whole of it.

> **This stops casual use. It does not stop a determined attacker.**

It is worth having anyway, for the honest reason: what is behind it is a *setup
choice*, not a shop's money or a shop's books. The thing that must not be
guessable — the vendor administrator password that could reach every shop on the
account — is deliberately **not in this build at all**, and never was; see the
note at the top of `core/trial_activation.py` about why the fresh-install path
carries no credential. Nothing was weakened to add this button.

The setup log records only that an unlock was refused and how many times. It
never records either value.

---

## No "available stores", ever

**The installer must never put an existing shop in front of anyone, and must
never hand the app a name that can resolve to one.** Three ways it could have,
and where each is closed:

**1. A list on the wizard.** There is none. The installer holds no credential it
could ask the server for stores with — `provision_trial` in `core/server_api.py`
is an unauthenticated call by design — so it could not build a list of server
stores, and it must not build one out of local `Store_*` folders either. The one
screen in the product that *does* list stores is Settings → Data & System →
Stores, behind the app's own administrator area, which is where a shop that
genuinely has to re-join a store on the server belongs.

**2. The name reaching an existing store.** In Online mode it cannot.
`/api/provision/trial` only ever INSERTs (server `src/services/provisionService.js`),
and the PC is then pinned to the `store_id` the server just created — so nothing
later matches this shop to a server store by its display **name**. Offline is
the mode that skips that pin, which is why it is now behind the Administrator
password.

**3. A handoff landing on a PC that already has a shop.** *This one was
genuinely open, and it is the bug this round fixed.* `WriteProvisionFile` only
checked that a name had been collected — but `/StoreName=` fills that in even
when the wizard page was skipped. So re-running the installer with `/StoreName=`
on a PC that already had store folders wrote a handoff asking for a **brand new
store beside them**. The app's own guard (`already_set_up` in
`core/trial_activation.py`) is `has_registry` *and* activated, so a PC with
store folders that never finished activating went straight through it, ended up
with two stores, and the only way back out is the store list in Settings.

The handoff is now refused on exactly the same test the wizard page is skipped
on (`ForbidStoreChoice`), so "we did not ask" and "we do not answer" cannot
drift apart. A PC with the program installed but no shop yet is still a fresh
shop and a silent `/StoreName=` install of it still works — that case is
deliberately not included.

---

## The wizard's look, and 125% / 150% DPI

`WizardStyle=modern` was already set; what was missing was everything around it.

- **`WizardSizePercent=120`.** The modern header costs vertical room, and both
  custom pages had grown. At the default size they stack edge to edge.
- **A real header logo** — `WizardSmallImageFile`, three sizes (100%, 150%,
  200%), so a scaled shop PC gets a sharp logo rather than a 55-pixel bitmap
  stretched by the window manager. `satpuda.ico` is a solid black tile, so
  `make_wizard_logo.py` rounds its corners and lays it on white, which is what
  the modern page header behind it is.
- **Fonts deliberately *not* pinned.** `Default.isl` leaves `DialogFontName`
  empty, which means "the system UI font" — Segoe UI 9 on Windows 10 and 11,
  the only place this runs. So the modern style is already on the right font,
  and writing `DialogFontName=Segoe UI` would change nothing on a normal machine
  while taking away the larger font from the one shopkeeper who has turned
  Windows' *Make text bigger* up. The pages can afford to follow him now,
  because none of them assumes a font metric any more.

**The DPI work is the part that mattered.** Controls built in code do **not**
scale themselves:

- Every `TNewRadioButton` on both custom pages had **no height set at all** — so
  it stayed 17 pixels tall while its caption was drawn at 150%. Fixed: they are
  `ScaleY(17)`, the height Inno's own `CodeClasses.iss` example uses.
- Wrapped labels were given guessed heights (`ScaleY(28)`, `ScaleY(26)`) that
  were right for one font at one size. Fixed: `LayoutLabel` in `[Code]` sets the
  caption, calls **`TNewStaticText.AdjustHeight`** — which measures the text as
  it is actually being drawn — and only then decides where the next control
  goes. Nothing on either page is guessed any more.
- The maintenance page's climb up from the bottom edge was a ladder of magic
  numbers (18, 50, 68, 86, 106, 112) that only added up at one font size. Every
  step now comes off a real control height.
- Buttons are sized with `WizardForm.CalculateButtonWidth`, so a caption cannot
  outgrow its button.

Both custom pages now use the same separator, the same spacing and the same
measure-then-place rule, so they read as part of the modern wizard rather than
as two custom pages bolted onto it.

---

## What gets installed where

```
%LOCALAPPDATA%\Programs\Satpuda Core\      <- install folder
    satpuda.ico
    satpuda_update.ps1
    satpuda_update_silent.vbs
    unins000.exe
    app\                                   <- the downloaded program lives here
        SatpudaCore_Desktop.exe
        engine\ tools\ ...
```

Shop data stays where it already is, at `%LOCALAPPDATA%\VeterinaryApp\`, and is
**not** touched by install, upgrade or uninstall unless the user ticks the box.

**Per-user, not per-machine — on purpose.** The install goes under the user's
own AppData and needs no administrator password. Reasons:

- The data is already per-user, so a shared per-machine program folder would
  give two Windows users the same app and different books.
- A shopkeeper on a standard account can install and update it himself.
- No UAC prompt at any point.
- The desktop shortcut goes in a folder that user is guaranteed to own, which
  removes the most common cause of the icon failing.

If you ever do want one copy for all users of a PC, run
`SatpudaCoreInstaller.exe /ALLUSERS` from an admin account. The script supports
it; it just is not the default.

`PrivilegesRequiredOverridesAllowed` is set to `commandline` **only**. It used
to also include `dialog`, which makes Inno open with a "Select Setup Install
Mode" page offering "Install for all users" behind a UAC shield — the one
choice a shopkeeper on a standard account cannot complete, on the very first
screen, before he has agreed to anything. `/ALLUSERS` still works; the dead-end
dialog is gone. Do not add `dialog` back.

---

## Uninstall

Settings → Apps → **Satpuda Core** → Uninstall, or the entry in Programs and
Features. It removes the program folder, the shortcuts (at the real paths they
were created at, which matters when the desktop was redirected), the download
cache, and its registry key.

It shows a checkbox, **unticked**, offering to delete the shop's data as well.
Leave it unticked and the bills, stock and settings survive for a reinstall.
For scripted use: `unins000.exe /VERYSILENT /REMOVEDATA`.

---

## What was actually tested, and what was not

### This round (the four changes at the top)

**Not compiled.** Compiling belongs to the next step, as it did last round. What
*was* done instead, on the build PC's own Inno Setup 6.7.3 rather than from
memory:

| Checked | How | Result |
|---|---|---|
| `GetSHA256OfString` exists, and what it hashes | The registration signature pulled straight out of `Setup.e32` | `function GetSHA256OfString(const S: AnsiString): String;` — the **AnsiString** overload, one byte per character. That is why the Python derivation of `ADMIN_HASH` encodes `ascii`, and why `AdminDigest` calls this and not `GetSHA256OfUnicodeString`. |
| Hex case | — | Not assumed. Every round is put through `LowerCase` before it is fed back, so whichever case Inno returns cannot change the chain. |
| `TPasswordEdit`, and its `Password` property | `ISCmplr.dll` class table | Present; `TCustomEdit` descendant, `Password: Boolean` exposed. |
| `TNewStaticText.AdjustHeight` | `ISCmplr.dll` class table | `function AdjustHeight: Integer` — present. |
| `TBevel`, `TNewEdit.OnKeyPress`, `WizardForm.CalculateButtonWidth` | `ISCmplr.dll`; `Examples\CodeClasses.iss` | All present; `CalculateButtonWidth` and the `AutoSize := False; WordWrap := True; … AdjustHeight` idiom are taken from Inno's own example. |
| `WizardSizePercent`, `WizardSmallImageFile` | `ISCmplr.dll` directive table | Present. |
| Dark mode would not put a white logo on a dark header | `whatsnew.htm` | Dark rendering needs an explicit `WizardStyleFile`. None is set, so the header is white. |
| Control heights | `Examples\CodeClasses.iss` | edit `ScaleY(23)`, radio `ScaleY(17)`, button `ScaleY(23)` — the numbers now used. |
| **`ADMIN_HASH` is the right digest** | Re-derived in Python from the `ADMIN_SALT` / `ADMIN_ROUNDS` constants *read back out of the .iss*, against the owner's real credentials | Matches. Also checked: a wrong password, a wrong user name and empty input all fail; the user name is case- and whitespace-tolerant (`SatpudaCore`, `  satpudacore ` both match) and the password is **not** (`Satpuda Core` fails), which is what `AdminDigest` does. |
| No plaintext credential anywhere | grep over `.iss`, `.bat`, `.py`, `README.md` | None. The only `SatpudaCore` strings are the pre-existing registry key path. |
| `begin`/`end`/`case`/`try` balance, and every routine declared before its first caller | The same comment-and-string-stripping lexer as last round | Balances to zero; 64 routines, no forward use. |

**Not verified, and the next person should:**

- **That the compile succeeds.** One directive is being taken on documented
  behaviour rather than checked against the binary: the comma-separated list in
  `WizardSmallImageFile=wizard_logo.bmp,wizard_logo_150.bmp,wizard_logo_200.bmp`.
  Inno 6 selects the entry nearest the screen's DPI from such a list. If ISCC
  rejects it, the fix is one line — drop it to `WizardSmallImageFile=wizard_logo.bmp`
  and lose only the sharper logo on scaled screens.
- **That the unlock actually opens with the real credentials.** Type them into
  the Administrator row. A refusal writes `administrator unlock refused
  (attempt N)` to the setup log; acceptance writes `administrator unlock
  accepted`. Neither line records either value. If it refuses a correct
  password, the derivation is the suspect — re-run the four Python lines under
  *The Administrator unlock* and compare with `ADMIN_HASH`.
- **That both custom pages fit at 125% and 150%** with no clipping, and that the
  Administrator row and the mode radios each fit in the strip they share. The
  height budget they were written against is in the comment above
  `CreateSetupQuestionsPage`.

### The previous round (the four faults above)

`SatpudaCore.iss` was **not compiled** in this round — compiling is the next
step and belongs to whoever does it. It was checked statically instead: the
`begin`/`end`/`case`/`try` nesting balances to zero through a lexer that strips
comments and string literals; every routine is declared before its first caller
(Pascal Script is single-pass); and every Inno API, type and constant used was
verified **against the Inno Setup 6.7.3 source itself**, not from memory — the
version the build PC has. Specifically checked there: `HKA`
(`Compiler.ScriptFunc.pas:314`), `TOnLog`'s exact signature
(`Compiler.ScriptFunc.pas:175`), `TWizardPage.Surface` / `SurfaceWidth` /
`SurfaceHeight` / `ID` (`Compiler.ScriptClasses.pas:481-489`),
`TControl.Repaint`, `TButton.OnClick: TNotifyEvent`, `TForm.Close`,
`TWinControl.Handle: HWND`, and that `HWND` and `THandle` are both `NativeUInt`
so the two `user32` imports are correct on a 32- or 64-bit Setup.

Both `.ps1` files parse cleanly and were run end to end on the real PC.

| Checked, this round | Result |
|---|---|
| Both `.ps1` files parse | Clean, via `[Parser]::ParseFile` |
| **Full cold install, 16 segments** | 121.9 MB in **63 s (1.94 MB/s)**, joined in 0.4 s, 2,644 entries / 274 MB extracted in 5 s, swapped, exit 0, marker written, cache reclaimed. **70 s total** |
| **Download speed, before** | 0.20 MB/s, read off the owner's own Setup log (8 MB per ~41 s, four consecutive samples) |
| **Connection scaling** | 1 → 0.15-0.19, 4 → 0.53-0.75, 8 → 0.81, 16 → 1.41-1.67, 24 → 2.28, 32 → 2.96 MB/s |
| **Stop during download** | **0.41 s** to exit code 3, with 16 segments and 33 established connections open. No `app.staging`, no `app`, empty install root; 28.4 MB kept for resume |
| **Stop during extraction** | **0.60 s** to exit code 3 after 741 files had been written into staging. Staging gone, cached zip kept |
| **Resume after a stop** | Stopped at 35.8 MB across 16 segment files; the re-run reported `Downloading 36 of 122 MB` 1.2 s in and pulled only the remaining 86 MB |
| **Resume after a hard kill** | Same, from a worker killed outright mid-transfer rather than asked to stop |
| `-Repair` cache policy | Without `-Repair`: `reusing previously downloaded zip`. With `-Repair` and no published checksum: `not trusting the cached zip`, re-downloads |
| `-CheckOnly` | Returns `LATEST\|v1.0.1` in about a second, to stdout and to the named file |
| Marker parsing | The Pascal string walk over the real `.satpuda-install.json` yields `v1.0.1` and `2026-09-10`, and is not fooled by the colons inside the timestamp |

Not exercised in this round, and worth doing on the first compiled build: the
maintenance page's Remove path (it launches the real uninstaller), the
`taskkill` fallback (the worker always stopped on its own, well inside the six
second window), and a `Range`-refusing server forcing the single-stream
fallback.

### Earlier rounds

**The installer script itself was UNCOMPILED in these rounds.** Inno Setup was
not on the build PC at the time (it is now — 6.7.3), so `SatpudaCore.iss` had
never been through ISCC. It was read line by line, its `begin`/`end` nesting
checked by a lexer, and every Inno API it calls verified against the official
documentation — but expect to fix a typo or two on the first compile.

The payload was **473 MB** when the tests below were run; it is 121.9 MB now,
so read those byte counts as historical, not as what a fresh install moves.

The setup-questions page added later (`CreateSetupQuestionsPage`,
`NextButtonClick`, `WriteProvisionFile`) is in the same position: statically
checked only. Three things to look at on the first compile — `CreateCustomPage`
with hand-built `TNewEdit` / `TNewRadioButton` controls, `SaveStringsToUTF8File`
(Inno 6; the app reads the file as `utf-8-sig`, so the byte-order mark it writes
is expected), and `IsAdminInstallMode`. And one thing to actually *try* rather
than read: install with a Devanagari shop name and confirm the app opens
activated, not asking.

A later review pass found one of those and fixed it, so do not read the
paragraph above as "it will probably compile":

> `AppIsRunning` passed a `String` to `LoadStringFromFile`, whose second
> parameter is declared `var S: AnsiString`. A `var` parameter needs an exact
> type match, so that was a hard compile error on line 1 of the first build,
> not a warning. The variable is now an `AnsiString` and is widened by
> assignment (not by a `String(...)` cast, which Pascal Script does not
> reliably support).

The same review re-ran the PowerShell worker end to end on the real PC after
its own edits, so `satpuda_fetch.ps1` and `satpuda_update.ps1` are known to
parse and run as shipped. `SatpudaCore.iss` is still only statically checked.

Everything that *could* be run, was run, on the real Windows machine (build
26200, PowerShell 5.1, .NET 4.8.1, `LongPathsEnabled=0`). Two small hardening
changes were made to `satpuda_fetch.ps1` *after* the full real install run —
retrying a silently truncated stream, and the exception unwrap in item 2 below.
Neither touches the path that run exercised, but they have not themselves been
through a 473 MB download.

| Checked | Result |
|---|---|
| Both `.ps1` files parse | Clean, via `[Parser]::ParseFile` |
| **Full real install, end to end** | Downloaded the live 473 MB release, verified it, extracted **3,696 entries / 938,120,135 bytes**, swapped it in, exit code 0. `SatpudaCore_Desktop.exe`, `engine\SatpudaEngine.exe` and `tools\SumatraPDF64.exe` all landed at byte sizes matching the build folder |
| **Resumable download** | Killed mid-transfer at 303,038,464 bytes; the re-run logged `Resuming download at 289 MB` and pulled only the remaining 192,908,962 bytes via a Range request instead of starting over |
| Top-folder strip on the real zip | `stripping top-level folder: SatpudaCore_Desktop_Win10/` — `app\` contains the program, not a nested folder |
| Cleanup after success | No `app.staging`, no `app.old-*`, cache zip and `.part` both removed |
| Long paths | A 312-character destination path extracted and read back, with `LongPathsEnabled=0` |
| **Locked file during upgrade** | Held an exclusive handle inside `app\`; the swap refused, **left the existing install intact and working**, left no `app.staging`, and produced an actionable message |
| Version comparison | 10 cases pass, including `1.0.10` > `1.0.9` and `v` prefixes |
| Update check: no internet | Correctly classified (`NameResolutionFailure`) |
| Update check: private / 404 release | Correctly classified (HTTP 404) |
| Update check: rate limit | Reasoned from the documented headers, not triggered — exhausting the 60/hour limit would have blocked the PC's own GitHub access for an hour |
| Generated startup `.vbs` | Produces the exact intended command line |
| Desktop folder resolution | Probed on the real machine (see below) |

### One assumption that testing disproved

The swap renames `app` aside rather than writing over it. The original comment
claimed this always survives a locked file, because Windows lets you rename a
directory containing a *running* `.exe`. Measured: that is true for a running
executable, but **not** for a file some process holds with no sharing — the
rename fails with Access Denied.

The design still behaves correctly, which is the point of renaming rather than
overwriting: the old install stayed intact and usable, no half-written folder
was left, and the user got a sentence telling them what to close. The retry
window was widened to about 6 seconds (antivirus and the search indexer grab
freshly written files and let go shortly after), and the message now says the
download is already saved so the retry costs minutes rather than another
473 MB. `engine/config/desktop_api.log` was the file most likely to be held open
inside `app\` while the program runs; the shell now keeps its log in
`%LOCALAPPDATA%\VeterinaryApp\logs\` instead, so nothing the shell itself
opens lives inside the folder an update renames.

This was then re-tested against the shipped script: a clean install, an upgrade
over it with `engine\SatpudaEngine.exe` held open with `FileShare.None`, and a
retry once the handle was released. The locked run stopped at `E|LOCKED`, the
previous install and a canary file inside it both survived untouched, no
`app.staging` or `app.old-*` was left behind, and the downloaded zip stayed in
the cache so the retry cost seconds. The retry then installed normally.

Two bugs were found only by running things, and both were silent:

1. PowerShell has a built-in alias **`lp` for `Out-Printer`**, and aliases beat
   functions in name resolution. A helper named `LP` meant every filesystem
   path was being sent to the printer instead of being converted to a long-path
   form. Renamed to `Get-LongPath`. Every other helper name was then checked
   against the built-ins; none collide.

2. PowerShell wraps anything thrown by a .NET method call in a
   `MethodInvocationException`, so `$_.Exception -as [System.Net.WebException]`
   returns `$null`. The download loop's HTTP 416 / 404 / 403 handling was
   therefore **dead code** that could never run. Fixed by unwrapping
   `InnerException`, and verified against real 404, 416 and DNS-failure
   responses. (`satpuda_update.ps1` was never affected: a *typed*
   `catch [WebException]` does unwrap, which is why its error classification
   tested correctly.)

### The build PC is itself an example of failure 1

Probing it found:

```
Shell Folders\Desktop  = C:\Users\win10\OneDrive\Desktop     <- what Explorer shows
C:\Users\win10\Desktop                          also exists  <- stale, invisible to the user
```

The Desktop is redirected into OneDrive **and** the old local folder is still
lying there. An installer that writes to `%USERPROFILE%\Desktop` — the obvious
path — puts the icon in a folder the shopkeeper never sees, and he reports "no
icon". That is why the shortcut code asks the registry for the *real* desktop
first and only falls back to `%USERPROFILE%\Desktop` later.

## When a shop reports a problem, ask for these

Three logs, all plain text, all on the shop's PC:

| Log | Where | What it tells you |
|---|---|---|
| Installer log | `%TEMP%\Setup Log *.txt` | Every line the worker printed, prefixed `[fetch]`, plus which desktop folders were tried and why each failed |
| Worker log | `%LOCALAPPDATA%\SatpudaCore\install.log` | The same, kept even if the installer window is gone |
| Update-check log | `%LOCALAPPDATA%\SatpudaCore\update-check.log` | Version compared, and which of the three failure branches was hit |

The desktop-icon lines are the useful ones for failure 1 — they name each
candidate folder and say whether it was missing, unwritable, or accepted the
shortcut but left no file on disk (which means antivirus removed it).

## Known limitations

- **The Administrator unlock stops casual use, not a determined attacker.** An
  Inno Setup installer can be unpacked and its script read, which yields the
  salt, the round count and the digest; 20,000 rounds only make guessing slow.
  What is behind it is a setup choice, not money or books. Full reasoning under
  *The Administrator unlock*.
- **The installer is not code-signed.** Windows SmartScreen will show
  "Windows protected your PC" the first time each shop runs it; they must click
  *More info → Run anyway*. Fixing this properly needs a code-signing
  certificate. Worth doing if you keep distributing this.
- The update check runs **at logon and on demand**, not inside the app process.
  The app's shortcut points straight at `SatpudaCore_Desktop.exe`, so nothing —
  not antivirus, not a script policy — can sit between the shopkeeper and his
  program opening. The trade-off is that the check happens at logon (once every
  20 hours) and from the Start Menu item "Check for Satpuda Core updates",
  rather than at the moment the app starts.
- `LongPathsEnabled` is `0` on the build PC and probably on the shop PCs too.
  The extractor does not rely on it — it prefixes every path with `\\?\` — but
  the app itself may still hit `MAX_PATH` at runtime if it uses deep paths.
