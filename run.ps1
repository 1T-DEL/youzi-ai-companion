# ==========================================================
# 电子女友 · 启动脚本
# 用法：在项目目录执行 .\run.ps1
# 启动后浏览器打开 http://127.0.0.1:8000
# ==========================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path ".env")) {
    Write-Host "未找到 .env，将以演示模式启动。" -ForegroundColor Yellow
    Write-Host "（先去 setup.html 填表生成配置，真实对话更完整）" -ForegroundColor Yellow
}

Write-Host "== 启动电子女友 ==" -ForegroundColor Cyan
python -m app.main
