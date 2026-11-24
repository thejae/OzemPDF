@echo off
rem Runs the bundled OzemPDF engine and opens the interface in the default web
rem browser. No Electron, no Python installation and no installer required.
rem All app-owned files (settings, staging, temp, logs) stay in .\data next to
rem this script. Close this window to stop the local server.
setlocal
cd /d "%~dp0"
set "OZEMPDF_DATA_DIR=%~dp0data"
set "TEMP=%~dp0data\temp"
set "TMP=%~dp0data\temp"
if not exist "%TEMP%" mkdir "%TEMP%"
"%~dp0resources\engine\ozempdf-engine\ozempdf-engine.exe" --serve %*
