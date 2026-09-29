$ErrorActionPreference = "Stop"

function New-Secret([int]$Bytes = 32) {
  $buffer = New-Object byte[] $Bytes
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $rng.GetBytes($buffer)
  $rng.Dispose()
  [Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function New-FernetKey {
  $buffer = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $rng.GetBytes($buffer)
  $rng.Dispose()
  [Convert]::ToBase64String($buffer).Replace('+', '-').Replace('/', '_')
}

if (Test-Path ".env") {
  if (-not (Select-String -Path ".env" -Pattern "^COMPOSE_BAKE=" -Quiet)) {
    Add-Content -Path ".env" -Value "COMPOSE_BAKE=false"
    Write-Host "Added COMPOSE_BAKE=false to existing .env."
  }
  if (-not (Select-String -Path ".env" -Pattern "^ADMIN_EMAIL=" -Quiet)) {
    Add-Content -Path ".env" -Value "ADMIN_EMAIL=admin@example.com"
    Add-Content -Path ".env" -Value "ADMIN_PASSWORD=StrongPass123"
    Add-Content -Path ".env" -Value "ADMIN_FULL_NAME=System Admin"
    Write-Host "Added default admin credentials to existing .env."
  }
  Write-Host ".env already exists; leaving it unchanged."
  exit 0
}

$content = @"
POSTGRES_DB=secure_library
POSTGRES_USER=app_admin
POSTGRES_PASSWORD=$(New-Secret)
AUTH_DB_USER=auth_user
AUTH_DB_PASSWORD=$(New-Secret)
LIBRARY_DB_USER=library_user
LIBRARY_DB_PASSWORD=$(New-Secret)
LOAN_DB_USER=loan_user
LOAN_DB_PASSWORD=$(New-Secret)
AUDIT_DB_USER=audit_user
AUDIT_DB_PASSWORD=$(New-Secret)
JWT_SECRET=$(New-Secret 48)
INTERNAL_API_KEY=$(New-Secret 32)
FILE_ENCRYPTION_KEY=$(New-FernetKey)
RABBITMQ_USER=secure_mq
RABBITMQ_PASSWORD=$(New-Secret)
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=StrongPass123
ADMIN_FULL_NAME=System Admin
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
OAUTH_REDIRECT_BASE=https://localhost:3443
COMPOSE_BAKE=false
"@

Set-Content -Path ".env" -Value $content -Encoding ASCII
Write-Host "Created .env with local development secrets."
