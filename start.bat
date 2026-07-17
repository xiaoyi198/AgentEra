@echo off

cd /d %~dp0

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found
    pause
    exit /b 1
)

node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js not found
    pause
    exit /b 1
)

echo [1/4] Installing Python deps...
python -m pip install -r requirements.txt -q

echo [2/4] Installing frontend deps...
cd /d %~dp0frontend
call npm install --silent
cd /d %~dp0

echo [3/4] Starting backend on localhost:8000 with auto-reload ...
start "Backend" python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

timeout /t 3 /nobreak >nul

echo [4/4] Starting frontend on localhost:3000 ...
cd /d %~dp0frontend
start "Frontend" npm run dev -- --host
cd /d %~dp0

echo.
echo   Backend: http://localhost:8000
echo   Frontend: http://localhost:3000
echo.
pause
