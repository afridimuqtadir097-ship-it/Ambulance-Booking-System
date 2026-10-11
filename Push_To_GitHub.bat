@echo off
title Push MedRush to GitHub
cd /d "%~dp0"

echo.
echo Sending your latest MedRush code to GitHub...
echo.

git add .
git commit -m "Update MedRush"
git push

echo.
echo ----------------------------------------------------
echo If you see "main -> main" above, your code is on GitHub.
echo Now go to https://dashboard.render.com to deploy it.
echo ----------------------------------------------------
echo.
pause
