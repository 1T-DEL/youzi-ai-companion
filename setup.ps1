# ==========================================================
# 电子女友 · 安装脚本（首次运行执行一次）
# 用法：右键「用 PowerShell 运行」，或在项目目录执行 .\setup.ps1
# ==========================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "== 检查 Python ==" -ForegroundColor Cyan
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "未找到 python，请先安装 Python 3.10+ 并勾选 Add to PATH。" -ForegroundColor Red
    exit 1
}
python --version

Write-Host "`n== 安装依赖 ==" -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Write-Host "`n== 完成 ==" -ForegroundColor Green
Write-Host "现在运行 .\run.ps1 启动，浏览器打开 http://127.0.0.1:8000"
Write-Host "如果 .env 还没生成：打开 setup.html 填表 -> 保存到项目目录（选本文件夹），或下载 .env 放到这里。"
