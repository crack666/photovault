@echo off
REM 1-Click-Stop: derselbe Weg wie start.bat, nur "stop".
call "%~dp0start.bat" stop
exit /b %ERRORLEVEL%
