@echo off
setlocal EnableDelayedExpansion
title BurgerBot Launcher

REM ============================================================
REM  BurgerBot one-click launcher
REM   1. Pulls the latest code to this machine
REM   2. SSHes into EC2, pulls there, rebuilds + starts Docker
REM   3. Waits for the backend to report healthy
REM   4. Opens the Grill Station dashboard in your browser
REM  Edit the three values below if your host/key ever change.
REM ============================================================
set "EC2_HOST=ubuntu@18.226.72.231"
set "KEY_PATH=%USERPROFILE%\.ssh\BurgerKey.pem"
set "REMOTE_DIR=/opt/burgerbot"
set "DASHBOARD_URL=http://18.226.72.231:8000/"
set "HEALTH_URL=http://18.226.72.231:8000/health"

echo.
echo   ============================================
echo      ^>^>^>  BURGERBOT GRILL STARTUP  ^<^<^<
echo   ============================================
echo.

if not exist "%KEY_PATH%" (
    echo [ERROR] SSH key not found: %KEY_PATH%
    pause
    exit /b 1
)

echo [1/4] Pulling latest code to this machine...
pushd "%~dp0"
git pull --ff-only
if errorlevel 1 (
    echo [WARN] Local git pull failed - continuing with EC2 deploy anyway.
)
popd
echo.

echo [2/4] Deploying on EC2 (git pull + docker rebuild)...
ssh -i "%KEY_PATH%" -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new %EC2_HOST% "cd %REMOTE_DIR% && git pull --ff-only && docker compose up -d --build"
if errorlevel 1 (
    echo [ERROR] EC2 deploy failed. Check that the instance is running and the IP is current.
    echo         AWS console -^> EC2 -^> Instances. If the IP changed, edit EC2_HOST in this file.
    pause
    exit /b 1
)
echo.

echo [3/4] Waiting for the backend to report healthy...
set HEALTHY=0
for /l %%i in (1,1,30) do (
    if !HEALTHY! equ 0 (
        for /f %%c in ('curl -s -o nul -w "%%{http_code}" --max-time 4 "%HEALTH_URL%" 2^>nul') do (
            if "%%c"=="200" (
                set HEALTHY=1
            )
        )
        if !HEALTHY! equ 0 (
            timeout /t 2 /nobreak >nul
        )
    )
)
if !HEALTHY! equ 1 (
    echo        Backend is up and healthy!
) else (
    echo [WARN] Backend did not respond within ~60s. Opening the dashboard anyway -
    echo        it will show KITCHEN OFFLINE until the container finishes starting.
)
echo.

echo [4/4] Opening the Grill Station dashboard...
start "" "%DASHBOARD_URL%"
echo.
echo   Burgers are on the grill. This window can be closed.
echo.
pause
