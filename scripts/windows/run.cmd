@REM Copyright 2026 Lusoris
@REM SPDX-License-Identifier: EUPL-1.2
@echo off
setlocal

set "FOUND_ARCH=%PROCESSOR_ARCHITECTURE%"
if "%PROCESSOR_ARCHITECTURE%"=="AMD64" goto :arch_ok
if "%PROCESSOR_ARCHITEW6432%"=="AMD64" goto :arch_ok

echo ERROR: Unsupported architecture "%FOUND_ARCH%". Pelorus tester requires Windows x64 (AMD64). >&2
exit /b 2

:arch_ok
"%SystemRoot%\System32\where.exe" /q vulkan-1.dll
if %ERRORLEVEL% neq 0 (
    echo ERROR: vulkan-1.dll not found: install the GPU vendor's Vulkan driver; this zip does not ship the Vulkan loader >&2
    exit /b 3
)

"%~dp0ffmpeg.exe" %*
exit /b %ERRORLEVEL%
