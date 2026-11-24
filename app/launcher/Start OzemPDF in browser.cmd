@echo off
rem Runs the bundled OzemPDF engine and opens the interface in the default web
rem browser. No Electron, no Python installation and no installer required --
rem this is the fallback for managed hosts that block the Electron sandbox or
rem NSIS extraction. Close this window to stop the local server.
setlocal
cd /d "%~dp0"
"%~dp0resources\engine\ozempdf-engine\ozempdf-engine.exe" --serve %*
