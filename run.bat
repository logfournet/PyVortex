@echo off
title PyVortex Minecraft Launcher
cd /d "%~dp0"
python main.py
if errorlevel 1 (
  echo.
  echo Python was not found. Install Python from https://www.python.org/downloads/
  echo (make sure to tick "Add Python to PATH").
  pause
)
