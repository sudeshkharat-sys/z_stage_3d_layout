@echo off
echo Installing dependencies...
pip install -r requirements.txt
echo.
echo Starting VRML Converter at http://localhost:5555
echo Press Ctrl+C to stop.
echo.
python app.py
pause
