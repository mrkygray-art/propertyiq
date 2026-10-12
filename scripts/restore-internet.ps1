# Target of the "PropertyIQ - Restore internet" desktop shortcut. Turns any PropertyIQ lockdown off.
# Windows asks for permission first (the script changes firewall rules).
Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\lockdown.ps1`" -Mode Off"
