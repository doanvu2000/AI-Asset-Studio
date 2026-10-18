@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Git - AI Asset Studio

:menu
cls
for /f "delims=" %%b in ('git branch --show-current') do set "BRANCH=%%b"
echo ==========================================
echo   AI Asset Studio  -  branch: !BRANCH!
echo ==========================================
git status --short
echo ------------------------------------------
echo   1. Pull
echo   2. Commit (add all)
echo   3. Push
echo   4. Commit + Push
echo   5. Pull + Commit + Push
echo   6. Log (15 commit gan nhat)
echo   7. Diff (thay doi chua stage)
echo   0. Thoat
echo ------------------------------------------
set "CHOICE="
set /p "CHOICE=Chon: "

if "!CHOICE!"=="1" call :pull & goto pause
if "!CHOICE!"=="2" call :commit & goto pause
if "!CHOICE!"=="3" call :push & goto pause
if "!CHOICE!"=="4" call :commit && call :push & goto pause
if "!CHOICE!"=="5" call :pull && call :commit && call :push & goto pause
if "!CHOICE!"=="6" git log --oneline --graph --decorate -15 & goto pause
if "!CHOICE!"=="7" git --no-pager diff --stat & goto pause
if "!CHOICE!"=="0" exit /b 0
goto menu

:pause
echo.
pause
goto menu

:pull
echo.
echo ^>^> git pull --rebase --autostash
git pull --rebase --autostash
if errorlevel 1 (echo [LOI] Pull that bai. & exit /b 1)
exit /b 0

:commit
echo.
git add -A
git diff --cached --quiet
if not errorlevel 1 (echo Khong co gi de commit. & exit /b 0)
git diff --cached --stat
echo.
set "MSG="
set /p "MSG=Commit message (de trong = huy): "
if "!MSG!"=="" (git reset -q & echo Da huy commit. & exit /b 1)
git commit -m "!MSG!"
if errorlevel 1 (echo [LOI] Commit that bai. & exit /b 1)
exit /b 0

:push
echo.
git rev-parse --abbrev-ref --symbolic-full-name @{u} >nul 2>&1
if errorlevel 1 (
    echo ^>^> git push -u origin !BRANCH!
    git push -u origin !BRANCH!
) else (
    echo ^>^> git push
    git push
)
if errorlevel 1 (echo [LOI] Push that bai. & exit /b 1)
exit /b 0
