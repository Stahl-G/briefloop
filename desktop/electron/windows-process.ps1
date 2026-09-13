$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    Add-Type -Path (Join-Path $PSScriptRoot 'windows-process.cs') -ErrorAction Stop
    $request = [Console]::ReadLine() | ConvertFrom-Json
    if (-not $request.executable) { throw 'Missing executable' }
    [BriefLoopOwnedProcess]::Run([string]$request.executable, [string[]]$request.args)
} catch {
    [Console]::Out.WriteLine('{"type":"error","code":"supervisor_unavailable","cleanupConfirmed":true}')
    exit 1
}
