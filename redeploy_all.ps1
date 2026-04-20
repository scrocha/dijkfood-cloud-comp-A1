Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  DIJKFOOD - REDEPLOY COMPLETO (destroy + deploy + sim)"     -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $PSScriptRoot

# 1. Destroy
Write-Host "[1/4] Destruindo infraestrutura..." -ForegroundColor Yellow
uv run python destroy.py --hard
if ($LASTEXITCODE -ne 0) { Write-Host "ERRO no destroy!" -ForegroundColor Red; exit 1 }

# 2. Deploy principal (RDS + DynamoDB + ECS APIs + Seed)
Write-Host ""
Write-Host "[2/4] Deploy principal (APIs + Seed)..." -ForegroundColor Yellow
uv run python deploy.py
if ($LASTEXITCODE -ne 0) { Write-Host "ERRO no deploy!" -ForegroundColor Red; exit 1 }

# 3. Deploy simuladores (ECR + ALB + ECS Services)
Write-Host ""
Write-Host "[3/4] Deploy simuladores..." -ForegroundColor Yellow
uv run python simulador_ecs/deploy_simulador.py
if ($LASTEXITCODE -ne 0) { Write-Host "ERRO no deploy simuladores!" -ForegroundColor Red; exit 1 }

# 4. Dashboard
Write-Host ""
Write-Host "[4/4] Abrindo Dashboard..." -ForegroundColor Green
uv run streamlit run simulador_ecs/dashboard_carga.py
