param([string]$Python = 'python')

# Use the same installer, Python service and WebUI as other platforms.
# A separate Python path avoids changing the user's global PATH.
& $Python -X utf8 (Join-Path $PSScriptRoot 'bootstrap.py') @args
exit $LASTEXITCODE
