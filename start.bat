@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title duckai-api - OpenAI compatible gateway

:: ==========================================================================
::  duckai-api launcher
::
::    start.bat                  start with defaults, port 3006
::    start.bat --port 8080      custom port
::    start.bat --visible        show the browser window (HEADLESS=false)
::    start.bat --key <token>    require Bearer auth on /v1/*
::    start.bat --install        create .venv and install dependencies
::    start.bat --help
::
::  Never hardcode a real API key in this file. Pass it with --key, or set
::  API_KEY in your environment before running this script.
::
::  NOTE: batch text below is deliberately ASCII-only and this file must keep
::  CRLF line endings - cmd seeks by byte offset after goto/call and drifts
::  (mangles lines) on LF-only files.
:: ==========================================================================

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "PORT=3006"
set "HEADLESS=true"
set "WANT_INSTALL="
set "API_KEY="

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--port" (
    set "PORT=%~2"
    shift
    shift
    goto parse
)
if /i "%~1"=="--visible" (
    set "HEADLESS=false"
    shift
    goto parse
)
if /i "%~1"=="--key" (
    set "API_KEY=%~2"
    shift
    shift
    goto parse
)
if /i "%~1"=="--install" (
    set "WANT_INSTALL=1"
    shift
    goto parse
)
if /i "%~1"=="--help"  goto usage
if /i "%~1"=="/?"     goto usage
echo   unknown option: %~1
echo.
goto usage

:usage
echo.
echo   duckai-api launcher
echo.
echo     start.bat                 start on port 3006, no auth
echo     start.bat --port 8080      use another port
echo     start.bat --visible        show the browser window
echo     start.bat --key TOKEN      require Bearer auth on /v1/*
echo     start.bat --install        create .venv and install dependencies
echo.
echo   Env: PORT, HOST, HEADLESS, API_KEY, RECYCLE_AFTER_REQUESTS,
echo        RECYCLE_INTERVAL_HOURS
echo.
exit /b 1

:parsed

:: --------------------------------------------------------------------------
::  ANSI colors - pure ASCII escape sequences, safe in any console
:: --------------------------------------------------------------------------
set "ESC="
for /F "delims=#" %%E in ('"prompt #$E# & echo on & for %%B in (1) do rem"') do set "ESC=%%E"
if defined ESC (
    set "RS=%ESC%[0m"
    set "B=%ESC%[1m"
    set "CY=%ESC%[96m"
    set "GR=%ESC%[92m"
    set "YE=%ESC%[93m"
    set "RD=%ESC%[91m"
    set "MG=%ESC%[95m"
    set "GY=%ESC%[90m"
    set "WH=%ESC%[97m"
) else (
    set "RS="
    set "B="
    set "CY="
    set "GR="
    set "YE="
    set "RD="
    set "MG="
    set "GY="
    set "WH="
)
set "RULE============================================================================"

echo.
echo   %B%%CY% duckai-api %RS%   %GY%OpenAI-compatible gateway%RS%   %GY%v1.2.0%RS%
echo   %GY%!RULE!%RS%
echo.

:: --------------------------------------------------------------------------
::  Environment checks
:: --------------------------------------------------------------------------
echo   %B%%MG%ENVIRONMENT%RS%

set "PY="
if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo     %RD%[x]%RS% python not found on PATH
    echo         install Python 3.11+ from python.org, tick "Add python.exe to PATH"
    echo.
    exit /b 1
)
set "PYVER=?"
for /f "tokens=2" %%V in ('"%PY%" --version 2^>^&1') do set "PYVER=%%V"
echo     %GR%[+]%RS% python !PYVER!   %GY%!PY!%RS%

"%PY%" -c "import fastapi, uvicorn, playwright, pydantic" >nul 2>&1
if errorlevel 1 (
    echo     %YE%[!]%RS% dependencies missing
    if defined WANT_INSTALL (
        call :do_install
        if errorlevel 1 goto abort_deps
    ) else (
        set /p "ANS=        install them now into .venv? [Y/n] "
        if /i "!ANS!"=="n" goto abort_deps
        call :do_install
        if errorlevel 1 goto abort_deps
    )
    "%PY%" -c "import fastapi, uvicorn, playwright, pydantic" >nul 2>&1
    if errorlevel 1 goto abort_deps
) else (
    echo     %GR%[+]%RS% dependencies present   %GY%fastapi / uvicorn / playwright / pydantic%RS%
)

set "BROWSER="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "BROWSER=Chrome"
if not defined BROWSER if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" set "BROWSER=Edge"
if not defined BROWSER (
    echo     %RD%[x]%RS% no Chrome or Edge found - the bridge cannot launch
) else (
    echo     %GR%[+]%RS% browser: !BROWSER!
)

set "BUSY="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort !PORT! -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess"`) do set "BUSY=%%P"
if defined BUSY (
    echo     %RD%[x]%RS% port !PORT! already in use   %GY%pid !BUSY!%RS%
    echo         stop it:  %WH%taskkill /PID !BUSY! /F%RS%
    echo         or use:   %WH%start.bat --port 8080%RS%
    echo.
    exit /b 1
) else (
    echo     %GR%[+]%RS% port !PORT! free
)
echo.

:: --------------------------------------------------------------------------
::  Connection card
:: --------------------------------------------------------------------------
echo   %GY%!RULE!%RS%
echo   %B%%WH%CONNECT%RS%
echo.
echo     %GY%Base URL  %RS% %WH%http://localhost:!PORT!/v1%RS%
if defined API_KEY (
    echo     %GY%API key   %RS% %YE%required%RS%  %GY%- header: Authorization: Bearer ^<token^>%RS%
) else (
    echo     %GY%API key   %RS% %GR%not required%RS%  %GY%- no Authorization header needed%RS%
)
echo     %GY%Model     %RS% %WH%gpt-6-luna%RS%  %GY%- or any id from MODELS below%RS%
echo     %GY%Web GUI   %RS% %WH%http://localhost:!PORT!/%RS%
echo     %GY%API docs  %RS% %WH%http://localhost:!PORT!/docs%RS%
echo     %GY%Health    %RS% %WH%http://localhost:!PORT!/health%RS%
echo.
echo   %B%%MG%MODELS%RS%
echo     %GR%gpt-6-luna%RS%       %GR%claude-haiku-5.5%RS%   %GR%gpt-5.4-mini%RS%
echo     %GR%mistral-small-4%RS%  %GR%gpt-oss-120b%RS%     %GR%gemma-4-31b%RS%
echo     %GY%aliases: gpt-4o, gpt-3.5-turbo, claude, claude-haiku-4.5, mistral, gemma, oss-120b%RS%
echo.
echo   %B%%MG%CONNECT FROM A CLIENT%RS%
echo.
echo     %CY%curl%RS%
echo       curl http://localhost:!PORT!/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"gpt-6-luna\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
echo.
echo     %CY%powershell%RS%
echo       $b = '{"model":"gpt-6-luna","messages":[{"role":"user","content":"hi"}]}'
echo       Invoke-RestMethod http://localhost:!PORT!/v1/chat/completions -Method Post -ContentType 'application/json' -Body $b
echo.
echo     %CY%python - openai sdk%RS%   %GY%needs: pip install openai%RS%
echo       from openai import OpenAI
echo       c = OpenAI(base_url="http://localhost:!PORT!/v1", api_key="local")
echo       r = c.chat.completions.create(model="gpt-6-luna", messages=[{"role":"user","content":"hi"}])
echo       print(r.choices[0].message.content)
echo.
echo   %B%%MG%NOTES%RS%
echo     %GY%-%RS% one generation at a time - concurrent requests queue on a lock
echo     %GY%-%RS% temperature and max_tokens are accepted but ignored upstream
echo     %GY%-%RS% no embeddings, tool calling, JSON mode or vision
echo     %GY%-%RS% /health and / are unauthenticated - keep this port off the internet
if defined API_KEY (
    echo     %GR%-%RS% auth enabled - token is not printed on purpose
) else (
    echo     %YE%-%RS% auth disabled - anyone who reaches this port can call /v1/*
)
echo.
echo   %GY%!RULE!%RS%
echo   %GR%%B%starting%RS%   %GY%logs stream below%RS%   %WH%Ctrl+C%RS% %GY%to stop%RS%
echo.

:: --------------------------------------------------------------------------
::  Launch
:: --------------------------------------------------------------------------
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "HOST=0.0.0.0"

"%PY%" "%ROOT%server.py"
set "CODE=%ERRORLEVEL%"

echo.
if "!CODE!"=="0" (
    echo   %GY%server stopped%RS%
) else (
    echo   %GY%server stopped%RS%   %GY%- exit code !CODE!%RS%
)
echo.
exit /b !CODE!

:: --------------------------------------------------------------------------
::  Subroutines
:: --------------------------------------------------------------------------
:do_install
echo.
if not exist "%ROOT%.venv\Scripts\python.exe" (
    echo         creating .venv   %GY%- this happens once%RS%
    "%PY%" -m venv "%ROOT%.venv"
    if errorlevel 1 (
        echo         %RD%failed to create the virtual environment%RS%
        exit /b 1
    )
)
set "PY=%ROOT%.venv\Scripts\python.exe"
echo         installing requirements   %GY%- needs internet%RS%
"%PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%PY%" -m pip install -r "%ROOT%requirements.txt" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo         %RD%dependency install failed%RS%
    exit /b 1
)
echo         %GR%[+]%RS% installed
exit /b 0

:abort_deps
echo.
echo   %RD%cannot start without dependencies%RS%
echo   run:  %WH%start.bat --install%RS%
echo.
exit /b 1
