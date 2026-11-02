@echo off
cd /d "%~dp0"
python -m pip install -q --disable-pip-version-check -r requirements.txt
start "" /b pythonw.exe app.py
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765/"
exit
