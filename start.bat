@echo off
cd /d %~dp0
echo Starting Cowan ^& Rutter Property Database...
start /b pythonw serve.py
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8080
echo Done. Server running at http://127.0.0.1:8080
