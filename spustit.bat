@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py") || (set "PY=python")
%PY% -c "import requests, bs4" 2>nul || %PY% -m pip install --user requests beautifulsoup4
%PY% hlidac_aut.py --bez-mailu %*
start "" "docs\index.html"
pause
