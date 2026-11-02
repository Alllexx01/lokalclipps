@echo off
cd /d "%~dp0"
python -m pip install -q --disable-pip-version-check -r requirements.txt
start "" /b pythonw.exe pc_helper.py
exit
