@echo off
setlocal
set "CROUS_INTERACTIVE=1"
if not "%~1"=="" set "CROUS_INTERACTIVE=0"
pushd "%~dp0" || exit /b 2
set "CROUS_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%CROUS_PYTHON%" goto missing_python
if exist "cergy.json" goto configured
"%CROUS_PYTHON%" -m crous_personal init
if errorlevel 1 goto failed

:configured
if "%~1"=="" goto menu
if /i "%~1"=="status" goto command_status
if /i "%~1"=="changes" goto command_changes
if /i "%~1"=="history" goto command_history
echo Usage: Crous-Cergy.cmd [status ^| changes ^| history]
set "CROUS_RESULT=2"
goto finish

:menu
echo.
echo =====================================================
echo             MON SUIVI CROUS - CERGY
echo =====================================================
echo Tes preferences sont conservees dans cergy.json (statut: 1).
echo La recherche publique ne remplace pas le profil DSE.
echo Aucun email ou Telegram envoye.
echo.
echo 1. Voir le statut conserve - sans reseau
echo 2. Faire un controle public ponctuel
echo 3. Voir l'historique - sans reseau
echo 4. Voir les alertes locales - sans reseau
echo 5. Surveiller maintenant au premier plan - Ctrl+C arrete
echo 0. Quitter
echo.
choice /c 123450 /n /m "Ton choix: "
if errorlevel 6 goto quit
if errorlevel 5 goto menu_monitor
if errorlevel 4 goto menu_changes
if errorlevel 3 goto menu_history
if errorlevel 2 goto menu_check
if errorlevel 1 goto menu_status
goto quit

:menu_status
call :run status
goto menu_pause
:menu_check
call :run check
goto menu_pause
:menu_history
call :run history
goto menu_pause
:menu_changes
call :run changes
goto menu_pause
:menu_monitor
echo Surveillance au premier plan. Fermer cette fenetre l'arrete aussi.
call :run monitor
goto menu_pause
:menu_pause
echo.
pause
goto menu

:command_status
call :run status
set "CROUS_RESULT=%errorlevel%"
goto finish
:command_changes
call :run changes
set "CROUS_RESULT=%errorlevel%"
goto finish
:command_history
call :run history
set "CROUS_RESULT=%errorlevel%"
goto finish

:run
"%CROUS_PYTHON%" -m crous_personal %1
exit /b %errorlevel%

:missing_python
echo L'environnement .venv est absent. Consulte README-PERSONAL.md.
set "CROUS_RESULT=2"
if "%CROUS_INTERACTIVE%"=="1" pause
goto finish
:failed
set "CROUS_RESULT=%errorlevel%"
if "%CROUS_INTERACTIVE%"=="1" pause
goto finish
:quit
set "CROUS_RESULT=0"
:finish
popd
exit /b %CROUS_RESULT%
