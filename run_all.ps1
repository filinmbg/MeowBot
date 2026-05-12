# run_all.ps1

Write-Host "Starting MeowBot stack..." -ForegroundColor Green

Start-Process powershell -ArgumentList "-NoExit", "cd `"$PWD`"; python -m meowbot.apps.telegram_bot.main"
Start-Process powershell -ArgumentList "-NoExit", "cd `"$PWD`"; python -m meowbot.apps.telegram_event_worker.main"
Start-Process powershell -ArgumentList "-NoExit", "cd `"$PWD`"; python -m meowbot.apps.bot_online.main"