#define AppName "Serial Bridge Manager"
#define VersionFileHandle FileOpen(AddBackslash(SourcePath) + "..\VERSION")
#if !VersionFileHandle
  #error "Cannot open VERSION"
#endif
#define AppVersion Trim(FileRead(VersionFileHandle))
#expr FileClose(VersionFileHandle)
#define AppPublisher "xinme324"
#define AppExeName "SerialBridgeManager.exe"
#define DisplayName "串口桥接"
#define AppUserModelId "xinme324.SerialBridgeManager"
#define DistDir "..\build\nuitka\main.dist"

[Setup]
AppId={{7E7D2764-35C9-4D48-B5A8-3D6784D51E21}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=mailto:xinme324@163.com
AppSupportURL=mailto:xinme324@163.com
DefaultDirName={localappdata}\Programs\Serial Bridge Manager
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=SerialBridgeManager-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\serial_bridge\assets\serial_bridge.ico
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\assets\serial_bridge.ico
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\serial_bridge\assets\serial_bridge.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

[InstallDelete]
Type: files; Name: "{autoprograms}\Serial Bridge Manager.lnk"
Type: files; Name: "{autodesktop}\Serial Bridge Manager.lnk"

[Icons]
Name: "{autoprograms}\{#DisplayName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\serial_bridge.ico"; AppUserModelID: "{#AppUserModelId}"
Name: "{autodesktop}\{#DisplayName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\serial_bridge.ico"; AppUserModelID: "{#AppUserModelId}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#DisplayName}"; Flags: nowait postinstall skipifsilent
