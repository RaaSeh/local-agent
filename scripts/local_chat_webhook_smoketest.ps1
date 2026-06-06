$ErrorActionPreference = 'Stop'

$python = 'c:/Users/platt/Desktop/local-agent/.venv/Scripts/python.exe'
$env:PYTHONPATH = 'c:/Users/platt/Desktop/local-agent/src'
$env:GOOGLE_CHAT_VERIFICATION_TOKEN = 'local-test-token'
$env:CHAT_ALLOWED_USERS = 'owner@example.com,partner@example.com'
$env:CHAT_MAX_RESPONSE_CHARS = '3500'

$uvicornCmd = "$python -m uvicorn local_agent.integrations.google_chat_bot:app --host 127.0.0.1 --port 8010"
$proc = Start-Process -FilePath 'powershell' -ArgumentList '-NoProfile', '-Command', $uvicornCmd -PassThru -WindowStyle Hidden

try {
    Start-Sleep -Seconds 2

    $health = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8010/health'
    Write-Host "Health:" ($health | ConvertTo-Json -Compress)

    $body = @{
        type = 'MESSAGE'
        token = 'local-test-token'
        user = @{ email = 'owner@example.com' }
        message = @{ text = '/agents' }
    } | ConvertTo-Json -Depth 6

    $resp = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8010/google-chat/events' -ContentType 'application/json' -Body $body
    Write-Host "Agents command response length:" $resp.text.Length
    Write-Host "Response preview:"
    Write-Host $resp.text
}
finally {
    if ($proc -and !$proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
    }
}
