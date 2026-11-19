; Inno Setup script of the Windows installer (docs/FLASH_TOOL.md §7). PREPARED ONLY - not compiled or tested yet (no Windows runtime in
; this repository; G3 F-6). Input: the PyInstaller one-folder output dist\hifi-bridge-flash built ON WINDOWS with
;     pyinstaller --noconfirm flashtool\packaging\hifi-bridge-flash.spec
;     python flashtool\packaging\collect_licenses.py dist\hifi-bridge-flash\licenses
; Code signing: the owner signs hifi-bridge-flash.exe, hififlash-helper.exe and the installer with their certificate (SignTool). No
; certificate or key is part of this repository; "SignTool=ownersign" refers to a sign tool the owner configures in the Inno Setup IDE.
#define AppVersion "0.1.0"

[Setup]
AppId={{6A0F4E1C-8C4B-4F2E-9B7D-2E5C1A3D9F10}
AppName=Hi-Fi Bridge Flash Tool
AppVersion={#AppVersion}
AppPublisher=Hi-Fi Bridge
AppPublisherURL=https://github.com/iskaron/hifi-bridge
DefaultDirName={autopf}\Hi-Fi Bridge Flash Tool
DefaultGroupName=Hi-Fi Bridge
LicenseFile=..\..\..\LICENSE
OutputBaseFilename=hifi-bridge-flash-{#AppVersion}-windows-x64-setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
; SignTool=ownersign
; SignedUninstaller=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Files]
; one folder, Qt/PySide6 as separate replaceable DLLs (LGPL-3.0)
Source: "..\..\..\dist\hifi-bridge-flash\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Hi-Fi Bridge Flash Tool"; Filename: "{app}\hifi-bridge-flash.exe"
Name: "{group}\Licences"; Filename: "{app}\licenses\THIRD_PARTY_NOTICES.txt"

[Run]
; started without elevation: the GUI must not inherit the installer's administrator rights
Filename: "{app}\hifi-bridge-flash.exe"; Flags: nowait postinstall skipifsilent runasoriginaluser
