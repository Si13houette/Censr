; Inno Setup — установщик Censr. Собирается батником build.bat:
;   ISCC.exe /DMyAppVersion=<версия> installer.iss
; Версия по умолчанию берётся отсюда, если /D не передан.

#define MyAppName "Censr"
#ifndef MyAppVersion
  #define MyAppVersion "1.5.0"
#endif
#define MyAppExeName "Censr.exe"
#define MyAppPublisher "Censr"

[Setup]
; AppId уникален и СТАБИЛЕН между версиями — иначе апдейт поставится рядом, а не поверх.
AppId={{8B6E5A2C-4F1D-4C9A-9E2B-CE6F0A1D7B33}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Censr
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Ставим в профиль пользователя — без прав администратора, лог пишется рядом с exe.
PrivilegesRequired=lowest
OutputDir=installer\Output
OutputBaseFilename=Censr-Setup-{#MyAppVersion}
SetupIconFile=censr.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Вся папка сборки PyInstaller (exe + _internal + models + ffmpeg + censr.ico).
Source: "dist\Censr\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
