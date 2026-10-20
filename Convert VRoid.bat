@echo off
rem Drag a .vrm file onto this file to convert it (no .exe needed).
rem Put Kassandra's reference export next to this file as kassandra_reference.glb.
setlocal
set "HERE=%~dp0"
if "%~1"=="" (
  echo Drag your VRoid .vrm file onto "Convert VRoid.bat".
  pause & exit /b 1
)
set "REF=%HERE%kassandra_reference.glb"
if not exist "%REF%" (
  echo Kassandra's reference is missing. Export her mesh from AnvilToolkit as .glb
  echo and save it next to this file as kassandra_reference.glb
  pause & exit /b 1
)
set "BLENDER="
for /d %%D in ("%ProgramFiles%\Blender Foundation\Blender *") do if exist "%%D\blender.exe" set "BLENDER=%%D\blender.exe"
if not defined BLENDER if exist "%ProgramFiles(x86)%\Steam\steamapps\common\Blender\blender.exe" set "BLENDER=%ProgramFiles(x86)%\Steam\steamapps\common\Blender\blender.exe"
if defined BLENDER_EXE set "BLENDER=%BLENDER_EXE%"
if not defined BLENDER (
  echo Blender 4.2 or newer was not found. Install it from blender.org, or set BLENDER_EXE.
  pause & exit /b 1
)
"%BLENDER%" -b --factory-startup -P "%HERE%converter\run_converter.py" -- --vrm "%~1" --reference "%REF%" --out "%~dpn1_kassandra"
if errorlevel 1 (
  echo Conversion failed - see the messages above.
) else (
  echo Done. Open "%~dpn1_kassandra\NEXT_STEPS.txt" for the last steps.
  start "" "%~dpn1_kassandra"
)
pause
