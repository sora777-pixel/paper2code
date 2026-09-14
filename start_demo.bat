@echo off
setlocal
cd /d "%~dp0"
title paper2code Demo
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "PY=D:\Python311\python.exe"
set "VENV=%CD%\.venv\Scripts\python.exe"
set "PORT=8010"
set "P2C_PDF_BACKEND=hybrid"
set "P2C_DOCLING_PYTHON=C:\Users\lenovo\paper2code-xfer\pdf-bakeoff\.venv\Scripts\python.exe"
if not exist "%P2C_DOCLING_PYTHON%" (
  echo [warn] Docling bakeoff python missing; PDF backend falls back to pymupdf
  set "P2C_PDF_BACKEND=pymupdf"
  set "P2C_DOCLING_PYTHON="
)

if not exist "%PY%" (
  echo [ERROR] missing D:\Python311\python.exe
  echo Install portable Python 3.11 to D:\Python311. Do not change system PATH.
  pause
  exit /b 1
)

if not exist "%VENV%" (
  echo [setup] create .venv ...
  "%PY%" -m venv .venv
  if errorlevel 1 (
    echo [ERROR] venv failed
    pause
    exit /b 1
  )
)

echo [setup] install extras if needed ...
"%VENV%" -m pip install -r requirements-win.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo [ERROR] pip failed
  pause
  exit /b 1
)

echo [open] http://127.0.0.1:%PORT%
start "" "http://127.0.0.1:%PORT%"
"%VENV%" -m paper2code serve --port %PORT%
