; PictureHouse installer for Windows (Inno Setup 6).
;
; Built by windows/build_windows.py, which first builds the app into
; dist\windows\PictureHouse (PictureHouse.exe + server\) and passes:
;   /DAppVersion=0.1.0 /DSourceDir=<that folder> /DOutputDir=<dist\windows>
;
; Installs for the current user only (no admin prompt), into
; %LOCALAPPDATA%\Programs\PictureHouse. The library, settings and artwork live
; in %LOCALAPPDATA%\PictureHouse and are kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\windows\PictureHouse"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\windows"
#endif

[Setup]
AppId={{1E872EEF-4A86-4A74-ABBD-C90BCCF3987A}
AppName=PictureHouse
AppVersion={#AppVersion}
AppVerName=PictureHouse {#AppVersion}
AppPublisher=PictureHouse
AppPublisherURL=https://github.com/LukeShoe199/PictureHouse
AppSupportURL=https://github.com/LukeShoe199/PictureHouse/issues
DefaultDirName={autopf}\PictureHouse
DefaultGroupName=PictureHouse
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=PictureHouse-{#AppVersion}-setup
SetupIconFile=..\desktop\icons\icon.ico
UninstallDisplayIcon={app}\PictureHouse.exe
UninstallDisplayName=PictureHouse
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Upgrading while it runs: close it (and its server) first.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "ffmpeg"; Description: "Install &FFmpeg with winget (needed to convert and stream most video and music; a window opens and asks you to agree to its terms)"; GroupDescription: "FFmpeg was not found on this PC:"; Check: NeedsFFmpeg

[Files]
Source: "{#SourceDir}\PictureHouse.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\server\*"; DestDir: "{app}\server"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Old server files from a previous version (libraries renamed between builds).
Type: filesandordirs; Name: "{app}\server"

[Icons]
Name: "{autoprograms}\PictureHouse"; Filename: "{app}\PictureHouse.exe"
Name: "{autodesktop}\PictureHouse"; Filename: "{app}\PictureHouse.exe"; Tasks: desktopicon

[Run]
; Visible console: winget asks the user to agree to the source and package terms.
Filename: "winget.exe"; Parameters: "install --id Gyan.FFmpeg --exact --source winget"; StatusMsg: "Installing FFmpeg with winget..."; Flags: waituntilterminated; Tasks: ffmpeg
Filename: "{app}\PictureHouse.exe"; Description: "Start PictureHouse now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM PictureHouse.exe"; Flags: runhidden; RunOnceId: "StopApp"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM picturehouse-server.exe"; Flags: runhidden; RunOnceId: "StopServer"

[Code]
const
  WebView2Client = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2At(Root: Integer; const Key: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0');
end;

{ Microsoft's documented check: machine-wide (32-bit view) or per-user. }
function HasWebView2: Boolean;
begin
  Result := WebView2At(HKLM, 'SOFTWARE\WOW6432Node\' + WebView2Client)
    or WebView2At(HKCU, 'Software\' + WebView2Client);
end;

function FileOnPath(const Name: String): Boolean;
begin
  Result := FileSearch(Name, GetEnv('PATH')) <> '';
end;

{ Mirrors picturehouse.ffmpeg.locate_tool: PATH, then winget's folders. }
function NeedsFFmpeg: Boolean;
var
  FindRec: TFindRec;
  Packages: String;
begin
  Result := not FileOnPath('ffmpeg.exe')
    and not FileExists(ExpandConstant('{localappdata}\Microsoft\WinGet\Links\ffmpeg.exe'));
  if Result then begin
    Packages := ExpandConstant('{localappdata}\Microsoft\WinGet\Packages\');
    if FindFirst(Packages + 'Gyan.FFmpeg*', FindRec) then begin
      FindClose(FindRec);
      Result := False;
    end;
  end;
  if Result and not FileOnPath('winget.exe') then
    Result := False;  { no winget to offer: the app explains what to do }
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if not HasWebView2 then
    MsgBox('PictureHouse''s window needs Microsoft Edge WebView2, which this PC doesn''t seem to have.' + #13#10#13#10 +
      'Setup will continue. If PictureHouse doesn''t open afterwards, install the "Evergreen Bootstrapper" from' + #13#10 +
      'https://developer.microsoft.com/microsoft-edge/webview2/' + #13#10#13#10 +
      'The server still runs: open http://127.0.0.1:9595/ in any browser.', mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then begin
    { "Start when I sign in" (tray menu) adds this; don't leave it pointing nowhere. }
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'PictureHouse');
    if not UninstallSilent then
      MsgBox('PictureHouse is removed. Your library, settings and artwork are kept in' + #13#10 +
      ExpandConstant('{localappdata}\PictureHouse') + #13#10 +
      'Delete that folder too if you won''t reinstall.', mbInformation, MB_OK);
  end;
end;
