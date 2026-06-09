; ==========================================================================
;  Censr - Inno Setup script
;  Builds an installer from the PyInstaller one-folder output (dist\Censr).
;  Compile:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\censr.iss
;  (build.bat runs this automatically if Inno Setup 6 is installed.)
;
;  NOTE: strings here are ASCII on purpose. Inno reads .iss as ANSI unless the
;  file has a UTF-8 BOM; the installer WIZARD is shown in Russian via the
;  Russian language file below. To localize the custom strings too, re-save
;  this file as "UTF-8 with BOM" and translate them.
; ==========================================================================

#define MyAppName "Censr"
#define MyAppVersion "1.4.0"
#define MyAppExeName "Censr.exe"
#define DistDir "..\dist\Censr"

[Setup]
AppId={{8F3C2A14-9B7E-4D62-AE51-3D7C0E5A91B4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Censr
DefaultDirName={autopf}\Censr
DefaultGroupName=Censr
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=Censr-Setup-{#MyAppVersion}
SetupIconFile=..\censr.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Whole PyInstaller folder, including models\ and ffmpeg\ added by build.bat
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Censr"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Censr"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,Censr}"; Flags: nowait postinstall skipifsilent
