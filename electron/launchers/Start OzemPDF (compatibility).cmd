@echo off
rem Compatibility launcher for managed, containerised or otherwise restricted
rem Windows hosts where Chromium's sandbox refuses to initialise (the standard
rem OzemPDF.exe exits before its main process starts). It runs the same
rem application with the sandbox disabled. Prefer OzemPDF.exe on normal PCs.
start "" "%~dp0OzemPDF.exe" --no-sandbox %*
