; =============================================================================
; RecallHub Windows Installer - Inno Setup Script
; =============================================================================
; This script creates the Windows installer package for RecallHub.
; It handles WSL2 setup, distribution import, and service configuration.
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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupicon"; Description: "Start RecallHub when Windows starts"; GroupDescription: "Startup Options"

[Files]
; Tray application
Source: "tray-app\bin\Release\net8.0-windows\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

; PowerShell scripts
Source: "scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs

; Configuration files
Source: "config\*"; DestDir: "{app}\config"; Flags: ignoreversion recursesubdirs

; WSL2 distribution (large file)
Source: "distro\recallhub.tar.gz"; DestDir: "{app}\distro"; Flags: ignoreversion

; Ollama models (large files)
Source: "models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs; Check: FileExists(ExpandConstant('{src}\models\ollama-models.tar'))

; Assets
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs

[Dirs]
Name: "{app}\logs"
Name: "{app}\backups"
Name: "{app}\updates"
Name: "{app}\wsl"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Start {#MyAppName}"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\Start-Services.ps1"""; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Stop {#MyAppName}"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\Stop-Services.ps1"""; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
; Post-installation tasks
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\Install-WSL2.ps1"""; StatusMsg: "Configuring WSL2..."; Flags: runhidden waituntilterminated
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\Import-Distro.ps1"" -DistroPath ""{app}\distro\recallhub.tar.gz"" -InstallPath ""{app}\wsl"" -Force"; StatusMsg: "Importing RecallHub distribution..."; Flags: runhidden waituntilterminated
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\Configure-Firewall.ps1"""; StatusMsg: "Configuring firewall..."; Flags: runhidden waituntilterminated
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\Start-Services.ps1"" -Wait"; StatusMsg: "Starting services..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\Uninstall.ps1"" -Force"; Flags: runhidden waituntilterminated

[UninstallDelete]
Type: filesandordirs; Name: "{app}\wsl"
Type: filesandordirs; Name: "{app}\logs"
Type: dirifempty; Name: "{app}"

[Code]
var
  WSLRestartRequired: Boolean;

function InitializeSetup(): Boolean;
var
  WinVer: TWindowsVersion;
begin
  Result := True;
  WSLRestartRequired := False;
  
  // Check Windows version
  GetWindowsVersionEx(WinVer);
  
  if WinVer.Build < 19041 then
  begin
    MsgBox('RecallHub requires Windows 10 version 2004 or later (build 19041+).' + #13#10 +
           'Your current build is ' + IntToStr(WinVer.Build) + '.' + #13#10 + #13#10 +
           'Please update Windows and try again.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  
  // Check for sufficient disk space (5GB minimum)
  if GetSpaceOnDisk(ExpandConstant('{localappdata}'), True, True) < 5368709120 then
  begin
    MsgBox('RecallHub requires at least 5 GB of free disk space.' + #13#10 +
           'Please free up some space and try again.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Check if restart is needed for WSL2
    if FileExists(ExpandConstant('{localappdata}\RecallHub\install-state.json')) then
    begin
      WSLRestartRequired := True;
    end;
  end;
end;

function NeedRestart(): Boolean;
begin
  Result := WSLRestartRequired;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Clean up any remaining files
    DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;

// Check if a source file exists
function FileExists(FileName: string): Boolean;
begin
  Result := FileExists(FileName);
end;

[Messages]
WelcomeLabel1=Welcome to the [name] Setup Wizard
WelcomeLabel2=This will install [name/ver] on your computer.%n%nRecallHub is a local-first RAG (Retrieval-Augmented Generation) application that runs entirely on your machine.%n%nRequirements:%n- Windows 10 version 2004 or later%n- 8 GB RAM minimum (16 GB recommended)%n- 5 GB free disk space%n%nThe installation will:%n1. Configure Windows Subsystem for Linux 2 (WSL2)%n2. Import the RecallHub environment%n3. Start all services%n%nThis may take 10-15 minutes depending on your system.
FinishedLabelNoIcons=Setup has finished installing [name] on your computer.%n%nRecallHub is now running!%n%nOpen your browser to: http://localhost:11080
