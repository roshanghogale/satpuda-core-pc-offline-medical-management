; ============================================================================
;  Satpuda Core - Windows 10 / 11 installer
;
;  The app itself is NOT bundled in this file. The published zip is ~122 MB,
;  so the installer downloads it from the GitHub release, checks it, unpacks it
;  and puts it in Programs. That keeps this .exe around 2 MB and means a new
;  build needs a new zip on the release, not a new installer.
;
;  Requires Inno Setup 6.3 or newer (for ArchitecturesAllowed=x64compatible).
;  Built and tested against Inno Setup 6.7.3.
;  Compile:  ISCC.exe SatpudaCore.iss
;
;  To publish a new version, change MyAppVersion below and nothing else.
;  See README.md next to this file.
;
;  1.0.2 is the VPS-move build. Two things travel in it:
;    * the payload, whose core\server_api.py now calls srv1970994 directly and
;      keeps the Cloudflare tunnel as its fallback;
;    * this installer, which corrects the OLD server address where an existing
;      shop has it saved in AppData - see MigrateApiBaseFile in the script.
;  The version number is bumped for a reason and not only for tidiness: the
;  maintenance page decides there is something to apply by comparing
;  MyReleaseTag with the tag on the disk (UpdateIsAvailable), so at 1.0.1 a
;  shop already on 1.0.1 would be told "this is the newest version" and would
;  have to reach for Repair to get the correction. Repair still does the whole
;  job at the same version - that path is deliberately kept working - but the
;  bump is what makes the wizard OFFER it.
;
;  BEFORE PUBLISHING THIS: release v1.0.2 in roshanghogale/exes-for-satpuda-core
;  must exist and must carry SatpudaCore_Desktop_Win10.zip. The download URL is
;  built from the tag, so a bumped version with no matching release turns every
;  install into a 404. See "Publishing a new version" in README.md.
; ============================================================================

#define MyAppName        "Satpuda Core"
#define MyAppVersion     "1.0.6"
#define MyAppPublisher   "Satpuda"
#define MyAppExeName     "SatpudaCore_Desktop.exe"
#define MyEngineExeName  "SatpudaEngine.exe"
#define MyRepo           "roshanghogale/exes-for-satpuda-core"
#define MyReleaseTag     "v" + MyAppVersion
#define MyAssetName      "SatpudaCore_Desktop_Win10.zip"
#define MyDirectUrl      "https://github.com/" + MyRepo + "/releases/download/" + MyReleaseTag + "/" + MyAssetName
#define MyReleasePage    "https://github.com/" + MyRepo + "/releases"

[Setup]
AppId={{0A235245-EF46-48A8-9A21-9BAEF71183F2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyReleasePage}
AppSupportURL={#MyReleasePage}
AppUpdatesURL={#MyReleasePage}

; --- Programs and Features entry -------------------------------------------
; The version goes in the DISPLAY NAME, not only in the DisplayVersion value.
; Inno writes DisplayName from UninstallDisplayName when it is set, replacing
; the "AppVerName" it would otherwise use - so the old value of plain
; "{#MyAppName}" was what stripped the version out of the Programs list.
; DisplayVersion is rewritten in CurStepChanged with the version of the payload
; that actually landed, which is the number that matters to a shop.
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\satpuda.ico
CreateUninstallRegKey=yes

; --- per-user install, deliberately ----------------------------------------
; The shop's data already lives per-user in %LOCALAPPDATA%\VeterinaryApp, so a
; per-machine install would share the program but not the data. Per-user also
; means NO UAC prompt: a shopkeeper on a standard Windows account can install
; this without an administrator password, and the desktop shortcut lands in a
; folder he is guaranteed to be allowed to write to. /ALLUSERS still works for
; a shared machine.
PrivilegesRequired=lowest
; "commandline" only, NOT "dialog". With "dialog" Inno shows a "Select Setup
; Install Mode" page to EVERY user before anything else, and a shopkeeper who
; picks the shielded "Install for all users" gets a UAC prompt he has no
; administrator password for - a dead end on the first screen. /ALLUSERS still
; works from the command line for a genuinely shared machine.
PrivilegesRequiredOverridesAllowed=commandline
DefaultDirName={code:GetDefaultDir}
DefaultGroupName={#MyAppName}

; --- Windows 10 and 11, 64-bit ---------------------------------------------
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Downloaded payload unpacks to roughly 900 MB.
ExtraDiskSpaceRequired=1000000000

OutputDir=Output
OutputBaseFilename=SatpudaCoreInstaller
SetupIconFile=satpuda.ico
Compression=lzma2/max
SolidCompression=yes

; --- how the wizard looks ---------------------------------------------------
; "modern" is Inno's own current style: a white page header with the product
; logo in it, no 164-pixel bitmap strip down the left of every page, and the
; system UI font rather than the Tahoma 8 of the classic style. It was already
; set; what was missing around it is the rest of the look.
;
; WizardSizePercent buys back the room the header costs. The shop-name page now
; carries a question, an explanation, a separator and an Administrator area, and
; the maintenance page carries four lines of version detail and three choices;
; at the default size both stack edge to edge with no air between them.
WizardStyle=modern
WizardSizePercent=120
; The header logo, in the three sizes Windows actually asks for: 100%, 150% and
; 200%. Setup picks the one nearest the screen's DPI, so a 125% or 150% shop PC
; gets a sharp logo instead of a 55-pixel bitmap stretched by the window
; manager. Generated from satpuda.ico by make_wizard_logo.py - the icon is a
; solid black tile, so the script rounds its corners and lays it on white, which
; is what the modern header behind it is.
WizardSmallImageFile=wizard_logo.bmp,wizard_logo_150.bmp,wizard_logo_200.bmp
DisableProgramGroupPage=yes
ShowLanguageDialog=no
SetupLogging=yes
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; NO [LangOptions] font block here, and that is a decision rather than an
; omission. Default.isl leaves DialogFontName empty, which means "the system's
; own UI font" - Segoe UI 9 on Windows 10 and 11, which is the only place this
; installer runs (MinVersion=10.0). So the modern style is ALREADY on the right
; font, and writing "DialogFontName=Segoe UI / DialogFontSize=9" would change
; nothing on a normal machine while overriding the one machine where it matters:
; a shopkeeper who has turned Windows' "Make text bigger" up gets a larger
; system font, and pinning 9pt would take that away from him. The pages can
; afford to follow him because they no longer assume any font metric - every
; label measures itself with AdjustHeight. See LayoutLabel in [Code].

[Files]
; Worker that does the download/extract. dontcopy means it is never installed;
; ExtractTemporaryFile puts it in {tmp} during install, before the app folder
; exists. DestDir is deliberately omitted - it is not allowed with dontcopy.
Source: "satpuda_fetch.ps1";  Flags: dontcopy
; Installed for later use by the update check.
Source: "satpuda_update.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "satpuda.ico";        DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon";  Description: "Create a &desktop shortcut";                          GroupDescription: "Shortcuts:"
Name: "updatecheck";  Description: "Check for new versions when this computer starts";    GroupDescription: "Updates:"

[Run]
Filename: "{app}\app\{#MyAppExeName}"; Description: "Start {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files;          Name: "{app}\satpuda_update_silent.vbs"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\app.staging"
Type: dirifempty;     Name: "{app}"

[Code]
const
  SW_HIDE_ = 0;

  { The server's own rules for a shop name -- cleanStoreName() in
    src/services/provisionService.js. Repeated here so a name it would refuse is
    refused at the question, not after a 122 MB download. }
  STORE_NAME_MIN = 2;
  STORE_NAME_MAX = 60;

  { Same GUID as AppId above, without Inno's doubled opening brace. Inno builds
    the uninstall key name as "<AppId>_is1", and CurStepChanged writes the real
    payload version into it. If AppId is ever changed, change this too. }
  APP_UNINST_KEY = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{0A235245-EF46-48A8-9A21-9BAEF71183F2}_is1';

  { EnableMenuItem flags. MF_BYCOMMAND and MF_ENABLED are both 0; they are
    spelled out so the call below reads as the Win32 documentation does. }
  MF_BYCOMMAND_ = $0;
  MF_ENABLED_   = $0;
  SC_CLOSE_     = $F060;

  { ---- The Administrator unlock ----------------------------------------
    The owner's credentials are NOT in this file. What is in it is a salted,
    stretched SHA-256 of the user name and the password together, and the only
    thing the installer can do with it is say yes or no to something typed.

    HOW IT IS BUILT, so it can be re-derived or the password rotated:

        import hashlib
        SALT   = "<ADMIN_SALT below>"
        ROUNDS = <ADMIN_ROUNDS below>
        h = hashlib.sha256(
                (SALT + "|" + user.lower() + "|" + password).encode("ascii")
            ).hexdigest()
        for _ in range(ROUNDS):
            h = hashlib.sha256((SALT + h).encode("ascii")).hexdigest()
        h  # <- this final h is ADMIN_HASH

    ASCII, because Inno's own declaration is
    "function GetSHA256OfString(const S: AnsiString): String" - the AnsiString
    overload, one byte per character. (GetSHA256OfUnicodeString is the UTF-16
    one; it is deliberately NOT what AdminDigest calls.) Every round is put
    through LowerCase before it is fed back, so whichever case Inno returns its
    hex in cannot change the chain.

    The user name is hashed IN rather than compared separately: a plaintext
    "satpudacore" sitting next to a hash would hand over half the answer for
    nothing.

    WHAT THIS IS WORTH. An installer can be unpacked and its script read; the
    salt, the round count and this digest all come out. 20,000 rounds turn a
    dictionary attack from instant into slow, and that is the whole of it. This
    stops the shopkeeper, the shopkeeper's nephew and anyone poking at the
    wizard. It does not stop somebody who wants in. The thing it protects is a
    setup choice, not a shop's money, and it is said plainly in README.md. }
  ADMIN_SALT   = 'SatpudaCore-2026-53774ce85388d6aa80444bbe';
  ADMIN_ROUNDS = 20000;
  ADMIN_HASH   = 'ee34ab1ab48f7fe463a09e9d4d9e509684a718cb70d352df92fccb0fcc233b2d';

  { ---- The saved server address ----------------------------------------
    All four names come from core\server_api.py and are repeated here rather
    than guessed at:

      _PREFS           = "server_api_prefs.json"     -> API_PREFS_FILE
      prefs_path()     = _appdata_dir() + _PREFS,
                         and _appdata_dir() in core\license_manager.py is
                         %LOCALAPPDATA%\VeterinaryApp for a frozen build
                                                     -> AppDataDir + the above
      the key          = "api_base"                  -> API_BASE_KEY
      _RETIRED_HOSTS   holds "srv1892850.hstgr.cloud"  -> RETIRED_API_HOST
      _NEW_HOST        = "srv1970994.hstgr.cloud"    -> NEW_API_HOST

    RETIRED_API_HOST is written in lower case because the host read out of the
    file is lower-cased before it is compared - a shop that typed SRV1892850 in
    capitals has the same dead box. }
  API_PREFS_FILE   = 'server_api_prefs.json';
  API_BASE_KEY     = '"api_base"';
  RETIRED_API_HOST = 'srv1892850.hstgr.cloud';
  NEW_API_HOST     = 'srv1970994.hstgr.cloud';

  { What MigrateApiBaseFile answers. NOTHING covers every "correctly left
    alone" case as well as every "there was nothing here" case, because the
    installer treats them identically: it says so in the log and carries on. }
  API_MIG_NOTHING = 0;
  API_MIG_DONE    = 1;
  API_MIG_FAILED  = 2;

var
  GProgressPage   : TOutputProgressWizardPage;
  GStatusText     : String;    { current step caption, mirrored so we never read it back }
  GWorkerOK       : Boolean;   { worker printed OK| }
  GErrCode        : String;    { machine-readable failure code from the worker }
  GErrMsg         : String;    { worker's own wording, used if the code is unknown }
  GWarnings       : String;
  GCancelRequested: Boolean;
  GCancelFile     : String;
  GWorkerRunning  : Boolean;
  GWorkerPid      : Integer;   { from the worker's PID| line; 0 until it arrives }
  GNoCancel       : Boolean;   { worker is doing the folder swap - too late to stop }
  GStopButton     : TNewButton;
  GPayloadTag     : String;    { V| line: the release tag that actually landed }

  GDesktopLnk     : String;    { where the desktop shortcut actually landed, '' if none }
  GStartMenuLnk   : String;
  GDesktopProblem : String;    { why the desktop shortcut could not be made }

  { The saved server address. Both are '' unless a correction was needed AND
    could not be written; the Finished page says so, the same way it does for a
    shortcut that could not be created. }
  GApiBaseProblem : String;    { the sentence to show, '' when there is nothing to say }
  GApiBaseFailPath: String;    { the first file that could not be written }

  { The one question. Asked here, before the 122 MB download, so the app has
    nothing left to ask on its first launch. }
  GSetupPage      : TWizardPage;
  GNameEdit       : TNewEdit;
  GStoreName      : String;    { the cleaned answer, '' when nothing was asked }
  GSyncMode       : String;    { 'online' or 'offline' - 'online' unless an
                                 administrator has unlocked the page }
  GModeSummary    : TNewStaticText;  { the sentence a shopkeeper reads instead }

  { The Administrator area on that page. Everything below is hidden until the
    credentials are accepted, and the mode radios do not exist for anyone else. }
  GAdminButton    : TNewButton;
  GAdminHint      : TNewStaticText;
  GAdminUserLbl   : TNewStaticText;
  GAdminUserEdit  : TNewEdit;
  GAdminPassLbl   : TNewStaticText;
  GAdminPassEdit  : TPasswordEdit;
  GAdminGo        : TNewButton;
  GAdminStatus    : TNewStaticText;
  GAdminOpen      : Boolean;   { the credential row is showing }
  GAdminUnlocked  : Boolean;   { the credentials were accepted }
  GAdminTries     : Integer;
  GModePrompt     : TNewStaticText;
  GOnlineRadio    : TNewRadioButton;
  GOnlineNote     : TNewStaticText;
  GOfflineRadio   : TNewRadioButton;
  GOfflineNote    : TNewStaticText;

  { What is already on this computer, and what to do about it. }
  GHaveInstall    : Boolean;
  GInstalledPath  : String;
  GInstalledTag   : String;    { payload tag on disk, '' when unknown }
  GInstalledWhen  : String;    { date from the payload marker, '' when unknown }
  GLatestTag      : String;    { newest tag on GitHub, '' when the check failed }
  GTargetTag      : String;    { release this run installs; '' until ResolveTargetTag }
  GMaintPage      : TWizardPage;
  GMaintUpdate    : TNewRadioButton;
  GMaintRepair    : TNewRadioButton;
  GMaintRemove    : TNewRadioButton;
  GMaintNote      : TNewStaticText;
  GMaintPrompt    : TNewStaticText;
  GMaintRepairNote: TNewStaticText;
  GMaintAction    : String;    { 'fresh' | 'update' | 'repair' | 'remove' }
  GCheckedOnline  : Boolean;
  GStopping       : Boolean;   { RequestStop is already running; do not re-enter }

{ Re-enabling the window's X. See the long note above RequestStop for why it is
  greyed in the first place. Every call is wrapped in try/except: a missing
  import must never be the reason an install fails. HWND and THandle are both
  NativeUInt in Pascal Script, so these stay correct whether Setup was built
  32-bit or 64-bit. }
function GetSystemMenu(hWnd: HWND; bRevert: Integer): THandle;
  external 'GetSystemMenu@user32.dll stdcall';
function EnableMenuItem(hMenu: THandle; uIDEnableItem, uEnable: Cardinal): Integer;
  external 'EnableMenuItem@user32.dll stdcall';

{ ======================================================================== }
{  Small helpers                                                            }
{ ======================================================================== }

function PSExePath: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  if not FileExists(Result) then
    Result := ExpandConstant('{win}\System32\WindowsPowerShell\v1.0\powershell.exe');
end;

function Q(const S: String): String;
begin
  { Quote a path for a PowerShell -File argument. A trailing backslash would
    escape the closing quote, so it is stripped first. }
  Result := '"' + RemoveBackslashUnlessRoot(S) + '"';
end;

function CacheDir: String;
begin
  Result := ExpandConstant('{localappdata}\SatpudaCore\cache');
end;

{ How many connections the download uses. 16 is what the measurements chose;
  /Segments=4 exists for a shop whose router cannot hold many at once.
  Sanitised here rather than trusted, because a value PowerShell cannot bind to
  an [int] parameter would stop the worker before it printed anything. }
function SegmentCount: Integer;
begin
  Result := StrToIntDef(Trim(ExpandConstant('{param:Segments|16}')), 16);
  if Result < 1  then Result := 1;
  if Result > 32 then Result := 32;
end;

{ ======================================================================== }
{  Which version is on this computer                                        }
{                                                                           }
{  Three sources, most trustworthy first:                                   }
{                                                                           }
{    1. <app>\app\.satpuda-install.json - written by the worker as the LAST  }
{       thing inside the staging folder, so it can only exist if the         }
{       extraction ran all the way through. This is the version of the       }
{       program that is actually sitting on the disk.                        }
{    2. HKCU\Software\Satpuda\SatpudaCore\Tag - written when a wizard run    }
{       finished. Missing whenever a run was stopped after the payload       }
{       landed but before the wizard ended, which is exactly the state the   }
{       owner's PC was in.                                                   }
{    3. The .exe's own file version, as a last resort.                       }
{                                                                           }
{  The installer's own AppVersion is deliberately NOT one of the sources:    }
{  it says which installer ran, not which program is installed.              }
{ ======================================================================== }

{ Pull "tag": "v1.0.1" out of the marker without a JSON parser. The file is
  written by ConvertTo-Json, one value per line, so this is enough. }
function TagFromMarker(const MarkerPath: String): String;
var
  Raw: AnsiString;
  S: String;
  P, Q1, Q2: Integer;
begin
  Result := '';
  if not FileExists(MarkerPath) then Exit;
  if not LoadStringFromFile(MarkerPath, Raw) then Exit;
  S := Raw;
  P := Pos('"tag"', S);
  if P = 0 then Exit;
  S := Copy(S, P + 5, Length(S));
  P := Pos(':', S);
  if P = 0 then Exit;
  S := Copy(S, P + 1, Length(S));
  Q1 := Pos('"', S);
  if Q1 = 0 then Exit;
  S := Copy(S, Q1 + 1, Length(S));
  Q2 := Pos('"', S);
  if Q2 = 0 then Exit;
  Result := Trim(Copy(S, 1, Q2 - 1));
end;

function DateFromMarker(const MarkerPath: String): String;
var
  Raw: AnsiString;
  S: String;
  P, Q1, Q2: Integer;
begin
  Result := '';
  if not FileExists(MarkerPath) then Exit;
  if not LoadStringFromFile(MarkerPath, Raw) then Exit;
  S := Raw;
  P := Pos('"installedAt"', S);
  if P = 0 then Exit;
  S := Copy(S, P + 13, Length(S));
  P := Pos(':', S);
  if P = 0 then Exit;
  S := Copy(S, P + 1, Length(S));
  Q1 := Pos('"', S);
  if Q1 = 0 then Exit;
  S := Copy(S, Q1 + 1, Length(S));
  Q2 := Pos('"', S);
  if Q2 = 0 then Exit;
  Result := Copy(Trim(Copy(S, 1, Q2 - 1)), 1, 10);   { just the date part }
end;

{ 'v1.0.10' vs '1.0.9' -> compares numerically, per component, tolerant of a
  leading v, of different lengths, and of a '-beta' suffix. 1 if A>B, -1 if
  A<B, 0 if equal or if either side is unusable. }
function VersionPart(const S: String; Index: Integer): Integer;
var
  Cur, Part: String;
  I, N, Seen: Integer;
  C: Char;
begin
  Result := 0;
  Cur  := Trim(S);
  if (Cur <> '') and ((Cur[1] = 'v') or (Cur[1] = 'V')) then
    Cur := Copy(Cur, 2, Length(Cur));
  { cut anything from the first '-', '+' or space }
  for I := 1 to Length(Cur) do
  begin
    C := Cur[I];
    if (C = '-') or (C = '+') or (C = ' ') then
    begin
      Cur := Copy(Cur, 1, I - 1);
      Break;
    end;
  end;
  Seen := 0;
  Part := '';
  for I := 1 to Length(Cur) + 1 do
  begin
    if (I > Length(Cur)) or (Cur[I] = '.') then
    begin
      if Seen = Index then
      begin
        { keep only the leading digits, so '2rc' reads as 2 }
        N := 0;
        while (N < Length(Part)) and (Part[N + 1] >= '0') and (Part[N + 1] <= '9') do
          N := N + 1;
        Result := StrToIntDef(Copy(Part, 1, N), 0);
        Exit;
      end;
      Seen := Seen + 1;
      Part := '';
    end
    else
      Part := Part + Cur[I];
  end;
end;

function CompareTags(const A, B: String): Integer;
var
  I, X, Y: Integer;
begin
  Result := 0;
  if (Trim(A) = '') or (Trim(B) = '') then Exit;
  for I := 0 to 3 do
  begin
    X := VersionPart(A, I);
    Y := VersionPart(B, I);
    if X > Y then begin Result :=  1; Exit; end;
    if X < Y then begin Result := -1; Exit; end;
  end;
end;

{ A tag as a shop reads it: v1.0.6 -> 1.0.6. }
function TagNumber(const Tag: String): String;
begin
  Result := Trim(Tag);
  if (Result <> '') and ((Result[1] = 'v') or (Result[1] = 'V')) then
    Result := Copy(Result, 2, Length(Result));
end;

{ The release this run installs: the newest one on GitHub once ResolveTargetTag
  has found it, otherwise the version this installer was built as. So one
  installer keeps working for every later release without being rebuilt. }
function TargetTag: String;
begin
  if GTargetTag <> '' then
    Result := GTargetTag
  else
    Result := '{#MyReleaseTag}';
end;

function TargetDirectUrl: String;
begin
  Result := 'https://github.com/{#MyRepo}/releases/download/' + TargetTag + '/{#MyAssetName}';
end;

{ Only a plain "v1.2.3" tag from GitHub is trusted: it goes into a download URL
  and onto a command line. }
function IsPlainTag(const S: String): Boolean;
var
  I: Integer;
begin
  Result := (Length(S) >= 2) and (Length(S) <= 20) and (S[1] = 'v');
  if not Result then Exit;
  for I := 2 to Length(S) do
    if not (((S[I] >= '0') and (S[I] <= '9')) or (S[I] = '.')) then
    begin
      Result := False;
      Exit;
    end;
end;

{ Where an earlier install put itself. }
function FindInstalledPath: String;
var
  S: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'InstallPath', S) and (S <> '') then
    if FileExists(AddBackslash(S) + 'app\{#MyAppExeName}') then
    begin
      Result := S;
      Exit;
    end;

  { Inno's own record of where it installed last time. Present for HKCU and,
    for an /ALLUSERS install, HKLM. }
  if RegQueryStringValue(HKCU, APP_UNINST_KEY, 'Inno Setup: App Path', S) and (S <> '') then
    if FileExists(AddBackslash(S) + 'app\{#MyAppExeName}') then
    begin
      Result := S;
      Exit;
    end;
  if RegQueryStringValue(HKLM, APP_UNINST_KEY, 'Inno Setup: App Path', S) and (S <> '') then
    if FileExists(AddBackslash(S) + 'app\{#MyAppExeName}') then
    begin
      Result := S;
      Exit;
    end;

  { Nothing in the registry - which is exactly what a stopped run leaves behind.
    Look where the files would be. }
  S := ExpandConstant('{autopf}\{#MyAppName}');
  if FileExists(AddBackslash(S) + 'app\{#MyAppExeName}') then
  begin
    Result := S;
    Exit;
  end;
  S := ExpandConstant('{localappdata}\Programs\{#MyAppName}');
  if FileExists(AddBackslash(S) + 'app\{#MyAppExeName}') then
    Result := S;
end;

procedure DetectExistingInstall;
var
  S, Exe: String;
begin
  GInstalledPath := FindInstalledPath;
  GHaveInstall   := (GInstalledPath <> '');
  GInstalledTag  := '';
  GInstalledWhen := '';
  if not GHaveInstall then Exit;

  GInstalledTag  := TagFromMarker(AddBackslash(GInstalledPath) + 'app\.satpuda-install.json');
  GInstalledWhen := DateFromMarker(AddBackslash(GInstalledPath) + 'app\.satpuda-install.json');

  if GInstalledTag = '' then
    if RegQueryStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'Tag', S) then
      GInstalledTag := S;
  if GInstalledTag = '' then
    if RegQueryStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'Version', S) then
      GInstalledTag := S;
  if GInstalledTag = '' then
  begin
    Exe := AddBackslash(GInstalledPath) + 'app\{#MyAppExeName}';
    if GetVersionNumbersString(Exe, S) then
      GInstalledTag := S;
  end;
  Log('detected install at ' + GInstalledPath + ', version "' + GInstalledTag + '"');
end;

{ Shown wherever a version belongs. Never blank: if we genuinely cannot tell,
  say so in words rather than leaving an empty line. }
function InstalledVersionText: String;
begin
  if not GHaveInstall then
    Result := 'Not installed on this computer yet'
  else if GInstalledTag = '' then
    Result := 'Installed, but the version could not be read'
  else
    Result := GInstalledTag;
end;

{ DefaultDirName. An existing install is reinstalled where it already lives,
  even when the registry entry that would normally tell Inno that is missing. }
function GetDefaultDir(Param: String): String;
begin
  if GHaveInstall and (GInstalledPath <> '') then
    Result := GInstalledPath
  else
    Result := ExpandConstant('{autopf}\{#MyAppName}');
end;

{ ======================================================================== }
{  Laying out a custom page so 125% and 150% do not clip it                 }
{                                                                           }
{  Shop PCs run scaled. The old pages set Height := ScaleY(28) on a wrapped }
{  label and hoped: ScaleY multiplies by the DPI, but the number of LINES   }
{  the text takes is decided by the font and the width, and the moment a    }
{  caption is edited - or Windows picks a slightly wider font - a two-line  }
{  box has three lines of text in it and the third one is simply not there. }
{                                                                           }
{  TNewStaticText.AdjustHeight measures the caption that was actually set,  }
{  at the size it is actually being drawn, and resizes the control to fit.  }
{  So every label here is created, given its caption, measured, and only    }
{  then does the next control's Y get decided. Nothing is guessed.          }
{ ======================================================================== }

{ Create a wrapped label on a page, measure it, and return the Y for whatever
  comes next. Indent is in unscaled units and is scaled here. }
function LayoutLabel(Page: TWizardPage; var Ctl: TNewStaticText;
                     const S: String; Y, Indent, GapAfter: Integer;
                     Bold: Boolean): Integer;
begin
  Ctl := TNewStaticText.Create(Page);
  Ctl.Parent   := Page.Surface;
  Ctl.Left     := ScaleX(Indent);
  Ctl.Top      := Y;
  Ctl.Width    := Page.SurfaceWidth - ScaleX(Indent);
  Ctl.AutoSize := False;
  Ctl.WordWrap := True;
  if Bold then
    Ctl.Font.Style := [fsBold];
  Ctl.Caption  := S;
  { The measurement. Font.Style is set BEFORE the caption and the caption
    before this call, because AdjustHeight measures what is there now. }
  Ctl.AdjustHeight;
  Result := Y + Ctl.Height + ScaleY(GapAfter);
end;

{ The same, for a label whose text is filled in later (the unlock status line).
  Height is left at one line; the caller re-measures when it sets a caption. }
function LayoutBlankLabel(Page: TWizardPage; var Ctl: TNewStaticText;
                          Y, Indent, GapAfter: Integer): Integer;
begin
  Result := LayoutLabel(Page, Ctl, ' ', Y, Indent, GapAfter, False);
end;

{ ======================================================================== }
{  The Administrator unlock                                                 }
{                                                                           }
{  See ADMIN_HASH at the top for how the constant was derived and for an    }
{  honest statement of what this is worth.                                  }
{ ======================================================================== }

{ Salted, stretched SHA-256 of the user name and password together, as lower
  case hex. Pure arithmetic: it touches no file, no registry key and no
  network, and it is the only thing in this installer that looks at either
  value. }
function AdminDigest(const User, Pass: String): String;
var
  I: Integer;
  H: String;
begin
  H := LowerCase(GetSHA256OfString(
         ADMIN_SALT + '|' + LowerCase(Trim(User)) + '|' + Pass));
  for I := 1 to ADMIN_ROUNDS do
    H := LowerCase(GetSHA256OfString(ADMIN_SALT + H));
  Result := H;
end;

{ True only for the owner's user name and password. The password is compared
  EXACTLY - spaces included, because the real one has one in the middle - and
  only the user name is folded to lower case and trimmed. }
function AdminCredentialsOK(const User, Pass: String): Boolean;
begin
  Result := False;
  if (Trim(User) = '') or (Pass = '') then
    Exit;
  try
    Result := AdminDigest(User, Pass) = ADMIN_HASH;
  except
    { A hashing function that is not there is a build problem, not a reason to
      let anybody through. Fails CLOSED. }
    Log('administrator unlock: could not hash the credentials: ' +
        GetExceptionMessage);
    Result := False;
  end;
end;

{ ======================================================================== }
{  The one question                                                         }
{                                                                           }
{  Activation used to ask a shop two things: what it is called, and whether }
{  its data lives on the server or on this computer. The installer now asks }
{  ONE - the name - and pins the mode to ONLINE.                            }
{                                                                           }
{  WHY THE MODE QUESTION IS GONE. An Offline shop's expiry date lives on    }
{  the shop's own computer, and a file on a shop's own computer is a file   }
{  the shop can change. Online, the date is the server's and nothing on the }
{  till decides it. That is the owner's call and it is not a preference the }
{  person installing gets to have; the Administrator area below is how the  }
{  owner sets up an Offline shop himself.                                   }
{                                                                           }
{  AND IT IS WHY THERE IS NO STORE LIST. In Online mode the shop name goes  }
{  to /api/provision/trial, which only ever INSERTs (server                 }
{  src/services/provisionService.js) - it never resolves a name to a store  }
{  that already exists. So the name typed here cannot reach another shop's  }
{  books, and the installer has nothing to offer a list OF. Offline is the  }
{  mode that leaves a PC with a store the server has never pinned, and a    }
{  store with no pin is the one that later has to be matched to the server  }
{  by its display NAME. See ForbidStoreChoice below.                        }
{                                                                           }
{  THE HANDOFF IS A FILE:                                                   }
{                                                                           }
{      %LOCALAPPDATA%\VeterinaryApp\provision.json                          }
{      store_name = Roshan Medical,  sync_mode = online                     }
{                                                                           }
{  That folder is the app's own data directory (core/license_manager.py,    }
{  _appdata_dir) - the same place it keeps the store registry, the licence   }
{  and the databases. The app reads the file once, activates, and renames it }
{  to provision.done, so a second launch cannot start a second trial.        }
{                                                                           }
{  Not a registry value: retiring one written by an elevated installer is    }
{  exactly what a standard user may not be allowed to do, and a handoff that }
{  cannot be consumed asks for a new trial on every launch. Not a command-   }
{  line flag either: the app is started from the desktop shortcut, the Start }
{  Menu, the .exe itself and after every update, so a flag would be missing  }
{  on most launches and repeated on the rest. Nothing in the file is secret. }
{ ======================================================================== }

function AppDataDir: String;
begin
  Result := ExpandConstant('{localappdata}\VeterinaryApp');
end;

{ Whitespace collapsed, control characters out, ends trimmed - the same
  reduction the server performs before it stores the name. }
function CleanStoreName(const S: String): String;
var
  I: Integer;
  C: Char;
  Buf: String;
begin
  Buf := '';
  for I := 1 to Length(S) do
  begin
    C := S[I];
    if Ord(C) < 32 then
      C := ' ';
    if Ord(C) = 127 then
      C := ' ';
    if C = ' ' then
    begin
      if (Length(Buf) > 0) and (Buf[Length(Buf)] <> ' ') then
        Buf := Buf + ' ';
    end
    else
      Buf := Buf + C;
  end;
  Result := Trim(Buf);
end;

{ The server insists on at least one letter or digit, in any script. Pascal
  Script has no Unicode classes, so anything above ASCII counts as a letter -
  which is what makes a Devanagari or Gujarati shop name pass here. The only
  thing that slips through is a name made ENTIRELY of non-ASCII punctuation,
  and the server still refuses that; the app shows its sentence with the name
  already filled in. }
function HasLetterOrDigit(const S: String): Boolean;
var
  I, N: Integer;
begin
  Result := False;
  for I := 1 to Length(S) do
  begin
    N := Ord(S[I]);
    if ((N >= 48) and (N <= 57)) or
       ((N >= 65) and (N <= 90)) or
       ((N >= 97) and (N <= 122)) or
       (N > 127) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

{ '' when the name is usable, otherwise what to tell the shopkeeper. }
function StoreNameProblem(const S: String): String;
var
  Name: String;
begin
  Result := '';
  Name := CleanStoreName(S);
  if Length(Name) < STORE_NAME_MIN then
    Result := 'Please type the name of your shop.' + #13#10#13#10 +
              'It needs at least ' + IntToStr(STORE_NAME_MIN) + ' letters.'
  else if Length(Name) > STORE_NAME_MAX then
    Result := 'That shop name is too long.' + #13#10#13#10 +
              'Please shorten it to ' + IntToStr(STORE_NAME_MAX) + ' characters or fewer. ' +
              'It is only the label on your bills and your backup folder - it does not ' +
              'have to be the full legal name.'
  else if not HasLetterOrDigit(Name) then
    Result := 'The shop name needs at least one letter or number in it.';
end;

{ A Pascal string into a JSON string body. Backslash and quote are the two
  characters that would otherwise end the value early and leave the app with a
  file it cannot read. }
function JsonEscape(const S: String): String;
var
  I: Integer;
  C: Char;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := S[I];
    if C = '\' then
      Result := Result + '\\'
    else if C = '"' then
      Result := Result + '\"'
    else if Ord(C) < 32 then
      Result := Result + ' '
    else
      Result := Result + C;
  end;
end;

{ True when Satpuda Core has already been set up for THIS Windows user, so the
  questions are not asked again on a re-install or an upgrade. Any one of these
  means the app has a shop here and would ignore a handoff file anyway. }
function AlreadySetUpHere: Boolean;
var
  Base: String;
begin
  Base := AddBackslash(AppDataDir);
  Result := DirExists(Base + 'stores') or
            FileExists(Base + 'stores_registry.dat') or
            FileExists(Base + 'activation.dat') or
            FileExists(Base + 'provision.done');
end;

{ ======================================================================== }
{  The saved server address                                                 }
{                                                                           }
{  The VPS moved. srv1892850.hstgr.cloud is the old box: today it is a Caddy }
{  bridge forwarding to srv1970994.hstgr.cloud, and it stops answering the   }
{  day that VPS lapses. A shop that has been running for a while has that    }
{  dead name written into                                                   }
{                                                                           }
{      %LOCALAPPDATA%\VeterinaryApp\server_api_prefs.json     "api_base"     }
{                                                                           }
{  and the SAVED value beats the program's own default - load_prefs() and    }
{  configured_base() in core\server_api.py. So shipping a new build alone     }
{  changes nothing for that shop: the new .exe reads the old file and keeps  }
{  calling the old box. This is where the file is corrected, so that an      }
{  install, an update or a repair puts it right without anybody opening a    }
{  text editor in a shop.                                                    }
{                                                                           }
{  THE RULE, and it is deliberately narrow - the same rule migrate_api_base()}
{  applies inside the program:                                               }
{                                                                           }
{      Rewrite the HOST, only the host, and only when that host is exactly   }
{      srv1892850.hstgr.cloud. Scheme, port, path and query are carried      }
{      across untouched, and every other key in the file is left byte for    }
{      byte as it was.                                                       }
{                                                                           }
{  Left alone, every time:                                                   }
{                                                                           }
{    * https://api.satpudacore.online - the Cloudflare tunnel. It is slower  }
{      (measured from a shop in Maharashtra: ~460ms a call against ~85ms     }
{      direct) but it reaches the same server, and it is the escape hatch a  }
{      shop uses when the direct host will not answer. Rewriting it would    }
{      take that escape hatch away.                                          }
{    * a LAN address, localhost, a custom port, a path prefix, or any other  }
{      hostname - somebody typed that on purpose.                            }
{    * srv1970994 itself, and anything that merely CONTAINS the old name:    }
{      staging.srv1892850.hstgr.cloud, srv1892850-backup.hstgr.cloud. The    }
{      test is on the whole host, never a substring.                         }
{                                                                           }
{  A dead name is corrected; a live choice is never overruled.               }
{                                                                           }
{  And nothing is ever CREATED. A computer with no such file has never run   }
{  the program, and the build being installed already defaults to the new    }
{  host - writing a settings file there would be inventing a setting nobody  }
{  chose.                                                                    }
{ ======================================================================== }

function ApiPrefsPathIn(const Dir: String): String;
begin
  Result := AddBackslash(Dir) + API_PREFS_FILE;
end;

{ The four characters JSON allows between tokens. }
function IsJsonSpace(C: Char): Boolean;
begin
  Result := (C = ' ') or (C = #9) or (C = #13) or (C = #10);
end;

{ Locate the "api_base" MEMBER and hand back where its text value lives:
  ValStart is the first character INSIDE the quotes and ValEnd is the closing
  quote itself, so Copy(Text, ValStart, ValEnd - ValStart) is the value.

  False, with Why filled in, when there is no such member, when its value is
  not a plain string, or when the value carries a backslash. The backslash is
  a refusal rather than a decoder: nothing that writes this file produces one
  (Python's json.dump does not escape a forward slash, and a URL has nothing
  else in it to escape), so a backslash means the file is not the shape this
  code understands - and a file it does not understand is a file it does not
  touch. }
function FindApiBaseValue(const Text: String; var ValStart, ValEnd: Integer;
                          var Why: String): Boolean;
var
  I, J, N, P: Integer;
begin
  Result   := False;
  ValStart := 0;
  ValEnd   := 0;
  N := Length(Text);

  { The closing quote is part of what is searched for, so this can never match
    "api_base_migrated_from" or "api_base_migrated_on" - the character after
    api_base is an underscore in both of those, not a quote. And a colon has
    to follow, so an "api_base" that happened to be somebody's stored VALUE
    rather than a key is skipped instead of rewritten. }
  P := 0;
  for I := 1 to N - Length(API_BASE_KEY) + 1 do
    if Copy(Text, I, Length(API_BASE_KEY)) = API_BASE_KEY then
    begin
      J := I + Length(API_BASE_KEY);
      while (J <= N) and IsJsonSpace(Text[J]) do
        J := J + 1;
      if (J <= N) and (Text[J] = ':') then
      begin
        P := J + 1;
        Break;
      end;
    end;

  if P = 0 then
  begin
    Why := 'the file does not save an "api_base"';
    Exit;
  end;

  while (P <= N) and IsJsonSpace(Text[P]) do
    P := P + 1;
  if (P > N) or (Text[P] <> '"') then
  begin
    { null, a number, an object - not something to swap a hostname inside. }
    Why := '"api_base" is not a plain text value';
    Exit;
  end;

  ValStart := P + 1;
  I := ValStart;
  while (I <= N) and (Text[I] <> '"') do
  begin
    if Text[I] = '\' then
    begin
      Why := 'the saved address contains a backslash escape';
      ValStart := 0;
      Exit;
    end;
    I := I + 1;
  end;
  if I > N then
  begin
    Why := 'the file stops in the middle of "api_base"';
    ValStart := 0;
    Exit;
  end;

  ValEnd := I;
  Result := True;
end;

{ The host inside a base URL, as a span of Val: everything after "://", after
  any user:pass@, and before the port, the path or the query. So

      https://srv1892850.hstgr.cloud:8443/api?x=1
              ^HStart              ^HEnd

  and the caller can replace exactly that and keep the rest.

  False when there is no host to speak of. An IPv6 literal - https://[::1]:8000
  - comes out of here as the single character '[', which is not the retired
  host, so it is left alone: wrong span, right outcome. }
function HostSpan(const Val: String; var HStart, HEnd: Integer): Boolean;
var
  I, N, AuthEnd: Integer;
  C: Char;
begin
  N := Length(Val);
  HStart := 1;
  I := Pos('://', Val);
  if I > 0 then
    HStart := I + 3;

  AuthEnd := N + 1;
  for I := HStart to N do
  begin
    C := Val[I];
    if (C = '/') or (C = '?') or (C = '#') then
    begin
      AuthEnd := I;
      Break;
    end;
  end;

  { user:pass@host - the LAST '@' in the authority starts the host. }
  for I := AuthEnd - 1 downto HStart do
    if Val[I] = '@' then
    begin
      HStart := I + 1;
      Break;
    end;

  { :port, if there is one }
  HEnd := AuthEnd;
  for I := HStart to AuthEnd - 1 do
    if Val[I] = ':' then
    begin
      HEnd := I;
      Break;
    end;

  Result := HEnd > HStart;
end;

{ Correct ONE settings file. Answers API_MIG_NOTHING / _DONE / _FAILED, and
  fills Info in with something worth putting in the log on every one of those.

  DryRun asks the question without touching anything - that is what the
  maintenance page uses to tell the shopkeeper there is something here worth
  correcting, before he has chosen anything.

  Nothing here can fail an install. Every road out is a return value. }
function MigrateApiBaseFile(const Path: String; DryRun: Boolean; var Info: String): Integer;
var
  Raw, Narrowed, NewRaw, Back: AnsiString;
  Text, NewText, Val, NewVal, Host, Why, Extra, Tmp, Old: String;
  ValStart, ValEnd, HStart, HEnd: Integer;
begin
  Result := API_MIG_NOTHING;
  Info   := '';

  if not FileExists(Path) then
  begin
    Info := 'no settings file here, so nothing to correct: ' + Path;
    Exit;
  end;

  if not LoadStringFromFile(Path, Raw) then
  begin
    Info := 'the settings file could not be read: ' + Path;
    Exit;
  end;

  { LoadStringFromFile hands back BYTES; the wizard works in the Unicode string
    type. Widen the way the rest of this script does - then narrow the copy
    straight back and insist it is byte for byte what came off the disk.

    That one comparison is what makes the rest of this safe. If it holds, this
    machine's code page carries the file's contents losslessly in both
    directions, so the string that is written at the end is the same bytes as
    the string that was read except where it was deliberately changed. If it
    does not hold, every index below would be measured against something that
    is not the file, and a shop's saved user name and password are in it - so
    the file is left exactly as it is and the program corrects itself on its
    next launch instead. Fail closed: the worst this branch can do is nothing. }
  Text     := Raw;
  Narrowed := Text;
  if Narrowed <> Raw then
  begin
    Info := 'the settings file holds characters this installer will not risk rewriting: ' + Path;
    Exit;
  end;

  if not FindApiBaseValue(Text, ValStart, ValEnd, Why) then
  begin
    { No saved address at all is the good case, not a failure: the program then
      uses its own default, which is already the new host. }
    Info := Why + ' (' + Path + ')';
    Exit;
  end;

  Val := Copy(Text, ValStart, ValEnd - ValStart);

  if Pos('://', Val) = 0 then
  begin
    { A bare host with no scheme. migrate_api_base() in the program puts the
      https:// on as well as correcting the name, and it runs on the next
      launch; doing half of that here would leave a value neither side ever
      finishes. }
    Info := 'the saved address has no scheme and was left for the program: ' + Val;
    Exit;
  end;

  if not HostSpan(Val, HStart, HEnd) then
  begin
    Info := 'no host could be read out of the saved address: ' + Val;
    Exit;
  end;

  Host := LowerCase(Copy(Val, HStart, HEnd - HStart));
  if Host <> RETIRED_API_HOST then
  begin
    { The tunnel, a LAN box, a test port, the new host already - a live choice.
      This is the branch that runs on almost every computer, and it is the
      whole reason the test is on the exact host and not on a substring. }
    Info := 'left as it is, this is not the retired host: ' + Val;
    Exit;
  end;

  { The host, and only the host. Everything to the left of HStart (scheme, any
    user:pass@) and everything from HEnd on (:port, /path, ?query) is carried
    across exactly as it was found. }
  NewVal := Copy(Val, 1, HStart - 1) + NEW_API_HOST + Copy(Val, HEnd, Length(Val));

  if DryRun then
  begin
    Result := API_MIG_DONE;
    Info   := Val;
    Exit;
  end;

  { The same two keys the program writes when it does this itself
    (_persist_base_migration in core\server_api.py), so a file corrected by the
    installer and a file corrected by the app read the same to whoever opens
    them. Written only when they are not already there: a duplicate key is
    legal JSON and Python keeps the last one, but it is a puzzle for the next
    person, and an existing pair records a migration that really happened.

    Inserted immediately after the value's closing quote, which is valid
    wherever that member sits - a member followed by a comma and another member
    is legal whether what came next was another key or the closing brace. }
  Extra := '';
  if Pos('"api_base_migrated_from"', Text) = 0 then
    Extra := ', "api_base_migrated_from": "' + JsonEscape(Val) +
             '", "api_base_migrated_on": "' +
             GetDateTimeString('yyyy/mm/dd', '-', ':') + '"';

  NewText := Copy(Text, 1, ValStart - 1) + NewVal + '"' + Extra +
             Copy(Text, ValEnd + 1, Length(Text));
  NewRaw  := NewText;

  { Written beside the file and swapped in, never over the top of it. A
    SaveStringToFile that truncates and then fails halfway - a full disk, a
    disconnected roaming profile - would leave the shop with a settings file
    missing the user name and password it also holds. Everything below either
    ends with the new file in place or with the original untouched. }
  Tmp := Path + '.satpuda-new';
  Old := Path + '.satpuda-old';
  DeleteFile(Tmp);

  if not SaveStringToFile(Tmp, NewRaw, False) then
  begin
    DeleteFile(Tmp);
    Result := API_MIG_FAILED;
    Info   := 'the corrected file could not be written next to ' + Path;
    Exit;
  end;

  { Read it back before anything is moved. A short write that reported success
    is exactly the failure this whole dance exists to survive. }
  if (not LoadStringFromFile(Tmp, Back)) or (Back <> NewRaw) then
  begin
    DeleteFile(Tmp);
    Result := API_MIG_FAILED;
    Info   := 'the corrected file did not come back off the disk intact: ' + Path;
    Exit;
  end;

  DeleteFile(Old);
  if not RenameFile(Path, Old) then
  begin
    { Almost always the app still running with the file open, or a read-only
      profile. The original is still there and still correct. }
    DeleteFile(Tmp);
    Result := API_MIG_FAILED;
    Info   := 'the settings file is locked or read-only: ' + Path;
    Exit;
  end;

  if not RenameFile(Tmp, Path) then
  begin
    { Put the shop back exactly as it was before giving up. }
    RenameFile(Old, Path);
    DeleteFile(Tmp);
    Result := API_MIG_FAILED;
    Info   := 'the corrected file could not be moved into place: ' + Path;
    Exit;
  end;

  DeleteFile(Old);
  Result := API_MIG_DONE;
  Info   := Val + '  ->  ' + NewVal;
end;

{ The one that runs for the person installing. }
function MigrateApiBaseHere(DryRun: Boolean; var Info: String): Integer;
begin
  Result := MigrateApiBaseFile(ApiPrefsPathIn(AppDataDir), DryRun, Info);
end;

{ /ALLUSERS runs elevated, and %LOCALAPPDATA% is then the ADMINISTRATOR'S
  folder - so the call above would correct a file nobody opens the app with and
  leave the shopkeeper's alone. WriteProvisionFile has the same problem and
  answers it with a copy in ProgramData; there is no equivalent trick here,
  because the thing being corrected is a file that already exists inside one
  particular profile. So walk the profiles.

  This creates nothing and visits nobody: a profile that has never run Satpuda
  Core has no VeterinaryApp folder, so MigrateApiBaseFile returns NOTHING on
  the first line and moves on. }
procedure MigrateApiBaseAllProfiles(var Fixed, Failed: Integer);
var
  Users, Name, Info, Dir: String;
  Rec: TFindRec;
  Code: Integer;
begin
  { %LOCALAPPDATA% is <profiles>\<user>\AppData\Local, so three levels up is
    the profiles folder itself - read from Windows rather than assuming C:. }
  Users := ExtractFileDir(ExtractFileDir(ExtractFileDir(ExpandConstant('{localappdata}'))));
  if not DirExists(Users) then
  begin
    Log('profile sweep skipped: ' + Users + ' is not a folder');
    Exit;
  end;

  if not FindFirst(AddBackslash(Users) + '*', Rec) then
    Exit;
  try
    repeat
      if (Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        Name := Rec.Name;
        if (Name <> '.') and (Name <> '..') and
           (CompareText(Name, 'Public')       <> 0) and
           (CompareText(Name, 'Default')      <> 0) and
           (CompareText(Name, 'Default User') <> 0) and
           (CompareText(Name, 'All Users')    <> 0) then
        begin
          Dir  := AddBackslash(Users) + Name + '\AppData\Local\VeterinaryApp';
          Code := MigrateApiBaseFile(ApiPrefsPathIn(Dir), False, Info);
          if Code = API_MIG_DONE then
          begin
            Fixed := Fixed + 1;
            Log('server address corrected for ' + Name + ': ' + Info);
          end
          else if Code = API_MIG_FAILED then
          begin
            Failed := Failed + 1;
            if GApiBaseFailPath = '' then
              GApiBaseFailPath := ApiPrefsPathIn(Dir);
            Log('server address NOT corrected for ' + Name + ': ' + Info);
          end;
        end;
      end;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

{ ------------------------------------------------------------------------ }
{  The shop page: one question, and an area only the owner can open         }
{ ------------------------------------------------------------------------ }

{ Show or hide the Administrator half of the page. Three states, and the two
  blocks share the same strip of the surface because only one of them can ever
  be on screen:

    collapsed  - just the "Administrator" button. What every shop sees.
    open       - the user name and password row. Nothing else changes.
    unlocked   - the mode radios, in place of the credential row.

  Called from every handler rather than each of them poking at controls, so
  there is exactly one description of what the page looks like. }
procedure UpdateAdminArea;
var
  Creds, Mode: Boolean;
begin
  Creds := GAdminOpen and not GAdminUnlocked;
  Mode  := GAdminUnlocked;

  GAdminButton.Visible   := not GAdminUnlocked;
  GAdminButton.Enabled   := not GAdminOpen;
  GAdminHint.Visible     := not GAdminUnlocked;

  GAdminUserLbl.Visible  := Creds;
  GAdminUserEdit.Visible := Creds;
  GAdminPassLbl.Visible  := Creds;
  GAdminPassEdit.Visible := Creds;
  GAdminGo.Visible       := Creds;
  GAdminStatus.Visible   := Creds;

  GModePrompt.Visible    := Mode;
  GOnlineRadio.Visible   := Mode;
  GOnlineNote.Visible    := Mode;
  GOfflineRadio.Visible  := Mode;
  GOfflineNote.Visible   := Mode;

  { The plain-language sentence is for the shop. Once the radios are up they
    say the same thing better, and two answers on one page would be one too
    many. }
  GModeSummary.Visible   := not Mode;
end;

procedure AdminButtonClick(Sender: TObject);
begin
  GAdminOpen := True;
  UpdateAdminArea;
  WizardForm.ActiveControl := GAdminUserEdit;
end;

{ The unlock itself. Wrong credentials say so on the page and clear the
  password; they do not stop the wizard, because the shopkeeper who wandered in
  here still has an install to finish. }
procedure TryAdminUnlock;
begin
  if GAdminUnlocked then
    Exit;

  if not AdminCredentialsOK(GAdminUserEdit.Text, GAdminPassEdit.Text) then
  begin
    GAdminTries := GAdminTries + 1;
    { The count, never the values. A setup log is a file a shop mails to
      support, and a password typed into the wrong box has no business in it. }
    Log('administrator unlock refused (attempt ' + IntToStr(GAdminTries) + ')');
    GAdminPassEdit.Text := '';
    GAdminStatus.Caption := 'That user name and password were not accepted.';
    GAdminStatus.AdjustHeight;
    WizardForm.ActiveControl := GAdminPassEdit;
    Exit;
  end;

  Log('administrator unlock accepted; the mode question is now on the page');
  GAdminUnlocked := True;
  { Nothing is kept. The digest was compared and both boxes are emptied, so
    from here on the running installer holds neither value. }
  GAdminUserEdit.Text := '';
  GAdminPassEdit.Text := '';
  GAdminStatus.Caption := '';

  { Whatever the mode was, the radios open on it. It is Online unless a
    /Mode=offline command line was itself accompanied by these credentials. }
  GOnlineRadio.Checked  := (GSyncMode <> 'offline');
  GOfflineRadio.Checked := (GSyncMode = 'offline');

  UpdateAdminArea;
  WizardForm.ActiveControl := GOnlineRadio;
end;

procedure AdminGoClick(Sender: TObject);
begin
  TryAdminUnlock;
end;

{ Enter in either credential box means "unlock", not "Next". Key is cleared so
  the edit does not also beep at a character it cannot show. }
procedure AdminKeyPress(Sender: TObject; var Key: Char);
begin
  if Key = #13 then
  begin
    Key := #0;
    TryAdminUnlock;
  end;
end;

{ Heights come from Inno's own CodeClasses.iss example, which is the only place
  the idiom is written down: an edit is ScaleY(23), a radio ScaleY(17), a button
  ScaleY(23). None of those controls scales itself when it is built in code, so
  leaving the height alone is what puts a 150% caption in a 100% box. Static
  text is the exception and measures itself - that is what LayoutLabel is for.

  THE HEIGHT BUDGET, if you are about to lengthen a caption. Every label here is
  written to fit the line count below at the NARROWEST this page ever is - the
  default wizard width, ignoring the WizardSizePercent=120 that makes it wider.
  Because DPI scales the surface and the font together, the counts hold at 125%
  and 150% too; what breaks them is a longer sentence, not a bigger screen.

      heading 1 line, hint 1, edit, summary 2                 ~111
      separator + gap                                         ~123  <- AdminRow
    unlocked:  prompt 1, radio, note 1, radio, note 2         ~225
    open:      button, user, password, status 1               ~231

  against roughly 244 of surface at the default size and ~293 at 120%. Add a
  line to any of these captions and check it still lands under 244. }
procedure CreateSetupQuestionsPage;
var
  Lbl: TNewStaticText;
  Sep: TBevel;
  Y, AdminRow, LabelW, EditW, BtnW: Integer;
begin
  GSetupPage := CreateCustomPage(wpSelectDir,
    'Your shop',
    'One question. Satpuda Core will not ask again when it starts.');

  Y := 0;

  Y := LayoutLabel(GSetupPage, Lbl,
         'What is your shop called?', Y, 0, 2, True);
  Y := LayoutLabel(GSetupPage, Lbl,
         'The name on your bills and on your backup folder.', Y, 0, 6, False);

  GNameEdit := TNewEdit.Create(GSetupPage);
  GNameEdit.Parent := GSetupPage.Surface;
  GNameEdit.Left   := 0;
  GNameEdit.Top    := Y;
  GNameEdit.Width  := GSetupPage.SurfaceWidth;
  GNameEdit.Height := ScaleY(23);
  GNameEdit.Text   := GStoreName;
  Y := Y + GNameEdit.Height + ScaleY(14);

  { What happens next, said once, in the words a shopkeeper would use. No
    choice attached: this is the mode, not an offer of one. }
  Y := LayoutLabel(GSetupPage, GModeSummary,
         'Your shop will be set up ONLINE, on the Satpuda server, so the Android ' +
         'app can share it. Needs internet. Starts a free trial.', Y, 0, 14, False);

  { --- the line the shop stops at ------------------------------------- }
  Sep := TBevel.Create(GSetupPage);
  Sep.Parent := GSetupPage.Surface;
  Sep.Left   := 0;
  Sep.Top    := Y;
  Sep.Width  := GSetupPage.SurfaceWidth;
  Sep.Height := ScaleY(2);
  Y := Y + Sep.Height + ScaleY(10);

  { Everything below the line lives at one of two Y positions and never both:
    the Administrator button and, once the credentials are accepted, the mode
    question that REPLACES it. Keeping them on the same line is what stops the
    unlocked page having a hole in it where the button used to be. }
  AdminRow := Y;

  GAdminButton := TNewButton.Create(GSetupPage);
  GAdminButton.Parent  := GSetupPage.Surface;
  GAdminButton.Caption := 'Administrator';
  GAdminButton.Left    := 0;
  GAdminButton.Top     := Y;
  { Sized to the caption at the font actually in use, rather than to a number
    that happened to fit on the developer's 100% screen. }
  GAdminButton.Width   := WizardForm.CalculateButtonWidth([GAdminButton.Caption]);
  GAdminButton.Height  := ScaleY(23);
  GAdminButton.OnClick := @AdminButtonClick;
  BtnW := GAdminButton.Width;

  { To the right of the button, on the same line. }
  GAdminHint := TNewStaticText.Create(GSetupPage);
  GAdminHint.Parent   := GSetupPage.Surface;
  GAdminHint.Left     := BtnW + ScaleX(12);
  GAdminHint.Top      := Y + ScaleY(4);
  GAdminHint.Width    := GSetupPage.SurfaceWidth - BtnW - ScaleX(12);
  GAdminHint.AutoSize := False;
  GAdminHint.WordWrap := True;
  GAdminHint.Caption  := 'For Satpuda staff. Not needed to install a shop.';
  GAdminHint.AdjustHeight;

  { The credential row sits UNDER the button, which stays on screen (disabled)
    while it is being filled in. }
  Y := Y + GAdminButton.Height + ScaleY(12);

  { --- state "open": the credentials ---------------------------------- }
  LabelW := ScaleX(70);
  BtnW   := WizardForm.CalculateButtonWidth(['Unlock']);
  EditW  := GSetupPage.SurfaceWidth - LabelW - BtnW - ScaleX(8);
  if EditW > ScaleX(210) then
    EditW := ScaleX(210);

  GAdminUserLbl := TNewStaticText.Create(GSetupPage);
  GAdminUserLbl.Parent   := GSetupPage.Surface;
  GAdminUserLbl.Left     := 0;
  GAdminUserLbl.Top      := Y + ScaleY(4);
  GAdminUserLbl.Width    := LabelW;
  GAdminUserLbl.AutoSize := False;
  GAdminUserLbl.Caption  := 'User name';
  GAdminUserLbl.AdjustHeight;

  GAdminUserEdit := TNewEdit.Create(GSetupPage);
  GAdminUserEdit.Parent     := GSetupPage.Surface;
  GAdminUserEdit.Left       := LabelW;
  GAdminUserEdit.Top        := Y;
  GAdminUserEdit.Width      := EditW;
  GAdminUserEdit.Height     := ScaleY(23);
  GAdminUserEdit.OnKeyPress := @AdminKeyPress;
  Y := Y + GAdminUserEdit.Height + ScaleY(6);

  GAdminPassLbl := TNewStaticText.Create(GSetupPage);
  GAdminPassLbl.Parent   := GSetupPage.Surface;
  GAdminPassLbl.Left     := 0;
  GAdminPassLbl.Top      := Y + ScaleY(4);
  GAdminPassLbl.Width    := LabelW;
  GAdminPassLbl.AutoSize := False;
  GAdminPassLbl.Caption  := 'Password';
  GAdminPassLbl.AdjustHeight;

  { TPasswordEdit is the masked edit Inno's own password page uses; Password is
    set anyway so the intent survives a reader who does not know the class. }
  GAdminPassEdit := TPasswordEdit.Create(GSetupPage);
  GAdminPassEdit.Parent     := GSetupPage.Surface;
  GAdminPassEdit.Left       := LabelW;
  GAdminPassEdit.Top        := Y;
  GAdminPassEdit.Width      := EditW;
  GAdminPassEdit.Height     := ScaleY(23);
  GAdminPassEdit.Password   := True;
  GAdminPassEdit.OnKeyPress := @AdminKeyPress;

  GAdminGo := TNewButton.Create(GSetupPage);
  GAdminGo.Parent  := GSetupPage.Surface;
  GAdminGo.Caption := 'Unlock';
  GAdminGo.Left    := LabelW + EditW + ScaleX(8);
  GAdminGo.Top     := Y;
  GAdminGo.Width   := BtnW;
  GAdminGo.Height  := ScaleY(23);
  GAdminGo.OnClick := @AdminGoClick;
  Y := Y + GAdminPassEdit.Height + ScaleY(8);

  Y := LayoutBlankLabel(GSetupPage, GAdminStatus, Y, 0, 0);

  { --- state "unlocked": the mode, where the button was ---------------- }
  Y := AdminRow;

  Y := LayoutLabel(GSetupPage, GModePrompt,
         'Where should this shop''s data be kept?', Y, 0, 4, True);

  GOnlineRadio := TNewRadioButton.Create(GSetupPage);
  GOnlineRadio.Parent  := GSetupPage.Surface;
  GOnlineRadio.Left    := 0;
  GOnlineRadio.Top     := Y;
  GOnlineRadio.Width   := GSetupPage.SurfaceWidth;
  GOnlineRadio.Height  := ScaleY(17);
  GOnlineRadio.Caption := 'Online  -  on the Satpuda server';
  { Online unless /Mode=offline arrived WITH the credentials, in which case
    InitializeWizard has already unlocked the page and this opens on Offline. }
  GOnlineRadio.Checked := (GSyncMode <> 'offline');
  Y := Y + GOnlineRadio.Height + ScaleY(2);

  Y := LayoutLabel(GSetupPage, GOnlineNote,
         'The expiry date is the server''s, not this computer''s.', Y, 16, 8, False);

  GOfflineRadio := TNewRadioButton.Create(GSetupPage);
  GOfflineRadio.Parent  := GSetupPage.Surface;
  GOfflineRadio.Left    := 0;
  GOfflineRadio.Top     := Y;
  GOfflineRadio.Width   := GSetupPage.SurfaceWidth;
  GOfflineRadio.Height  := ScaleY(17);
  GOfflineRadio.Caption := 'Offline  -  on this computer';
  GOfflineRadio.Checked := (GSyncMode = 'offline');
  Y := Y + GOfflineRadio.Height + ScaleY(2);

  Y := LayoutLabel(GSetupPage, GOfflineNote,
         'Works with no internet, backed up to Google Drive. The expiry date then ' +
         'lives on this computer, where it can be edited.', Y, 16, 0, False);

  UpdateAdminArea;
end;

{ ======================================================================== }
{  NO STORE LIST, EVER                                                      }
{                                                                           }
{  The rule: this installer must never put a shop that already exists in    }
{  front of anybody, and must never hand the app a name that could resolve  }
{  to one. There are exactly three ways it could have, and all three are    }
{  closed here rather than trusted to stay closed by accident.              }
{                                                                           }
{  1. A list on the wizard. There is none, and there must not be one. The   }
{     installer has no credential to ask the server for stores with - see   }
{     core/server_api.py, provision_trial - so it could not build a list of }
{     server stores even if somebody wanted it to, and it must not build    }
{     one out of local Store_* folders either. The one screen in the        }
{     product that DOES list stores is Settings > Data & System > Stores,   }
{     behind the app's own administrator area, which is where a shop that   }
{     genuinely has to re-join a store on the server is sent.               }
{                                                                           }
{  2. The name reaching an existing store on the server. In Online mode it  }
{     cannot: /api/provision/trial only ever INSERTs (server                }
{     src/services/provisionService.js), and the PC is then pinned to the   }
{     store_id the server just made, so nothing later matches this shop to  }
{     a server store by its display NAME. Offline is the mode that skips    }
{     all of that and leaves an unpinned store behind - which is the second }
{     reason, after the editable expiry date, that it is now behind the     }
{     Administrator password.                                               }
{                                                                           }
{  3. A handoff landing on a PC that already has a shop. This is the one    }
{     that was actually open. WriteProvisionFile only checked that a name   }
{     had been collected, and /StoreName= puts a name in GStoreName even    }
{     when the wizard page was skipped - so re-running the installer with   }
{     /StoreName= on a PC that already had store folders wrote a handoff    }
{     asking for a BRAND NEW store beside them. The app's own guard         }
{     (already_set_up in core/trial_activation.py) is has_registry AND      }
{     activated, so a PC with store folders that never finished activating  }
{     sailed through it, ended up with two stores, and the only way back    }
{     out is the store list in Settings. Closed below.                      }
{ ======================================================================== }

{ True when a handoff must NOT be written: this Windows user already has a shop
  here. Deliberately the same test the wizard page is skipped on, so "we did not
  ask" and "we do not answer" can never drift apart. Deliberately NOT
  GHaveInstall as well: a PC with the program installed and no shop yet is a
  fresh shop, and a silent /StoreName= install of it should still work. }
function ForbidStoreChoice: Boolean;
begin
  Result := AlreadySetUpHere;
end;

{ Write the answers where the app will find them on its first launch.
  Returns nothing: a failure here costs the shopkeeper one question on the
  activation screen, which is not a reason to fail an install that worked. }
procedure WriteProvisionFile;
var
  Lines: TArrayOfString;
  Dir, Path, Mode: String;
begin
  if GStoreName = '' then
    Exit;

  if ForbidStoreChoice then
  begin
    Log('handoff not written: this Windows user already has a shop here');
    Exit;
  end;

  { The mode is pinned here as well as at every place it is set, because this is
    the last line of code between a choice and the app. Anything that is not the
    administrator's deliberate Offline is Online. }
  if GAdminUnlocked and (GSyncMode = 'offline') then
    Mode := 'offline'
  else
    Mode := 'online';
  if Mode <> GSyncMode then
    Log('handoff mode corrected from "' + GSyncMode + '" to "' + Mode + '"');

  SetArrayLength(Lines, 1);
  Lines[0] := '{"store_name": "' + JsonEscape(GStoreName) +
              '", "sync_mode": "' + Mode + '"}';

  Dir := AppDataDir;
  if ForceDirectories(Dir) then
  begin
    Path := AddBackslash(Dir) + 'provision.json';
    { UTF-8, because a shop name is often not English. The app reads this file
      as utf-8-sig, so the byte-order mark this writes is expected. }
    if not SaveStringsToUTF8File(Path, Lines, False) then
      Log('could not write ' + Path);
  end
  else
    Log('could not create ' + Dir);

  { An all-users install (/ALLUSERS) runs elevated, and %LOCALAPPDATA% is then
    the ADMINISTRATOR's folder, not the shopkeeper's - so the file above would
    be waiting in a profile that never opens the app. Leave a copy where every
    user of this PC can read it. The app takes it once per Windows user and
    records that in the user's own folder, because ProgramData is shared and a
    standard user cannot always delete a file an administrator created. }
  if IsAdminInstallMode then
  begin
    Dir := ExpandConstant('{commonappdata}\SatpudaCore');
    if ForceDirectories(Dir) then
    begin
      Path := AddBackslash(Dir) + 'provision.json';
      if not SaveStringsToUTF8File(Path, Lines, False) then
        Log('could not write ' + Path);
    end;
  end;
end;

{ ======================================================================== }
{  Turning a worker failure into a sentence the shopkeeper can act on       }
{ ======================================================================== }

function FriendlyError(const Code, Raw: String): String;
begin
  if Code = 'NET' then
    Result :=
      'Satpuda Core could not be downloaded.' + #13#10#13#10 +
      'Check that this computer is connected to the internet, then run this installer again. ' +
      'It will carry on from where it stopped, so nothing already downloaded is wasted.'
  else if (Code = 'SIZE') or (Code = 'HASH') or (Code = 'ZIP') then
    Result :=
      'The download arrived damaged and was thrown away.' + #13#10#13#10 +
      'This is usually a weak internet connection. Run this installer again to download it fresh.'
  else if Code = 'EXTRACT' then
    Result :=
      'Satpuda Core downloaded correctly but could not be unpacked.' + #13#10#13#10 +
      'The two usual reasons are the disk being full, or antivirus holding on to the files. ' +
      'Free up about 2 GB on this drive, and if the problem repeats, allow this installer in your antivirus and try again.' + #13#10#13#10 +
      'Details: ' + Raw
  else if Code = 'LOCKED' then
    Result :=
      'Satpuda Core is still open, so its folder could not be replaced.' + #13#10#13#10 +
      'Close Satpuda Core - and any Explorer window showing its folder - then run this installer again.' + #13#10#13#10 +
      'Your existing Satpuda Core has not been touched and still works. The download is already saved on ' +
      'this computer, so running the installer again will only take a minute.'
  else if Code = 'SWAP' then
    Result :=
      'The new files could not be moved into place. Your previous version has been left working.' + #13#10#13#10 +
      'Restart the computer and run this installer again.' + #13#10#13#10 + 'Details: ' + Raw
  else if Code = 'LAYOUT' then
    Result :=
      'The downloaded package did not contain Satpuda Core where it was expected.' + #13#10#13#10 +
      'The published zip has probably changed shape. Nothing on this computer was altered. ' +
      'Please tell Roshan, and mention version ' + TargetTag + '.'
  else if (Code = 'GONE') or (Code = 'NOASSET') then
    Result :=
      'The Satpuda Core download is no longer on the release page.' + #13#10#13#10 +
      'Nothing on this computer was altered. Please tell Roshan that {#MyAssetName} is missing from release ' + TargetTag + '.'
  else if Code = 'FORBID' then
    Result :=
      'GitHub refused to hand over the download.' + #13#10#13#10 +
      'The release is most likely private. It has to be public for the installer to work. Please ask Roshan to make it public.'
  else if Raw <> '' then
    Result := Raw
  else
    Result := 'The installation could not be completed.';
end;

{ ======================================================================== }
{  Worker output -> progress bar                                            }
{  Called line by line while satpuda_fetch.ps1 runs in its own process.      }
{ ======================================================================== }

procedure WorkerLog(const S: String; const Error, FirstLine: Boolean);
var
  Rest, Msg: String;
  P, Pct: Integer;
begin
  if S = '' then Exit;
  Log('[fetch] ' + S);

  if Copy(S, 1, 2) = 'P|' then
  begin
    Rest := Copy(S, 3, Length(S));
    P := Pos('|', Rest);
    if P > 0 then
    begin
      Pct := StrToIntDef(Copy(Rest, 1, P - 1), -1);
      Msg := Copy(Rest, P + 1, Length(Rest));
      if (Pct >= 0) and (GProgressPage <> nil) then
      begin
        GProgressPage.SetProgress(Pct, 100);
        GProgressPage.SetText(GStatusText, Msg);
      end;
    end;
  end
  else if Copy(S, 1, 2) = 'S|' then
  begin
    GStatusText := Copy(S, 3, Length(S));
    if GProgressPage <> nil then
      GProgressPage.SetText(GStatusText, '');
  end
  else if Copy(S, 1, 4) = 'PID|' then
  begin
    { The worker's own process id. Without it, a worker that stops answering
      can only be killed by name - which would also kill any other PowerShell
      the shopkeeper happens to be running. }
    GWorkerPid := StrToIntDef(Copy(S, 5, Length(S)), 0);
    Log('worker pid = ' + IntToStr(GWorkerPid));
  end
  else if Copy(S, 1, 2) = 'V|' then
    GPayloadTag := Copy(S, 3, Length(S))
  else if Copy(S, 1, 9) = 'NOCANCEL|' then
  begin
    { The worker has started swapping the folders. That takes a second or two
      and interrupting it is the one moment that could leave the shop with
      neither the old nor the new copy, so Stop is taken away for it. }
    GNoCancel := True;
    if GStopButton <> nil then
    begin
      GStopButton.Enabled := False;
      GStopButton.Caption := 'Finishing...';
    end;
    if WizardForm <> nil then
      WizardForm.CancelButton.Enabled := False;
  end
  else if Copy(S, 1, 2) = 'W|' then
    GWarnings := GWarnings + Copy(S, 3, Length(S)) + #13#10
  else if Copy(S, 1, 2) = 'E|' then
  begin
    Rest := Copy(S, 3, Length(S));
    P := Pos('|', Rest);
    if P > 0 then
    begin
      GErrCode := Copy(Rest, 1, P - 1);
      GErrMsg  := Copy(Rest, P + 1, Length(Rest));
    end
    else
      GErrMsg := Rest;
  end
  else if Copy(S, 1, 3) = 'OK|' then
    GWorkerOK := True;
end;

{ ======================================================================== }
{  Stopping                                                                 }
{                                                                           }
{  WHY THE OLD VERSION'S CANCEL DID NOTHING                                 }
{                                                                           }
{  The download runs from PrepareToInstall, on a page made by               }
{  CreateOutputProgressPage. Inno gives that page the psNoButtons style, and }
{  UpdateCurPageButtonState answers psNoButtons by HIDING Back, Next and     }
{  Cancel - and by greying the window's X, through                          }
{  EnableMenuItem(..., SC_CLOSE, MF_GRAYED). Inno's own comment on that page }
{  says it plainly: "the user shouldn't be able to cancel or do anything     }
{  else during this time". On top of that, Inno's ShowPreparing disables the }
{  Cancel button just before it calls PrepareToInstall.                      }
{                                                                           }
{  The old script tried to win that argument by poking Visible and Enabled   }
{  back on. Nothing put the X back, so the X stayed dead; and the whole      }
{  cancel path still had to travel through TMainForm.Close, whose first test }
{  is WizardForm.CancelButton.CanFocus - a chain of Inno internals to bet a  }
{  122 MB download on. The owner's own install log settles it: a script      }
{  MsgBox goes through Inno's LoggedMsgBox and is written to the log, and    }
{  the log from his run has NO "Stop installing Satpuda Core?" line at all,  }
{  only the folder-already-exists prompt from the start. The confirmation    }
{  never ran, so the flag file was never written.                            }
{                                                                           }
{  So this version does what Inno itself does for its built-in download and  }
{  extract pages: it puts a real button ON THE PAGE. TDownloadWizardPage and }
{  TExtractionWizardPage each create their own FAbortButton on the page      }
{  surface for exactly this reason. A button on the surface is clicked       }
{  directly, is untouched by UpdateCurPageButtonState, and needs none of     }
{  Inno's cancel plumbing to work.                                           }
{                                                                           }
{  The wizard's Cancel button and the X are still repaired as well, so all   }
{  three do the same thing.                                                  }
{ ======================================================================== }

{ Is a given process id still alive? tasklist quotes the filter back at you
  when it refuses, so an "ERROR" anywhere means "cannot tell" - and the safe
  answer to "cannot tell" here is "not running", which lets us stop waiting. }
function PidIsAlive(Pid: Integer): Boolean;
var
  TmpFile, Text: String;
  Output: AnsiString;
  RC: Integer;
begin
  Result := False;
  if Pid <= 0 then Exit;
  TmpFile := ExpandConstant('{tmp}\pid.txt');
  if Exec(ExpandConstant('{cmd}'),
          '/C tasklist /FI "PID eq ' + IntToStr(Pid) + '" /NH > "' + TmpFile + '" 2>&1',
          '', SW_HIDE_, ewWaitUntilTerminated, RC) then
    if LoadStringFromFile(TmpFile, Output) then
    begin
      { Widened by assignment, not by a cast: LoadStringFromFile hands back an
        AnsiString and passing one straight into LowerCase(String) is a compile
        error, not a warning. }
      Text := Output;
      Text := LowerCase(Text);
      if Pos('error', Text) = 0 then
        Result := Pos(IntToStr(Pid), Text) > 0;
    end;
  DeleteFile(TmpFile);
end;

procedure KillWorker;
var
  RC: Integer;
begin
  if GWorkerPid <= 0 then Exit;
  Log('worker did not stop on its own; killing pid ' + IntToStr(GWorkerPid));
  { /T so the tree goes with it: PowerShell is the parent of nothing here, but
    a future payload step could spawn something and an orphan holding the
    staging folder is worse than no cleanup at all. }
  Exec(ExpandConstant('{cmd}'), '/C taskkill /PID ' + IntToStr(GWorkerPid) + ' /T /F',
       '', SW_HIDE_, ewWaitUntilTerminated, RC);
end;

{ Everything Stop has to do, from wherever it was clicked. }
procedure RequestStop;
var
  I: Integer;
begin
  if not GWorkerRunning then Exit;

  { The wait loop below pumps the message queue, so a second click on Stop -
    or on the wizard's Cancel button - lands right back in here. Without this
    guard the shopkeeper gets a second confirmation on top of the first and two
    kill timers running against the same process. }
  if GStopping then Exit;

  if GNoCancel then
  begin
    MsgBox('Satpuda Core is putting the new files in place. This takes only a moment ' +
           'and stopping now is the one thing that could leave you without a working copy, ' +
           'so please let it finish.',
           mbInformation, MB_OK);
    Exit;
  end;

  if MsgBox('Stop installing {#MyAppName}?' + #13#10#13#10 +
            'Nothing on this computer will be changed. What has already been ' +
            'downloaded is kept, so starting again later carries on from there.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then
    Exit;

  GStopping := True;
  GCancelRequested := True;
  SaveStringToFile(GCancelFile, 'cancel', False);
  GStatusText := 'Stopping...';
  if GProgressPage <> nil then
    GProgressPage.SetText(GStatusText, 'Closing the connections');
  if GStopButton <> nil then
  begin
    GStopButton.Enabled := False;
    GStopButton.Caption := 'Stopping...';
  end;

  { The worker answers the flag in well under a second (measured: 0.4 s during
    the download, 0.6 s during extraction). Give it six, and if it is wedged -
    a socket that will not close, antivirus sitting on a file - kill it rather
    than leave 122 MB downloading behind a wizard the shopkeeper has closed.
    SetText pumps the message queue, so the window keeps repainting. }
  for I := 1 to 30 do
  begin
    if (GWorkerPid > 0) and (not PidIsAlive(GWorkerPid)) then Exit;
    if GProgressPage <> nil then
      GProgressPage.SetText(GStatusText, 'Closing the connections');
    Sleep(200);
  end;
  KillWorker;
end;

procedure StopButtonClick(Sender: TObject);
begin
  RequestStop;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  { The wizard's own Cancel button and the X both land here. While the worker
    is running they mean the same thing as the Stop button on the page, and
    the wizard must NOT be torn down - that would orphan the worker. }
  if GWorkerRunning then
  begin
    RequestStop;
    Cancel  := False;
    Confirm := False;
    Exit;
  end;

  { Handing over to the uninstaller: this close was asked for on purpose, so
    do not ask "are you sure you want to exit" on top of it. }
  if GMaintAction = 'remove' then
  begin
    Cancel  := True;
    Confirm := False;
  end;
end;

{ Put the wizard's Cancel button and the window's X back, on a page where Inno
  has deliberately taken both away. Best effort - the Stop button on the page
  surface is the one that has to work. }
procedure RepairWizardCancelAffordances;
var
  Menu: THandle;
begin
  try
    WizardForm.CancelButton.Visible := True;
    WizardForm.CancelButton.Enabled := True;
  except
    Log('could not re-show the wizard Cancel button: ' + GetExceptionMessage);
  end;
  try
    Menu := GetSystemMenu(WizardForm.Handle, 0);
    if Menu <> 0 then
      EnableMenuItem(Menu, SC_CLOSE_, MF_BYCOMMAND_ or MF_ENABLED_);
  except
    Log('could not re-enable the window close button: ' + GetExceptionMessage);
  end;
end;

{ ======================================================================== }
{  Shortcut creation that survives a hostile desktop folder                 }
{                                                                           }
{  Why this is not just CreateShellLink: on real shop PCs the Desktop folder }
{  is regularly (a) redirected into OneDrive, (b) redirected by group policy }
{  to a server share that is offline, (c) write-blocked by Windows Defender  }
{  Controlled Folder Access, or (d) simply absent on a fresh roaming profile.}
{  CreateShellLink raises an exception in some of those cases and quietly    }
{  produces nothing in others, so every attempt is wrapped, write-tested     }
{  first, and the resulting .lnk is checked for on disk afterwards.          }
{ ======================================================================== }

function DirIsWritable(const Dir: String): Boolean;
var
  TestFile: String;
begin
  Result := False;
  if Dir = '' then Exit;
  if not DirExists(Dir) then Exit;
  TestFile := AddBackslash(Dir) + 'satpuda_write_test.tmp';
  try
    if SaveStringToFile(TestFile, 'x', False) then
    begin
      DeleteFile(TestFile);
      Result := True;
    end;
  except
    Result := False;
  end;
end;

{ Try one folder. Returns the .lnk path that really exists on disk, or ''. }
function TryShortcutIn(const Dir, LinkName, Target, Args, WorkDir, IconFile: String): String;
var
  Created: String;
begin
  Result := '';
  if Dir = '' then Exit;

  if not DirExists(Dir) then
  begin
    { Only create it if it is the plain local Desktop. If a REDIRECTED folder is
      missing, the redirection target (OneDrive, or a server share) is offline,
      and recreating it locally would put the icon somewhere Explorer never
      shows the user. Better to fall through to the next candidate. }
    if (GetEnv('USERPROFILE') <> '') and
       (CompareText(Dir, AddBackslash(GetEnv('USERPROFILE')) + 'Desktop') = 0) then
    begin
      if not ForceDirectories(Dir) then Exit;
    end
    else
      Exit;
  end;

  if not DirIsWritable(Dir) then
  begin
    Log('shortcut: ' + Dir + ' is not writable');
    Exit;
  end;

  try
    Created := CreateShellLink(AddBackslash(Dir) + LinkName + '.lnk',
                               '{#MyAppName}', Target, Args, WorkDir, IconFile, 0, SW_SHOWNORMAL);
  except
    Log('shortcut: CreateShellLink raised in ' + Dir + ': ' + GetExceptionMessage);
    Exit;
  end;

  { CreateShellLink can return without error and still leave nothing behind if
    antivirus removes the .lnk. Trust the filesystem, not the return value. }
  if (Created <> '') and FileExists(Created) then
  begin
    Result := Created;
    Log('shortcut created: ' + Created);
  end
  else
    Log('shortcut: nothing on disk afterwards in ' + Dir);
end;

{ Ordered list of places a desktop icon could go. }
function DesktopCandidate(Index: Integer): String;
var
  S: String;
begin
  Result := '';
  case Index of
    { The expanded value Explorer actually uses, redirection included. }
    0: if RegQueryStringValue(HKCU,
         'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders', 'Desktop', S) then Result := S;
    1: Result := ExpandConstant('{userdesktop}');
    2: if GetEnv('USERPROFILE') <> '' then Result := AddBackslash(GetEnv('USERPROFILE')) + 'Desktop';
    3: if GetEnv('OneDrive')    <> '' then Result := AddBackslash(GetEnv('OneDrive'))    + 'Desktop';
    { Last resort: the shared desktop. Works for every user of this PC. }
    4: Result := ExpandConstant('{commondesktop}');
  end;
end;

procedure CreateDesktopShortcut;
var
  I: Integer;
  Dir, Target, IconFile, Tried: String;
begin
  GDesktopLnk := '';
  GDesktopProblem := '';
  Target   := ExpandConstant('{app}\app\{#MyAppExeName}');
  IconFile := Target;
  Tried    := '';

  for I := 0 to 4 do
  begin
    Dir := DesktopCandidate(I);
    if Dir = '' then Continue;
    { Skip duplicates - the registry value and the userdesktop constant are
      usually the same folder, and there is no point write-testing it twice. }
    if Pos(#13 + Uppercase(Dir) + #13, #13 + Uppercase(Tried) + #13) > 0 then Continue;
    Tried := Tried + Dir + #13;

    GDesktopLnk := TryShortcutIn(Dir, '{#MyAppName}', Target, '', ExpandConstant('{app}\app'), IconFile);
    if GDesktopLnk <> '' then
    begin
      if I = 4 then
        Log('desktop icon went to the shared Public desktop');
      Exit;
    end;
  end;

  { Every candidate failed. This must NOT stop the install - the program is
    perfectly usable from the Start Menu, and losing the whole install over a
    missing icon is a far worse outcome than losing the icon. }
  GDesktopProblem :=
    'Satpuda Core is installed, but a desktop icon could not be created.' + #13#10 +
    'Open it from the Start Menu instead: Start > Satpuda Core.';
  Log('desktop icon failed in every candidate folder: ' + Tried);
end;

procedure CreateStartMenuShortcut;
var
  Dir, Target, Ignored: String;
begin
  GStartMenuLnk := '';
  Target := ExpandConstant('{app}\app\{#MyAppExeName}');

  Dir := ExpandConstant('{autoprograms}\{#MyAppName}');
  ForceDirectories(Dir);
  GStartMenuLnk := TryShortcutIn(Dir, '{#MyAppName}', Target, '', ExpandConstant('{app}\app'), Target);

  if (GStartMenuLnk = '') and (GetEnv('APPDATA') <> '') then
  begin
    Dir := AddBackslash(GetEnv('APPDATA')) + 'Microsoft\Windows\Start Menu\Programs\{#MyAppName}';
    ForceDirectories(Dir);
    GStartMenuLnk := TryShortcutIn(Dir, '{#MyAppName}', Target, '', ExpandConstant('{app}\app'), Target);
  end;

  if GStartMenuLnk = '' then
    Log('start menu shortcut could not be created')
  else
  begin
    { "Check for updates" next to it. Best effort - never a reason to fail.
      Result assigned to a throwaway rather than discarded, so the script does
      not depend on the compiler tolerating an ignored function result. }
    Ignored := TryShortcutIn(ExtractFileDir(GStartMenuLnk), 'Check for {#MyAppName} updates',
      PSExePath,
      '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ' +
        Q(ExpandConstant('{app}\satpuda_update.ps1')) + ' -AppRoot ' + Q(ExpandConstant('{app}')),
      ExpandConstant('{app}'), ExpandConstant('{app}\satpuda.ico'));
  end;
end;

{ Logon update check. A .vbs launched by wscript starts PowerShell with a
  hidden window and no console flash; a .lnk straight to powershell.exe cannot
  do that. If antivirus blocks the .vbs only the automatic check is lost - the
  app still starts, because its shortcut points at the .exe directly. }
procedure CreateStartupUpdateCheck;
var
  VbsPath, Cmd, Lnk: String;
begin
  VbsPath := ExpandConstant('{app}\satpuda_update_silent.vbs');

  { The script works out its own folder instead of having the install path
    baked in. That keeps this file pure ASCII, so it cannot be corrupted by
    SaveStringToFile's encoding when the Windows user name contains non-English
    characters - which is exactly the kind of PC this has to work on. }
  Cmd :=
    'Set fso = CreateObject("Scripting.FileSystemObject")' + #13#10 +
    'base = fso.GetParentFolderName(WScript.ScriptFullName)' + #13#10 +
    'Set sh = CreateObject("WScript.Shell")' + #13#10 +
    'sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ ' +
      '& base & "\satpuda_update.ps1"" -Silent -AppRoot """ & base & """", 0, False' + #13#10;

  if not SaveStringToFile(VbsPath, Cmd, False) then
  begin
    Log('could not write the startup update script');
    Exit;
  end;

  Lnk := TryShortcutIn(ExpandConstant('{userstartup}'), '{#MyAppName} update check',
           ExpandConstant('{sys}\wscript.exe'), '//B ' + Q(VbsPath),
           ExpandConstant('{app}'), ExpandConstant('{app}\satpuda.ico'));
  if Lnk = '' then
    Log('startup update-check shortcut could not be created')
  else
    RegWriteStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'StartupLnk', Lnk);
end;

{ ======================================================================== }
{  Pre-flight                                                               }
{ ======================================================================== }

{ Is ExeName running? LoadStringFromFile's second parameter is declared
  "var S: AnsiString", so Output MUST be an AnsiString - passing a String is a
  compile error, not a warning. }
function ProcessIsRunning(const ExeName: String): Boolean;
var
  TmpFile, Text: String;
  Output: AnsiString;
  RC: Integer;
begin
  Result := False;
  TmpFile := ExpandConstant('{tmp}\tl.txt');
  if Exec(ExpandConstant('{cmd}'),
          '/C tasklist /FI "IMAGENAME eq ' + ExeName + '" /NH > "' + TmpFile + '" 2>&1',
          '', SW_HIDE_, ewWaitUntilTerminated, RC) then
    if LoadStringFromFile(TmpFile, Output) then
    begin
      { Widened by assignment rather than a String(...) cast: assignment between
        the two string types is plain Pascal, the cast syntax is not. }
      Text := Output;
      Text := LowerCase(Text);

      { stderr is folded into the same file by the 2>&1 above, and tasklist
        quotes the filter back at you when it refuses ("ERROR: Invalid
        argument/option - 'IMAGENAME eq SatpudaCore_Desktop.exe'"). A plain
        substring test would read that as "the app is running" and pin the
        Retry loop on a lie that no amount of closing windows can clear. A real
        listing never says ERROR, so treat that as "cannot tell" - which means
        "not running", the answer that lets the install go ahead.

        The matching "no tasks are running" line does not contain the image
        name at all, so it needs no special case. }
      if Pos('error', Text) = 0 then
        Result := Pos(LowerCase(ExeName), Text) > 0;
    end;
  DeleteFile(TmpFile);
end;

{ The payload ships TWO executables that lock the app\app folder: the Tauri
  shell and the Python data engine it spawns. A crashed shell leaves
  SatpudaEngine.exe running with nothing visible on screen, so checking only the
  shell tells the shopkeeper to "close Satpuda Core" when he already has.
  Check both. }
function AppIsRunning: Boolean;
begin
  Result := ProcessIsRunning('{#MyAppExeName}') or ProcessIsRunning('{#MyEngineExeName}');
end;

{ Ends the data engine the window left behind, and waits (up to 5 s) for it to go. }
procedure StopLeftoverEngine;
var
  RC, I: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#MyEngineExeName} /T /F', '',
       SW_HIDE_, ewWaitUntilTerminated, RC);
  for I := 1 to 20 do
  begin
    if not ProcessIsRunning('{#MyEngineExeName}') then Exit;
    Sleep(250);
  end;
end;

{ True once Satpuda Core is closed, False when the shopkeeper cancels.
  With the window still open the shopkeeper closes it (the engine then stops
  with it). With only the engine left -- it outlives the window while it takes
  the closing backup, and a crashed window leaves it behind for good -- the
  installer offers to end it instead of asking for a Retry that a shopkeeper
  with no window to close can only answer by guessing (5 Oct 2026). }
function WaitForAppClosed(const OpenMsg, EngineMsg: String): Boolean;
var
  Answer: Integer;
begin
  Result := True;
  while AppIsRunning do
  begin
    if ProcessIsRunning('{#MyAppExeName}') then
    begin
      if MsgBox(OpenMsg, mbError, MB_RETRYCANCEL) = IDCANCEL then
      begin
        Result := False;
        Exit;
      end;
    end
    else
    begin
      Answer := MsgBox(EngineMsg, mbConfirmation, MB_YESNOCANCEL);
      if Answer = IDCANCEL then
      begin
        Result := False;
        Exit;
      end;
      if Answer = IDYES then
        StopLeftoverEngine
      else
        Sleep(3000);
    end;
  end;
end;

{ ======================================================================== }
{  The maintenance page - update, repair or remove                          }
{                                                                           }
{  A shop with a damaged install used to have exactly one road back: run the }
{  installer, watch it re-download, and hope. There was nothing that SAID    }
{  what was installed and nothing that offered to put it right. This page is }
{  the first thing the wizard shows, in both directions - it names the       }
{  version that is on the computer, or says plainly that nothing is.         }
{ ======================================================================== }

function UninstallerCommand: String;
var
  S: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, APP_UNINST_KEY, 'UninstallString', S) and (S <> '') then
    Result := RemoveQuotes(S)
  else if RegQueryStringValue(HKLM, APP_UNINST_KEY, 'UninstallString', S) and (S <> '') then
    Result := RemoveQuotes(S)
  else if (GInstalledPath <> '') and FileExists(AddBackslash(GInstalledPath) + 'unins000.exe') then
    Result := AddBackslash(GInstalledPath) + 'unins000.exe';
  if (Result <> '') and (not FileExists(Result)) then
    Result := '';
end;

{ Is the release this run will install newer than what is on the disk? }
function UpdateIsAvailable: Boolean;
begin
  Result := GHaveInstall and (GInstalledTag <> '') and
            (CompareTags(TargetTag, GInstalledTag) > 0);
end;

{ Ask GitHub for the newest tag. One API call with a short timeout, run only
  when something is already installed, and only once. Failure is silent: an
  offline shop still gets a working maintenance page. }
procedure CheckLatestOnline;
var
  RC: Integer;
  Params, OutFile: String;
  Raw: AnsiString;
  S: String;
  P: Integer;
begin
  if GCheckedOnline then Exit;
  GCheckedOnline := True;

  { ExtractTemporaryFile raises on failure. This whole check is optional
    decoration on the maintenance page, so a disk-full or antivirus-blocked
    extraction must not take the page down with it. }
  try
    ExtractTemporaryFile('satpuda_fetch.ps1');
  except
    Log('online version check skipped: ' + GetExceptionMessage);
    Exit;
  end;
  OutFile := ExpandConstant('{tmp}\latest.txt');
  DeleteFile(OutFile);

  Params :=
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
      Q(ExpandConstant('{tmp}\satpuda_fetch.ps1')) +
    ' -CheckOnly' +
    ' -AppRoot '    + Q(ExpandConstant('{tmp}')) +
    ' -CacheDir '   + Q(ExpandConstant('{tmp}')) +
    ' -CancelFile ' + Q(ExpandConstant('{tmp}\never.flag')) +
    ' -Repo '       + Q('{#MyRepo}') +
    ' -OutFile '    + Q(OutFile);

  if not Exec(PSExePath, Params, ExpandConstant('{tmp}'), SW_HIDE_, ewWaitUntilTerminated, RC) then
  begin
    Log('online version check could not be started');
    Exit;
  end;
  if not LoadStringFromFile(OutFile, Raw) then Exit;
  S := Raw;              { widened by assignment; see ProcessIsRunning }
  S := Trim(S);
  P := Pos('LATEST|', S);
  if P > 0 then
    GLatestTag := Trim(Copy(S, P + 7, Length(S)));
  Log('latest tag on GitHub: "' + GLatestTag + '"');
end;

{ Put the version that is really going to be installed at the top of the
  window, replacing the one this installer happened to be compiled with. Safe to
  call more than once, and safe before the wizard exists. }
procedure ShowResolvedVersionInTitle;
var
  Num: String;
begin
  Num := TagNumber(TargetTag);
  if Num = '' then Exit;
  if WizardForm <> nil then
    WizardForm.Caption := '{#MyAppName} ' + Num + ' Setup';
end;

{ Decide once which release this run installs: GitHub's newest when it is newer
  than the version this installer was built as, else the built-in version (also
  the answer when the shop is offline or GitHub cannot be reached). }
procedure ResolveTargetTag;
begin
  if GTargetTag <> '' then Exit;
  CheckLatestOnline;
  if IsPlainTag(GLatestTag) and (CompareTags(GLatestTag, '{#MyReleaseTag}') > 0) then
    GTargetTag := GLatestTag
  else
    GTargetTag := '{#MyReleaseTag}';
  Log('installing release ' + GTargetTag + ' (built as {#MyReleaseTag}, GitHub latest "' + GLatestTag + '")');
end;

{ Laid out from the BOTTOM of the page surface upwards, and the description
  block above it simply takes whatever is left. Stacking tops downwards from
  zero is how a custom page ends up with its last radio button off the bottom
  edge on a machine whose font or DPI is not the developer's.

  What changed with the modern look: every step of the climb now comes off a
  control's REAL height rather than a ladder of magic numbers (18, 50, 68, 86,
  106, 112) that only added up at one font size. The radios in particular were
  never given a height at all - a TNewRadioButton built in code stays 17 pixels
  tall whatever the DPI is, so at 150% its caption was being drawn into a box
  two thirds the size it needed. They are ScaleY(17) now, and the wrapped note
  measures itself with AdjustHeight, which is where the old ScaleY(28) guess
  came from. The separator matches the one on the shop page. }
procedure CreateMaintenancePage;
var
  Sep: TBevel;
  H, Y, Rad: Integer;
begin
  GMaintPage := CreateCustomPage(wpWelcome,
    'Satpuda Core on this computer',
    'What is installed now, and what you would like to do about it.');

  H   := GMaintPage.SurfaceHeight;
  Rad := ScaleY(17);

  Y := H - Rad;
  GMaintRemove := TNewRadioButton.Create(GMaintPage);
  GMaintRemove.Parent  := GMaintPage.Surface;
  GMaintRemove.Left    := 0;
  GMaintRemove.Top     := Y;
  GMaintRemove.Width   := GMaintPage.SurfaceWidth;
  GMaintRemove.Height  := Rad;
  GMaintRemove.Caption := 'Remove Satpuda Core from this computer';

  { Built first so it can be measured, then lifted into place. }
  GMaintRepairNote := TNewStaticText.Create(GMaintPage);
  GMaintRepairNote.Parent   := GMaintPage.Surface;
  GMaintRepairNote.Left     := ScaleX(16);
  GMaintRepairNote.Width    := GMaintPage.SurfaceWidth - ScaleX(16);
  GMaintRepairNote.AutoSize := False;
  GMaintRepairNote.WordWrap := True;
  GMaintRepairNote.Caption  := 'Downloads a fresh copy of the program and puts it back over the ' +
                               'damaged one. Your bills, stock and settings are NOT touched.';
  GMaintRepairNote.AdjustHeight;
  Y := Y - ScaleY(8) - GMaintRepairNote.Height;
  GMaintRepairNote.Top := Y;

  Y := Y - ScaleY(2) - Rad;
  GMaintRepair := TNewRadioButton.Create(GMaintPage);
  GMaintRepair.Parent  := GMaintPage.Surface;
  GMaintRepair.Left    := 0;
  GMaintRepair.Top     := Y;
  GMaintRepair.Width   := GMaintPage.SurfaceWidth;
  GMaintRepair.Height  := Rad;
  GMaintRepair.Caption := 'Repair / Reinstall';

  Y := Y - ScaleY(4) - Rad;
  GMaintUpdate := TNewRadioButton.Create(GMaintPage);
  GMaintUpdate.Parent  := GMaintPage.Surface;
  GMaintUpdate.Left    := 0;
  GMaintUpdate.Top     := Y;
  GMaintUpdate.Width   := GMaintPage.SurfaceWidth;
  GMaintUpdate.Height  := Rad;
  GMaintUpdate.Caption := 'Update to {#MyReleaseTag}';

  GMaintPrompt := TNewStaticText.Create(GMaintPage);
  GMaintPrompt.Parent     := GMaintPage.Surface;
  GMaintPrompt.Left       := 0;
  GMaintPrompt.Width      := GMaintPage.SurfaceWidth;
  GMaintPrompt.AutoSize   := False;
  GMaintPrompt.WordWrap   := True;
  GMaintPrompt.Font.Style := [fsBold];
  GMaintPrompt.Caption    := 'What would you like to do?';
  GMaintPrompt.AdjustHeight;
  Y := Y - ScaleY(10) - GMaintPrompt.Height;
  GMaintPrompt.Top := Y;

  Y := Y - ScaleY(10) - ScaleY(2);
  Sep := TBevel.Create(GMaintPage);
  Sep.Parent := GMaintPage.Surface;
  Sep.Left   := 0;
  Sep.Top    := Y;
  Sep.Width  := GMaintPage.SurfaceWidth;
  Sep.Height := ScaleY(2);

  { Whatever is left, and never a negative height: a machine that somehow left
    no room would otherwise raise rather than simply show less. }
  Y := Y - ScaleY(10);
  if Y < ScaleY(20) then
    Y := ScaleY(20);

  GMaintNote := TNewStaticText.Create(GMaintPage);
  GMaintNote.Parent   := GMaintPage.Surface;
  GMaintNote.Left     := 0;
  GMaintNote.Top      := 0;
  GMaintNote.Width    := GMaintPage.SurfaceWidth;
  GMaintNote.AutoSize := False;
  GMaintNote.WordWrap := True;
  GMaintNote.Height   := Y;
  GMaintNote.Caption  := '';
end;

{ Fill the page in. Done from CurPageChanged rather than at creation time, so
  the wizard is already on screen before anything slow happens. }
procedure UpdateMaintenancePage;
var
  S, Addr, Info: String;
begin
  { Does this computer still have the retired server address saved? Asked
    read-only - nothing is written until the install itself - and said on this
    page because this is where the shopkeeper chooses. It matters most in the
    case the version comparison is blind to: a shop ALREADY on the newest
    version, where the page would otherwise say "nothing to do" while the PC is
    still calling a box that is about to go dark. Repair does the whole job. }
  { Which release to offer comes from GitHub (one call, four-second ceiling),
    so ask before the page is filled in. Offline it falls back to this build. }
  ResolveTargetTag;
  ShowResolvedVersionInTitle;
  GMaintUpdate.Caption := 'Update to ' + TargetTag;

  Addr := '';
  if MigrateApiBaseHere(True, Info) = API_MIG_DONE then
  begin
    Log('maintenance page: this computer still saves the retired address ' + Info);
    Addr := #13#10#13#10 +
            'This computer is still set to the old server address. Installing, updating or ' +
            'repairing from here corrects it.';
  end;

  if not GHaveInstall then
  begin
    { "Nothing is installed" said in words. The old wizard said nothing at all,
      which is indistinguishable from a version it failed to read. }
    GMaintNote.Caption :=
      'Satpuda Core is not installed on this computer yet.' + #13#10#13#10 +
      'This installer will download and install version ' + Copy(TargetTag, 2, Length(TargetTag)) + '. ' +
      'It is about 122 MB and comes down over sixteen connections at once, so ' +
      'on a normal shop line it takes a minute or two.' + Addr;
    GMaintPrompt.Visible     := False;
    GMaintUpdate.Visible     := False;
    GMaintRepair.Visible     := False;
    GMaintRepairNote.Visible := False;
    GMaintRemove.Visible     := False;
    Exit;
  end;

  S := 'Satpuda Core is already installed on this computer.' + #13#10#13#10 +
       '      Version:  ' + InstalledVersionText;
  if GInstalledWhen <> '' then
    S := S + '        (installed ' + GInstalledWhen + ')';
  S := S + #13#10 + '      Folder:   ' + GInstalledPath + #13#10#13#10;

  if UpdateIsAvailable then
    S := S + 'A newer version is available: ' + TargetTag + '.'
  else if (GInstalledTag <> '') and (CompareTags(TargetTag, GInstalledTag) = 0) then
    S := S + 'This is the newest version.'
  else if GInstalledTag = '' then
    S := S + 'The version could not be read, so it cannot be compared. Repair / ' +
             'Reinstall will put ' + TargetTag + ' in place.'
  else
    S := S + 'What is installed is newer than the newest release found (' + TargetTag + ').';

  GMaintNote.Caption       := S + Addr;
  GMaintPrompt.Visible     := True;
  GMaintUpdate.Visible     := UpdateIsAvailable;
  GMaintUpdate.Enabled     := UpdateIsAvailable;
  GMaintRepair.Visible     := True;
  GMaintRepairNote.Visible := True;
  GMaintRemove.Visible     := True;

  if UpdateIsAvailable then
    GMaintUpdate.Checked := True
  else
    GMaintRepair.Checked := True;

end;

{ ======================================================================== }
{  The install itself                                                       }
{ ======================================================================== }

{ Returns '' on success, or the sentence to show the user and abort with.
  Runs from PrepareToInstall, whose documented contract is exactly that:
  a non-empty result aborts the install cleanly and displays the text. That
  avoids relying on Abort/exceptions to unwind a half-started install. }
function DoFetch: String;
var
  Params, Staging: String;
  RC: Integer;
begin
  Result := '';
  ExtractTemporaryFile('satpuda_fetch.ps1');

  GCancelFile := ExpandConstant('{tmp}\satpuda_cancel.flag');
  DeleteFile(GCancelFile);
  ForceDirectories(CacheDir);

  GWorkerOK := False;
  GErrCode  := '';
  GErrMsg   := '';
  GWarnings := '';
  GCancelRequested := False;
  GStopping := False;
  GNoCancel := False;
  GWorkerPid := 0;
  GPayloadTag := '';

  GStatusText := 'Getting Satpuda Core ready...';

  { Normally already decided on the maintenance page; a run that skipped it
    (silent install) decides here. }
  ResolveTargetTag;
  ShowResolvedVersionInTitle;

  Params :=
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
      Q(ExpandConstant('{tmp}\satpuda_fetch.ps1')) +
    ' -AppRoot '    + Q(ExpandConstant('{app}')) +
    ' -CacheDir '   + Q(CacheDir) +
    ' -CancelFile ' + Q(GCancelFile) +
    ' -Repo '       + Q('{#MyRepo}') +
    ' -Tag '        + Q(TargetTag) +
    ' -AssetName '  + Q('{#MyAssetName}') +
    ' -DirectUrl '  + Q(TargetDirectUrl) +
    ' -SentinelExe ' + Q('{#MyAppExeName}') +
    { 16 connections instead of 1. Measured on the owner's own line against the
      real 121.9 MB asset: 0.19 MB/s on one connection, 1.94 MB/s on sixteen.
      /Segments=4 on the command line for a shop whose router cannot cope. }
    ' -Segments '   + IntToStr(SegmentCount) +
    ' -LogFile '    + Q(ExpandConstant('{localappdata}\SatpudaCore\install.log'));

  { A repair exists because something on this PC is damaged, so the worker is
    told not to trust a cached zip it cannot prove is good. }
  if GMaintAction = 'repair' then
    Params := Params + ' -Repair';

  GWorkerRunning := True;
  GProgressPage.SetText(GStatusText, '');
  GProgressPage.SetProgress(0, 100);
  GProgressPage.Show;

  { The page's own Stop button is the one that actually works; the wizard's
    Cancel button and the window X are repaired as a courtesy. See the long
    note above RequestStop. }
  if GStopButton <> nil then
  begin
    GStopButton.Caption := 'Stop';
    GStopButton.Enabled := True;
    GStopButton.Visible := True;
  end;
  RepairWizardCancelAffordances;

  try
    { ExecAndLogOutput streams the worker's stdout back through WorkerLog as
      each line arrives, and Inno pumps the message queue every 50 ms while it
      waits (HandleProcessWait in Setup.InstFunc.pas), so the bar moves and the
      Stop button stays live while all the heavy lifting happens elsewhere. }
    if not ExecAndLogOutput(PSExePath, Params, ExpandConstant('{tmp}'),
                            SW_HIDE_, ewWaitUntilTerminated, RC, @WorkerLog) then
    begin
      Result := 'Windows would not start PowerShell, so Satpuda Core could not be downloaded.' + #13#10#13#10 +
                'Please tell Roshan and mention this message.';
      Exit;
    end;
  finally
    { Inno warns that a progress page which is never hidden leaves the wizard
      stuck forever, so this must happen on every path out. }
    if GStopButton <> nil then GStopButton.Visible := False;
    GProgressPage.Hide;
    GWorkerRunning := False;
    DeleteFile(GCancelFile);
  end;

  if (RC = 3) or GCancelRequested then
  begin
    { A worker that had to be killed cannot tidy up after itself, so the
      staging folder is removed from here. It is never a valid install and
      leaving one behind is what makes a broken install look finished. }
    Staging := ExpandConstant('{app}\app.staging');
    if DirExists(Staging) then
    begin
      Log('removing staging folder left by a stopped worker');
      DelTree(Staging, True, True, True);
    end;
    if DirExists(ExpandConstant('{app}')) and
       (not DirExists(ExpandConstant('{app}\app'))) then
      RemoveDir(ExpandConstant('{app}'));   { only succeeds when it is empty }

    Result := 'Installation stopped. Nothing on this computer was changed.' + #13#10#13#10 +
              'What has already been downloaded is kept, so running this installer again will carry on from there.';
    Exit;
  end;

  if (RC <> 0) or (not GWorkerOK) then
  begin
    Result := FriendlyError(GErrCode, GErrMsg);
    Exit;
  end;

  if GWarnings <> '' then
    Log('[fetch warnings] ' + GWarnings);
end;

{ Everything that can stop the install happens here: Inno's contract is that a
  non-empty result aborts cleanly and shows the text, with nothing installed. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  FreeMB, TotalMB: Cardinal;
  Drive: String;
begin
  Result := '';

  if not FileExists(PSExePath) then
  begin
    Result := 'Windows PowerShell was not found on this computer, so Satpuda Core cannot be downloaded.' + #13#10#13#10 +
              'Please tell Roshan - this PC needs Windows PowerShell turned on.';
    Exit;
  end;

  Drive := ExtractFileDrive(ExpandConstant('{app}'));
  if Drive <> '' then
    if GetSpaceOnDisk(AddBackslash(Drive), True, FreeMB, TotalMB) then
      if FreeMB < 2048 then
      begin
        Result := 'There is not enough free space on drive ' + Drive + '.' + #13#10#13#10 +
                  'Satpuda Core needs about 2 GB free to install. Only ' + IntToStr(FreeMB) +
                  ' MB is free. Please delete some files and try again.';
        Exit;
      end;

  { A running copy keeps its own folder locked. The swap survives that on its
    own (it renames the folder rather than writing into it), so this is only a
    courtesy - it stops the program being replaced under the shopkeeper's hands.
    Skipped entirely when silent: there would be nobody to click Retry, and the
    loop would hang an unattended install forever. }
  if not WizardSilent then
    if not WaitForAppClosed(
             'Satpuda Core is currently open.' + #13#10#13#10 +
             'Please close it, then click Retry.',
             'The Satpuda Core window is closed, but its data engine is still running' + #13#10 +
                '(it takes the closing backup for a few seconds after the window goes).' + #13#10#13#10 +
                'Yes - stop the engine now and carry on' + #13#10 +
                'No - wait a few seconds and check again' + #13#10 +
                'Cancel - stop the installation') then
    begin
      Result := 'Satpuda Core was left open, so the installation was stopped. Nothing was changed.';
      Exit;
    end;

  Result := DoFetch;
end;

{ ======================================================================== }
{  Wizard events                                                            }
{ ======================================================================== }

function InitializeSetup: Boolean;
begin
  Result := True;
  { Before DefaultDirName is expanded, so an existing install is reinstalled
    where it already lives. }
  DetectExistingInstall;
end;

procedure InitializeWizard;
var
  Lbl: TNewStaticText;
begin
  { A silent or scripted install answers on the command line instead:
      SatpudaCoreInstaller.exe /VERYSILENT /StoreName="Roshan Medical"
    An unusable name there is dropped rather than argued with - the app will
    ask the same question on its first launch. }
  GStoreName := CleanStoreName(ExpandConstant('{param:StoreName|}'));
  if StoreNameProblem(GStoreName) <> '' then
    GStoreName := '';

  { ONLINE, unless an administrator says otherwise - on the command line too.
    /Mode=offline on its own is now ignored. Leaving it open would have made the
    password on the wizard page decoration: the switch is written down in
    README.md, and anybody who can read the README can read the wizard. So the
    command line has to prove the same thing the page does:

      /VERYSILENT /StoreName="Roshan Medical" /Mode=offline
                  /AdminUser=... /AdminPass="..."

    The credentials are compared and thrown away, exactly as on the page. They
    are visible in this machine's process list while Setup runs, which is a real
    cost and the reason the page - where nothing is typed on a command line - is
    the way this is meant to be done. }
  GSyncMode := 'online';
  if LowerCase(Trim(ExpandConstant('{param:Mode|}'))) = 'offline' then
  begin
    if AdminCredentialsOK(ExpandConstant('{param:AdminUser|}'),
                          ExpandConstant('{param:AdminPass|}')) then
    begin
      GAdminUnlocked := True;
      GSyncMode      := 'offline';
      Log('/Mode=offline accepted: administrator credentials were supplied');
    end
    else
      Log('/Mode=offline IGNORED: it needs /AdminUser and /AdminPass; ' +
          'this install will be Online');
  end;

  if GHaveInstall then
    GMaintAction := 'repair'
  else
    GMaintAction := 'fresh';

  CreateMaintenancePage;
  CreateSetupQuestionsPage;

  { Created here rather than inside PrepareToInstall: Inno's own documentation
    asks for wizard pages to be made in InitializeWizard, and it lets the Stop
    button be built once, on a surface that certainly exists. }
  GProgressPage := CreateOutputProgressPage('Installing {#MyAppName}',
                     'The program is being downloaded and set up. This takes a while the first time.');

  { Anchored to the bottom of the surface, well clear of the two message labels
    and the progress bar that Inno puts at the top of this page. The width is
    the width the caption actually needs at this font, and the sentence beside
    it starts where the button really ends rather than at a hard-coded 132. }
  GStopButton := TNewButton.Create(GProgressPage);
  GStopButton.Parent  := GProgressPage.Surface;
  GStopButton.Caption := 'Stop';
  GStopButton.Left    := 0;
  GStopButton.Height  := ScaleY(25);
  GStopButton.Top     := GProgressPage.SurfaceHeight - GStopButton.Height;
  GStopButton.Width   := WizardForm.CalculateButtonWidth([GStopButton.Caption]);
  GStopButton.Visible := False;
  GStopButton.OnClick := @StopButtonClick;

  Lbl := TNewStaticText.Create(GProgressPage);
  Lbl.Parent   := GProgressPage.Surface;
  Lbl.Left     := GStopButton.Width + ScaleX(12);
  Lbl.Width    := GProgressPage.SurfaceWidth - GStopButton.Width - ScaleX(12);
  Lbl.AutoSize := False;
  Lbl.WordWrap := True;
  Lbl.Caption  := 'You can stop at any time until the very last step. Nothing on this ' +
                  'computer is changed until then, and the download is kept.';
  { Measured, then sat on the same baseline as the button. A fixed ScaleY(26)
    was two lines at 100% and not quite two at 150%. }
  Lbl.AdjustHeight;
  Lbl.Top := GProgressPage.SurfaceHeight - Lbl.Height;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;

  { The maintenance page is never skipped: when nothing is installed it is what
    says so, which is the whole point of it. }

  { An upgrade or a repair on a PC that already has a shop: the app would
    ignore the answer anyway (it refuses to provision over an existing till),
    so asking for it again would only be noise. ForbidStoreChoice is the same
    test WriteProvisionFile refuses to write on, so the page and the handoff
    cannot disagree about whether this PC is already a shop. }
  if (GSetupPage <> nil) and (PageID = GSetupPage.ID) then
    Result := ForbidStoreChoice or GHaveInstall;

  { Update and repair go back into the folder the program is already in.
    Offering to move it there would only create a second copy. }
  if PageID = wpSelectDir then
    Result := GHaveInstall;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Problem, Uninst: String;
  RC: Integer;
begin
  Result := True;

  if (GMaintPage <> nil) and (CurPageID = GMaintPage.ID) then
  begin
    if not GHaveInstall then
    begin
      GMaintAction := 'fresh';
      Exit;
    end;

    if GMaintRemove.Checked then
    begin
      Uninst := UninstallerCommand;
      if Uninst = '' then
      begin
        MsgBox('Satpuda Core cannot be removed from here, because Windows has no record of ' +
               'how it was installed.' + #13#10#13#10 +
               'Choose Repair / Reinstall instead. That puts a complete, working copy back, ' +
               'and it can then be removed normally from Settings > Apps.',
               mbError, MB_OK);
        Result := False;
        Exit;
      end;
      Log('launching uninstaller: ' + Uninst);
      if not Exec(Uninst, '', '', SW_SHOWNORMAL, ewNoWait, RC) then
        { GMaintAction is left alone on this path on purpose: the wizard stays
          open on the maintenance page, and CancelButtonClick must keep asking
          for confirmation the way it normally would. }
        MsgBox('The uninstaller could not be started.', mbError, MB_OK)
      else
      begin
        { Hand over and get out of the way. WizardForm.Close is Inno's own exit
          path, so the temporary folder is cleaned up properly, and
          CancelButtonClick suppresses the "exit Setup?" prompt because this
          close was asked for. }
        GMaintAction := 'remove';
        WizardForm.Close;
      end;
      Result := False;
      Exit;
    end;

    if GMaintUpdate.Visible and GMaintUpdate.Checked then
      GMaintAction := 'update'
    else
      GMaintAction := 'repair';
    Exit;
  end;

  if (GSetupPage = nil) or (CurPageID <> GSetupPage.ID) then
    Exit;

  { A password is sitting in the Administrator row: Next means "unlock", not
    "carry on". Without this a wrong password would be silently swallowed by the
    wizard moving to the next page, and the owner - the only person who ever
    opens this row - would be told nothing.

    The test is the PASSWORD box, not either box, and that is deliberate: a
    refusal clears the password, so pressing Next a second time carries on with
    the install instead of trapping whoever opened the row by accident on a page
    he cannot leave. }
  if GAdminOpen and not GAdminUnlocked and (GAdminPassEdit.Text <> '') then
  begin
    TryAdminUnlock;
    Result := False;
    Exit;
  end;

  Problem := StoreNameProblem(GNameEdit.Text);
  if Problem <> '' then
  begin
    MsgBox(Problem, mbError, MB_OK);
    WizardForm.ActiveControl := GNameEdit;
    Result := False;
    Exit;
  end;

  GStoreName := CleanStoreName(GNameEdit.Text);

  { THE MODE. Offline is reachable only through the radio, the radio exists
    only when the page has been unlocked, and both are checked here rather than
    one - an unlocked page whose radios were somehow left hidden must still come
    out Online. WriteProvisionFile pins it a third time. }
  if GAdminUnlocked and GOfflineRadio.Visible and GOfflineRadio.Checked then
    GSyncMode := 'offline'
  else
    GSyncMode := 'online';

  { Show the cleaned name back before moving on, so the name he approves is
    the one the app will use. }
  GNameEdit.Text := GStoreName;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Shown, Info: String;
  Code, Fixed, Failed: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    { The payload's own tag when the worker reported one, otherwise the release
      this run installed. This is what the wizard and Programs and Features
      both show from now on. }
    if GPayloadTag <> '' then
      Shown := GPayloadTag
    else
      Shown := TargetTag;

    RegWriteStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'Version',     '{#MyAppVersion}');
    RegWriteStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'Tag',         Shown);
    RegWriteStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'InstallPath', ExpandConstant('{app}'));

    { Programs and Features. Inno has already written DisplayVersion from
      AppVersion; overwrite it with the version of the program that actually
      landed, and make sure the icon and location are filled in so the entry
      never looks half-written. HKA follows the install mode, so a per-user
      install writes HKCU and /ALLUSERS writes HKLM. }
    if Copy(Shown, 1, 1) = 'v' then
      Shown := Copy(Shown, 2, Length(Shown));
    RegWriteStringValue(HKA, APP_UNINST_KEY, 'DisplayVersion',  Shown);
    RegWriteStringValue(HKA, APP_UNINST_KEY, 'DisplayIcon',     ExpandConstant('{app}\satpuda.ico'));
    RegWriteStringValue(HKA, APP_UNINST_KEY, 'InstallLocation', ExpandConstant('{app}'));
    RegWriteStringValue(HKA, APP_UNINST_KEY, 'Publisher',       '{#MyAppPublisher}');

    { The two answers, handed to the app. Written only after the download has
      succeeded: an install that failed leaves no file to act on. }
    WriteProvisionFile;

    { ---- The saved server address -------------------------------------
      Here, and only here, because ssPostInstall is the ONE step that a first
      install, an update and a repair all pass through - the maintenance page
      sends all three down the same road (NextButtonClick sets GMaintAction and
      then lets the wizard carry on), and Remove never reaches this procedure
      at all. It is also after the payload has landed, so the program that will
      read this file is already the new one.

      A computer with nothing saved, or one that has deliberately been pinned
      to the tunnel or to a LAN address, comes out of here untouched. See the
      long note above MigrateApiBaseFile for what is and is not rewritten. }
    Fixed  := 0;
    Failed := 0;
    GApiBaseFailPath := '';

    Code := MigrateApiBaseHere(False, Info);
    if Code = API_MIG_DONE then
    begin
      Fixed := 1;
      Log('server address corrected: ' + Info);
    end
    else if Code = API_MIG_FAILED then
    begin
      Failed := 1;
      GApiBaseFailPath := ApiPrefsPathIn(AppDataDir);
      Log('server address NOT corrected: ' + Info);
    end
    else
      Log('server address unchanged: ' + Info);

    if IsAdminInstallMode then
      MigrateApiBaseAllProfiles(Fixed, Failed);

    Log('server address: ' + IntToStr(Fixed) + ' corrected, ' +
        IntToStr(Failed) + ' could not be written');

    { An install that worked is NOT reported as a failure over this. The
      program corrects the same file itself the next time it starts
      (migrate_api_base in core\server_api.py), so the shop is not stranded.
      But it is not passed over in silence either - it is said on the last
      page, where a shortcut that could not be created is already said. }
    if Failed > 0 then
      GApiBaseProblem :=
        'Satpuda Core is installed, but the server address saved on this computer could' + #13#10 +
        'not be updated:' + #13#10#13#10 +
        GApiBaseFailPath + #13#10#13#10 +
        'The program corrects this itself the next time it starts, so there is nothing to' + #13#10 +
        'do now. If it keeps saying it cannot reach the server, tell Roshan and mention' + #13#10 +
        'this message.';

    CreateStartMenuShortcut;
    if GStartMenuLnk <> '' then
      RegWriteStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'StartMenuLnk', GStartMenuLnk);

    if WizardIsTaskSelected('desktopicon') then
    begin
      CreateDesktopShortcut;
      if GDesktopLnk <> '' then
        RegWriteStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'DesktopLnk', GDesktopLnk);
    end;

    { "Use the Start Menu instead" is only good advice if there IS a Start Menu
      entry. Both can fail together - Controlled Folder Access and a roaming
      profile whose whole shell-folder set is offline hit them at the same time
      - and telling him to look somewhere empty wastes his afternoon. Give him
      the real path to the .exe in that case. }
    if (GDesktopProblem <> '') and (GStartMenuLnk = '') then
      GDesktopProblem :=
        'Satpuda Core is installed, but no shortcut could be created - Windows would not' + #13#10 +
        'let the installer write to the Desktop or the Start Menu.' + #13#10#13#10 +
        'Start it by opening this file:' + #13#10 +
        ExpandConstant('{app}\app\{#MyAppExeName}');

    if WizardIsTaskSelected('updatecheck') then
      CreateStartupUpdateCheck;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (GMaintPage <> nil) and (CurPageID = GMaintPage.ID) then
    UpdateMaintenancePage;

  { The shop name is the first thing to type on that page, so put the cursor
    in it rather than on the Next button - unless the Administrator row is
    already open, in which case that is what the person came back for. }
  if (GSetupPage <> nil) and (CurPageID = GSetupPage.ID) then
  begin
    if GAdminOpen and not GAdminUnlocked then
      WizardForm.ActiveControl := GAdminUserEdit
    else
      WizardForm.ActiveControl := GNameEdit;
  end;

  { Say what version is now on the computer, and tell the user about a missing
    desktop icon where he cannot miss it rather than failing the install over
    it. Cleared as it is shown so it can never be appended twice. }
  if CurPageID = wpFinished then
  begin
    if GPayloadTag <> '' then
      WizardForm.FinishedLabel.Caption :=
        WizardForm.FinishedLabel.Caption + #13#10#13#10 +
        'Installed version: ' + GPayloadTag;
    if GDesktopProblem <> '' then
    begin
      WizardForm.FinishedLabel.Caption :=
        WizardForm.FinishedLabel.Caption + #13#10#13#10 + GDesktopProblem;
      GDesktopProblem := '';
    end;

    { The saved server address could not be rewritten. Same treatment as the
      shortcut above: the install succeeded, this is said rather than hidden,
      and it is cleared as it is shown so it can never appear twice. }
    if GApiBaseProblem <> '' then
    begin
      WizardForm.FinishedLabel.Caption :=
        WizardForm.FinishedLabel.Caption + #13#10#13#10 + GApiBaseProblem;
      GApiBaseProblem := '';
    end;
  end;
end;

{ ======================================================================== }
{  Uninstall                                                                }
{ ======================================================================== }

{ Refuse to start while the program is open. Without this the uninstaller
  cheerfully DelTree's a folder whose .exe and .pyd files are mapped into a
  running process: most of it goes, the locked files stay, and the shopkeeper
  is left with no Programs entry, no shortcuts, and a few hundred MB of orphan
  that nothing will ever clean up. Stopping before anything is deleted keeps
  the install whole and re-runnable. }
function InitializeUninstall: Boolean;
begin
  Result := True;
  if UninstallSilent then Exit;
  if not WaitForAppClosed(
           'Satpuda Core is still open.' + #13#10#13#10 +
           'Close it completely, then click Retry to carry on removing it.',
           'The Satpuda Core window is closed, but its data engine is still running' + #13#10 +
                '(it takes the closing backup for a few seconds after the window goes).' + #13#10#13#10 +
                'Yes - stop the engine now and carry on' + #13#10 +
                'No - wait a few seconds and check again' + #13#10 +
                'Cancel - leave Satpuda Core installed') then
    Result := False;
end;

{ Deliberately a Yes/No question and not a custom form with a real checkbox.
  A checkbox needs CreateCustomForm, whose behaviour inside the UNINSTALLER is
  not documented, and this script could not be compile-tested. A prompt that
  certainly works and defaults to keeping the data is worth more than a prettier
  one that might break the uninstaller. The default button is No, so pressing
  Enter or Space keeps the shop's books. }
function AskRemoveData: Boolean;
begin
  Result := False;

  { Silent uninstall: only ever remove data when explicitly told to. }
  if UninstallSilent then
  begin
    Result := (Pos('/REMOVEDATA', UpperCase(GetCmdTail)) > 0);
    Exit;
  end;

  Result := (MsgBox(
    'Do you also want to DELETE all your Satpuda Core shop data?' + #13#10#13#10 +
    'This is your bills, stock, customers and settings.' + #13#10#13#10 +
    'Choose No to keep it. The data stays on this computer and Satpuda Core will ' +
    'pick it up again if you reinstall later. This is what almost everyone wants.' + #13#10#13#10 +
    'Choose Yes only if you want it gone permanently. It cannot be undone.',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  RemoveData: Boolean;
  S, AppDir, DataDir: String;
  FR: TFindRec;
begin
  if CurUninstallStep <> usUninstall then Exit;

  RemoveData := AskRemoveData;
  AppDir := ExpandConstant('{app}');

  { Shortcuts, at the exact paths they were created at - they may not be in the
    folder Inno would guess, because of desktop redirection. }
  if RegQueryStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'DesktopLnk', S)   then DeleteFile(S);
  if RegQueryStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'StartupLnk', S)   then DeleteFile(S);
  if RegQueryStringValue(HKCU, 'Software\Satpuda\SatpudaCore', 'StartMenuLnk', S) then
  begin
    DeleteFile(S);
    DeleteFile(AddBackslash(ExtractFileDir(S)) + 'Check for {#MyAppName} updates.lnk');
    RemoveDir(ExtractFileDir(S));
  end;

  { The downloaded tree: Inno never tracked it, so it must go explicitly. }
  DelTree(AddBackslash(AppDir) + 'app',         True, True, True);
  DelTree(AddBackslash(AppDir) + 'app.staging', True, True, True);

  { Any app.old-* left behind by an interrupted upgrade. }
  if FindFirst(AddBackslash(AppDir) + 'app.old-*', FR) then
  try
    repeat
      if (FR.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        DelTree(AddBackslash(AppDir) + FR.Name, True, True, True);
    until not FindNext(FR);
  finally
    FindClose(FR);
  end;

  { The installer's own state folder: the download cache, the install log and
    the update-check stamp. None of this is shop data, so it always goes. The
    shop's actual books live in %LOCALAPPDATA%\VeterinaryApp and are only
    touched below, if the user asked for it. }
  DelTree(ExpandConstant('{localappdata}\SatpudaCore'), True, True, True);

  { The all-users copy of the two answers, if this was an /ALLUSERS install.
    It is only a shop name and a mode, but leaving it behind would hand the
    next person to install on this PC somebody else's shop name. Best effort:
    a standard user uninstalling an administrator's install cannot delete it,
    and the app ignores a stale one anyway. }
  DeleteFile(ExpandConstant('{commonappdata}\SatpudaCore\provision.json'));
  RemoveDir(ExpandConstant('{commonappdata}\SatpudaCore'));

  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Satpuda\SatpudaCore');

  { One source of truth for the shop data folder. This used to expand the
    path a second time here; a build that changed AppDataDir would then have
    an uninstaller still pointed at the old folder. }
  DataDir := AppDataDir;
  if RemoveData then
  begin
    Log('user asked to remove shop data at ' + DataDir);
    DelTree(DataDir, True, True, True);
  end
  else
    Log('shop data kept at ' + DataDir);
end;
