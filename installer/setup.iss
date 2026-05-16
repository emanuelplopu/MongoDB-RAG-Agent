; =============================================================================
; RecallHub Windows Installer - Inno Setup Script
; =============================================================================
; This script creates the Windows installer package for RecallHub.
; It handles Docker Desktop deployment, service configuration, and tray app launch.
; =============================================================================

#define MyAppName "RecallHub"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "RecallHub"
#define MyAppURL "https://recallhub.local"
#define MyAppExeName "RecallHubTray.exe"

[Setup]
; Basic installer settings
AppId={{8F7E9D4C-3A2B-4E5F-9C8D-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Installation directories
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Output settings
OutputDir=output
OutputBaseFilename=RecallHubSetup-{#MyAppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

; Compression
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; Privileges
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; Appearance
WizardStyle=modern
WizardSizePercent=120,120

; Misc
AllowNoIcons=yes
CloseApplications=yes
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041

; Logging
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checked
Name: "startupicon"; Description: "Start RecallHub when Windows starts"; GroupDescription: "Startup Options"

[Files]
; Tray application
Source: "tray-app\bin\Release\net8.0-windows\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

; PowerShell scripts
Source: "scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs

; Configuration files
Source: "config\*"; DestDir: "{app}\config"; Flags: ignoreversion recursesubdirs

; Docker images (exported tars)
Source: "images\*"; DestDir: "{app}\images"; Flags: ignoreversion recursesubdirs; Check: DirExists(ExpandConstant('{src}\images'))

; Ollama models (large files)
Source: "models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs; Check: FileExists(ExpandConstant('{src}\models\ollama-models.tar'))

; Assets
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs

[Dirs]
Name: "{app}\logs"
Name: "{app}\backups"
Name: "{app}\updates"

[Icons]
; Start Menu entries
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Comment: "Launch RecallHub"
Name: "{group}\Start {#MyAppName}"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\Start-Services.ps1"""; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Stop {#MyAppName}"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\Stop-Services.ps1"""; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\View Logs"; Filename: "{app}\logs"; IconFilename: "{sys}\shell32.dll"; IconIndex: 3; Comment: "Open RecallHub logs folder"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\assets\icon.ico"

; Desktop shortcut (user-scoped)
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

; Startup entry
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
; Post-installation tasks (output captured to log files)
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -Command ""& '{app}\scripts\Configure-Firewall.ps1' *> '{localappdata}\RecallHub\logs\configure-firewall-run.log'""" ; StatusMsg: "Configuring firewall..."; Description: "Configure firewall rules"; Flags: runhidden waituntilterminated
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -Command ""& '{app}\scripts\Start-Services.ps1' -Wait *> '{localappdata}\RecallHub\logs\start-services-run.log'""" ; StatusMsg: "Starting services..."; Description: "Start RecallHub services"; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Registry]
; Protocol handler: recallhub://
Root: HKCU; Subkey: "Software\Classes\recallhub"; ValueType: string; ValueName: ""; ValueData: "URL:RecallHub Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\recallhub"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\recallhub\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\assets\icon.ico"""
Root: HKCU; Subkey: "Software\Classes\recallhub\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """"{app}\{#MyAppExeName}"" ""%1"""

; File association: .rhu (RecallHub Update)
Root: HKCU; Subkey: "Software\Classes\.rhu"; ValueType: string; ValueName: ""; ValueData: "RecallHub.Update"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\RecallHub.Update"; ValueType: string; ValueName: ""; ValueData: "RecallHub Update File"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\RecallHub.Update\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\assets\icon.ico"""
Root: HKCU; Subkey: "Software\Classes\RecallHub.Update\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """"{app}\{#MyAppExeName}"" ""%1"""

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\Uninstall.ps1"" -Force"; Flags: runhidden waituntilterminated

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{localappdata}\RecallHub\window-state.json"
Type: files; Name: "{userdesktop}\{#MyAppName}.lnk"
Type: dirifempty; Name: "{app}"

[Code]
var
  InstallLogDir: String;
  InstallStartTime: String;

// ============================================================================
// Logging & Transcript
// ============================================================================

// Initialize log directory early
procedure InitializeLogDir();
begin
  InstallLogDir := ExpandConstant('{localappdata}\RecallHub\logs');
  ForceDirectories(InstallLogDir);
  InstallStartTime := GetDateTimeString('yyyy-mm-dd_hhnnss', '-', ':');
end;

// Log a message to our custom transcript file
procedure LogTranscript(const Msg: String);
var
  LogFile: String;
  Lines: TStringList;
begin
  LogFile := InstallLogDir + '\install-transcript-' + InstallStartTime + '.log';
  Lines := TStringList.Create;
  try
    if FileExists(LogFile) then
      Lines.LoadFromFile(LogFile);
    Lines.Add('[' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '] ' + Msg);
    Lines.SaveToFile(LogFile);
  finally
    Lines.Free;
  end;
end;

// ============================================================================
// Install Manifest
// ============================================================================

// Create install manifest JSON at end
procedure CreateInstallManifest();
var
  Manifest: TStringList;
  ManifestFile: String;
begin
  ManifestFile := InstallLogDir + '\install-manifest.json';
  Manifest := TStringList.Create;
  try
    Manifest.Add('{');
    Manifest.Add('  "version": "{#MyAppVersion}",');
    Manifest.Add('  "installTime": "' + GetDateTimeString('yyyy-mm-dd"T"hh:nn:ss', '-', ':') + '",');
    Manifest.Add('  "installDir": "' + ExpandConstant('{app}') + '",');
    Manifest.Add('  "logDir": "' + InstallLogDir + '",');
    Manifest.Add('  "components": {');
    Manifest.Add('    "docker": true,');
    Manifest.Add('    "ollama": true,');
    Manifest.Add('    "backend": true,');
    Manifest.Add('    "frontend": true');
    Manifest.Add('  },');
    Manifest.Add('  "transcriptFile": "install-transcript-' + InstallStartTime + '.log"');
    Manifest.Add('}');
    Manifest.SaveToFile(ManifestFile);
    LogTranscript('MANIFEST: Created at ' + ManifestFile);
  finally
    Manifest.Free;
  end;
end;

// ============================================================================
// Exec with Logging
// ============================================================================

// Execute post-install scripts WITH logging (for use in custom code paths)
function ExecWithLogging(const Filename, Params, LogPrefix: String; const Timeout: Integer): Boolean;
var
  ResultCode: Integer;
  StdoutFile: String;
  CmdLine: String;
begin
  StdoutFile := InstallLogDir + '\' + LogPrefix + '-' + InstallStartTime + '.log';
  LogTranscript('EXEC: ' + Filename + ' ' + Params);
  LogTranscript('  Output: ' + StdoutFile);
  LogTranscript('  Timeout: ' + IntToStr(Timeout) + ' ms');

  // Use cmd /c to redirect output to file
  CmdLine := '/C "powershell.exe -ExecutionPolicy Bypass -File "' + Filename + '" ' + Params + ' > "' + StdoutFile + '" 2>&1"';

  Result := Exec('cmd.exe', CmdLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if Result then begin
    LogTranscript('  Exit code: ' + IntToStr(ResultCode));
    if ResultCode <> 0 then begin
      LogTranscript('  WARNING: Script exited with non-zero code');
      Result := False;
    end;
  end else begin
    LogTranscript('  ERROR: Failed to execute script');
  end;
end;

// ============================================================================
// Setup Event Handlers
// ============================================================================

function InitializeSetup(): Boolean;
var
  WinVer: TWindowsVersion;
begin
  Result := True;

  // Initialize logging first
  InitializeLogDir();
  LogTranscript('=== RecallHub Installation Started ===');

  // Check Windows version
  GetWindowsVersionEx(WinVer);
  LogTranscript('Windows build: ' + IntToStr(WinVer.Build));

  if WinVer.Build < 19041 then
  begin
    LogTranscript('ERROR: Windows build too old (requires 19041+)');
    MsgBox('RecallHub requires Windows 10 version 2004 or later (build 19041+).' + #13#10 +
           'Your current build is ' + IntToStr(WinVer.Build) + '.' + #13#10 + #13#10 +
           'Please update Windows and try again.', mbError, MB_OK);
    Result := False;
    Exit;
  end;

  // Check for sufficient disk space (5GB minimum)
  if GetSpaceOnDisk(ExpandConstant('{localappdata}'), True, True) < 5368709120 then
  begin
    LogTranscript('ERROR: Insufficient disk space (requires 5 GB)');
    MsgBox('RecallHub requires at least 5 GB of free disk space.' + #13#10 +
           'Please free up some space and try again.', mbError, MB_OK);
    Result := False;
    Exit;
  end;

  LogTranscript('Install path: ' + ExpandConstant('{localappdata}\RecallHub'));
  LogTranscript('Pre-flight checks passed');
end;

// Track page changes for granular logging
procedure CurPageChanged(CurPageID: Integer);
begin
  case CurPageID of
    wpWelcome: LogTranscript('PAGE: Welcome');
    wpLicense: LogTranscript('PAGE: License');
    wpSelectDir: LogTranscript('PAGE: Select Directory');
    wpReady: LogTranscript('PAGE: Ready to Install');
    wpInstalling: LogTranscript('PAGE: Installing');
    wpFinished: LogTranscript('PAGE: Finished');
  end;
end;

// Track installation steps
procedure CurStepChanged(CurStep: TSetupStep);
begin
  case CurStep of
    ssInstall:
      LogTranscript('STEP: Installation files being copied');
    ssPostInstall:
      begin
        LogTranscript('STEP: Post-installation tasks starting');
      end;
    ssDone:
      begin
        LogTranscript('STEP: Installation completed successfully');
        CreateInstallManifest();
      end;
  end;
end;

function NeedRestart(): Boolean;
begin
  Result := False;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Clean up any remaining files
    DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;

[Messages]
WelcomeLabel1=Welcome to the [name] Setup Wizard
WelcomeLabel2=This will install [name/ver] on your computer.%n%nRecallHub is a local-first RAG (Retrieval-Augmented Generation) application that runs entirely on your machine.%n%nRequirements:%n- Windows 10 version 2004 or later%n- Docker Desktop installed and running%n- 8 GB RAM minimum (16 GB recommended)%n- 5 GB free disk space%n%nThe installation will:%n1. Configure firewall rules%n2. Load Docker images and start services%n3. Launch the RecallHub tray application%n%nThis may take 5-10 minutes depending on your system.
FinishedLabelNoIcons=Setup has finished installing [name] on your computer.%n%nRecallHub is now running!%n%nOpen your browser to: http://localhost:11080
