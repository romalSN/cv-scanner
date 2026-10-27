@echo off
REM Maakt een snelkoppeling "CV Scanner" op je bureaublad.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'CV Scanner.lnk')); $s.TargetPath='%~dp0START_HIER_WINDOWS.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%SystemRoot%\System32\shell32.dll,22'; $s.WindowStyle=7; $s.Save()"
if errorlevel 1 (echo Lukte niet. & pause & exit /b 1)
echo Klaar: "CV Scanner" staat nu op je bureaublad.
pause
