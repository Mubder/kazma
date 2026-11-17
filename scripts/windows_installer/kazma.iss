; Kazma's Windows installer (Inno Setup 6), REMAINING 2.7.
;
; It installs Kazma without git or a terminal: the files of the release's
; source archive (kazma-<version>.tar.gz, the archive every release signs),
; laid down by build.py together with the release-install marker
; (.kazma-release.json, kazma_cli/release_tree.py), then setup.cmd, the same
; setup a clone runs, in a window that shows its progress. `kazma update`
; updates the folder from the next release's signed archive.
;
; Built by scripts/windows_installer/build.py (it passes the defines below);
; never edit a define here. Signing is the release workflow's
; (.github/workflows/release.yml, job windows-installer): the installer is
; signed there once a signing account is configured, and is unsigned until
; then.

#ifndef AppVersion
  #error Build with scripts/windows_installer/build.py
#endif

[Setup]
; One id for every version: a new installer replaces the old one in
; Settings > Apps instead of adding a second entry.
AppId={{9C64B6AC-4F23-48DB-B198-5E4896430379}
AppName=Kazma
AppVersion={#AppVersion}
AppVerName=Kazma {#AppVersion}
AppPublisher=Kazma
AppPublisherURL=https://kazma.ai
AppSupportURL=https://github.com/Mubder/kazma/issues
AppUpdatesURL=https://github.com/Mubder/kazma/releases
AppCopyright=MIT License
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Kazma setup
; Kazma keeps its data beside its files (kazma-data) and the person runs it,
; so it goes where a person's programs go, never Program Files.
DefaultDirName={localappdata}\Programs\Kazma
DisableProgramGroupPage=yes
DefaultGroupName=Kazma
; Setup installs system parts (WSL, Docker's engine, the service): it needs
; administrator rights, asked for once, here.
PrivilegesRequired=admin
UsedUserAreasWarning=no
ArchitecturesAllowed=x64compatible arm64
ArchitecturesInstallIn64BitMode=x64compatible arm64
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=kazma-{#AppVersion}-windows-setup
SetupIconFile=kazma.ico
UninstallDisplayIcon={app}\kazma.ico
UninstallDisplayName=Kazma
LicenseFile={#SourceDir}\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Kazma's own files are replaced while its server may run (as git does);
; setup.cmd then holds and restarts it.
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The last file runs setup (AfterInstall): Inno Setup asks NeedRestart before
; ssPostInstall, so setup run from ssPostInstall could never ask for the WSL
; restart (the first VM drill, 2026-11-16: "Need to restart Windows? No").
Source: "kazma.ico"; DestDir: "{app}"; Flags: ignoreversion; AfterInstall: RunKazmaSetup

[Icons]
Name: "{group}\Kazma"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m kazma_cli open"; WorkingDir: "{app}"; IconFilename: "{app}\kazma.ico"; Comment: "Open Kazma in your browser"
Name: "{group}\Update Kazma"; Filename: "{cmd}"; Parameters: "/k """"{app}\.venv\Scripts\python.exe"" -m kazma_cli update"""; WorkingDir: "{app}"; IconFilename: "{app}\kazma.ico"; Comment: "Install the newest signed release"
Name: "{group}\Finish or repair Kazma setup"; Filename: "{app}\setup.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\kazma.ico"; Comment: "Run Kazma's setup again: it continues where it stopped"
Name: "{group}\Kazma documentation"; Filename: "https://kazma.ai/docs/"
Name: "{autodesktop}\Kazma"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m kazma_cli open"; WorkingDir: "{app}"; IconFilename: "{app}\kazma.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Put a Kazma shortcut on the desktop"; Flags: unchecked

[UninstallRun]
; `kazma uninstall` takes off what setup put on (the service, its tasks,
; Kazma's containers and engine, .venv) and keeps the data: kazma-data and
; .env stay in {app} unless the owner removes them.
Filename: "{app}\.venv\Scripts\python.exe"; Parameters: "-m kazma_cli uninstall --yes"; WorkingDir: "{app}"; Flags: waituntilterminated; RunOnceId: "KazmaUninstall"; Check: KazmaEnvironmentExists

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.kazma\update"

[Code]
var
  ModePage: TInputOptionWizardPage;
  RestartAfter: Boolean;

function KazmaEnvironmentExists(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\.venv\Scripts\python.exe'));
end;

procedure InitializeWizard();
begin
  ModePage := CreateInputOptionPage(wpSelectDir,
    'How to install Kazma', 'Express or Full',
    'Express installs the everyday parts: chat, memory, documents, the web pages and Kazma as a service ' +
    'that starts with Windows, with private web search and the memory graph where this computer can run ' +
    'containers. Full installs every part, PostgreSQL among them. Run "Finish or repair Kazma setup" later ' +
    'to add parts.',
    True, False);
  ModePage.Add('Express (recommended)');
  ModePage.Add('Full');
  ModePage.SelectedValueIndex := 0;
end;

function SetupMode(): String;
begin
  if ModePage.SelectedValueIndex = 1 then
    Result := 'full'
  else
    Result := 'express';
end;

procedure RunKazmaSetup();
var
  ResultCode: Integer;
  App, Params: String;
begin
  App := ExpandConstant('{app}');
  // setup.cmd in a window of its own, so its progress shows: uv, Python,
  // Kazma's packages, then `kazma setup install`. -NoElevate: this installer
  // already runs as administrator.
  Params := '/c ""' + App + '\setup.cmd" -Mode ' + SetupMode() + ' -Yes -NoElevate"';
  WizardForm.StatusLabel.Caption := 'Setting Kazma up (a window shows each step)...';
  if not Exec(ExpandConstant('{cmd}'), Params, App, SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    // Suppressible: a silent install (/SUPPRESSMSGBOXES) answers it itself. A
    // plain MsgBox waits for a click nobody can give (the VM drill hung on one).
    SuppressibleMsgBox('Kazma''s setup could not start (' + SysErrorMessage(ResultCode) + '). ' +
      'Run "Finish or repair Kazma setup" from the Start menu.', mbError, MB_OK, IDOK);
    exit;
  end;
  if ResultCode = 3 then
  begin
    // Windows must restart (WSL) before the container parts: setup goes on
    // by itself at the next sign-in (kazma_cli.install EXIT_RESTART).
    RegWriteStringValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\RunOnce',
      'KazmaSetup', '"' + App + '\setup.cmd" -Mode ' + SetupMode() + ' -Yes');
    RestartAfter := True;
  end
  else if ResultCode <> 0 then
    SuppressibleMsgBox('Kazma is installed, but not every part of it yet. Run "Finish or repair Kazma setup" ' +
      'from the Start menu: it continues where it stopped. What happened is in ' + App + '\.kazma\install.log',
      mbInformation, MB_OK, IDOK);
end;

function NeedRestart(): Boolean;
begin
  Result := RestartAfter;
end;
