; Knowledge Arch uninstall hook (tauri.conf.json > bundle.windows.nsis.installerHooks).
; Kept ASCII-only: NSIS reads BOM-less includes in the system code page.
;
; Tauri's own "Delete the application data" only removes the folders named after the bundle
; identifier (WebView cache); our data lives in "Knowledge Arch" folders. When the box is ticked
; and this is not an update, delete it with the rules of src/wipe.rs:
; - the platform default folders are removed entirely;
; - a custom data folder (written by the shell to %APPDATA%\Knowledge Arch\data-dir.txt, UTF-16LE)
;   is removed entirely only when it holds .knowledge-arch-owned, otherwise only the app's own
;   entries go (same list as KNOWN_ENTRIES in wipe.rs; a wipe.rs test keeps them in sync).
; The keychain master key stays in Credential Manager; it is useless once the data is gone.

!macro KA_REMOVE_ENTRY PATH
  RmDir /r "${PATH}"
  Delete "${PATH}"
!macroend

!macro KA_REMOVE_KNOWN_ENTRIES DIR
  !insertmacro KA_REMOVE_ENTRY "${DIR}\.knowledge-arch-owned"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\.ka_server.lock"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\.legacy_import"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\.r2_restore_rollback"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\.r2_restore_staging"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\agent-connection.json"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\embedding_cache.db"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\embedding_cache.db-journal"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\embedding_cache.db-shm"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\embedding_cache.db-wal"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\extras"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\index_compatibility.json"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\knowledge_repository.db"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\knowledge_repository.db-journal"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\knowledge_repository.db-shm"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\knowledge_repository.db-wal"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\library"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\lightrag_workspaces"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\logs"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\managed_docs"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\secrets"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\settings.json"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\uploads"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\vector_store.db"
  !insertmacro KA_REMOVE_ENTRY "${DIR}\zotero_web_cache"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    Push $R0
    Push $R1
    Push $R2
    SetShellVarContext current
    StrCpy $R1 ""
    ClearErrors
    FileOpen $R0 "$APPDATA\Knowledge Arch\data-dir.txt" r
    ${IfNot} ${Errors}
      FileReadUTF16LE $R0 $R1
      FileClose $R0
    ${EndIf}
    StrLen $R2 $R1
    ; never touch a drive root (C:\) or the user profile itself
    ${If} $R2 > 3
    ${AndIf} $R1 != $PROFILE
      ${If} ${FileExists} "$R1\.knowledge-arch-owned"
        RmDir /r "$R1"
      ${Else}
        !insertmacro KA_REMOVE_KNOWN_ENTRIES "$R1"
        RmDir "$R1"
      ${EndIf}
    ${EndIf}
    RmDir /r "$LOCALAPPDATA\Knowledge Arch"
    RmDir /r "$APPDATA\Knowledge Arch"
    Pop $R2
    Pop $R1
    Pop $R0
  ${EndIf}
!macroend
