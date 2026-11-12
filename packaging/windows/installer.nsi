Unicode true
RequestExecutionLevel user
!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"

!ifndef APP_STATE_KEY
  !define APP_STATE_KEY "Software\XHSStickerImporter\Installer"
!endif
!ifndef APP_UNINSTALL_KEY
  !define APP_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\XHSStickerImporterDesktop"
!endif
!ifndef MENU_FOLDER
  !define MENU_FOLDER "表情搬运助手"
!endif
!ifndef DESKTOP_LINK
  !define DESKTOP_LINK "表情搬运助手.lnk"
!endif

Name "表情搬运助手"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\StickerImporter"
InstallDirRegKey HKCU "${APP_STATE_KEY}" "InstallDir"
ShowInstDetails show
ShowUninstDetails show
SetCompressor zlib
VIProductVersion "0.8.1.0"
VIAddVersionKey "ProductName" "表情搬运助手"
VIAddVersionKey "FileDescription" "表情搬运助手安装程序"
VIAddVersionKey "FileVersion" "0.8.1"
VIAddVersionKey "ProductVersion" "0.8.1"
VIAddVersionKey "LegalCopyright" "Copyright (C) 2026 magofficinalis and contributors"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\StickerImporter.exe"
!define MUI_FINISHPAGE_RUN_TEXT "打开表情搬运助手"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "本版本需要 64 位 Windows。"
    Abort
  ${EndIf}
  SetShellVarContext current
FunctionEnd

Section "程序和使用说明（必需）" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "${APP_PAYLOAD}\*.*"
  File "/oname=先看这里.html" "${QUICK_GUIDE}"
  File "/oname=NSIS许可.txt" "${NSIS_LICENSE}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${APP_STATE_KEY}" "InstallDir" "$INSTDIR"
  ClearErrors
  ReadRegDWORD $0 HKCU "${APP_STATE_KEY}" "DesktopShortcut"
  IfErrors 0 +2
  WriteRegDWORD HKCU "${APP_STATE_KEY}" "DesktopShortcut" 0
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "DisplayName" "表情搬运助手"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "DisplayVersion" "0.8.1"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\StickerImporter.exe"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${APP_UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${APP_UNINSTALL_KEY}" "NoRepair" 1
  CreateDirectory "$SMPROGRAMS\${MENU_FOLDER}"
  CreateShortCut "$SMPROGRAMS\${MENU_FOLDER}\表情搬运助手.lnk" "$INSTDIR\StickerImporter.exe"
  CreateShortCut "$SMPROGRAMS\${MENU_FOLDER}\先看这里.lnk" "$INSTDIR\先看这里.html"
  CreateShortCut "$SMPROGRAMS\${MENU_FOLDER}\卸载表情搬运助手.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

Section /o "创建桌面快捷方式" SecDesktop
  IfFileExists "$DESKTOP\${DESKTOP_LINK}" desktop_done
  CreateShortCut "$DESKTOP\${DESKTOP_LINK}" "$INSTDIR\StickerImporter.exe"
  WriteRegDWORD HKCU "${APP_STATE_KEY}" "DesktopShortcut" 1
  desktop_done:
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$SMPROGRAMS\${MENU_FOLDER}\表情搬运助手.lnk"
  Delete "$SMPROGRAMS\${MENU_FOLDER}\先看这里.lnk"
  Delete "$SMPROGRAMS\${MENU_FOLDER}\卸载表情搬运助手.lnk"
  RMDir "$SMPROGRAMS\${MENU_FOLDER}"
  ReadRegDWORD $0 HKCU "${APP_STATE_KEY}" "DesktopShortcut"
  IntCmp $0 1 0 desktop_not_owned desktop_not_owned
  Delete "$DESKTOP\${DESKTOP_LINK}"
  desktop_not_owned:
  !include "${UNINSTALL_INCLUDE}"
  Delete "$INSTDIR\先看这里.html"
  Delete "$INSTDIR\NSIS许可.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${APP_UNINSTALL_KEY}"
  DeleteRegKey HKCU "${APP_STATE_KEY}"
SectionEnd
