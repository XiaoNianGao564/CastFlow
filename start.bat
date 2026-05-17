@echo off
cd /d "C:\Users\17166\Desktop\CastFlow"
title CastFlow Launcher

echo ==========================================
echo            CastFlow Starting
echo ==========================================
echo.

echo [1/3] Killing old bun processes...
taskkill /f /im bun.exe >nul 2>&1

rem Wait for port 3003 to be free (TIME_WAIT may take a while)
echo Waiting for port 3003...
for /l %%i in (1,1,10) do (
  netstat -ano | findstr /c:":3003 " | findstr LISTENING >nul 2>&1
  if errorlevel 1 goto PORT_FREE
  ping -n 2 127.0.0.1 >nul
)
:PORT_FREE

echo [2/3] Starting API on port 3003...
set PORT=3003
start "CastFlow-API" /min cmd /k "cd /d %CD% && set PORT=3003 && bun run --cwd packages\api src\index.ts"
ping -n 6 127.0.0.1 >nul

echo [3/3] Starting Web on port 3000...
set API_PORT=3003
start "CastFlow-Web" cmd /k "cd /d %CD% && set API_PORT=3003 && bun run --cwd packages\app dev"
ping -n 4 127.0.0.1 >nul

echo.
echo ==========================================
echo    Frontend:  http://localhost:3000
echo    API:       http://localhost:3003
echo ==========================================
echo.
echo Two windows opened. Close them to stop.
echo This window can be closed now.
echo.
pause
