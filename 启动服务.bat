@echo off
chcp 65001 >nul
title 知识库管理平台 - http://127.0.0.1:8801
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 Python，请先安装 Python 3.10+ 并加入 PATH。
  pause
  exit /b 1
)

echo ============================================================
echo   知识库管理平台（项目实战 2.9）
echo   启动后请用浏览器访问：http://127.0.0.1:8801
echo   演示账号：admin / kadmin / zhangsan / chenjing（密码 123456）
echo ============================================================
echo.

python run.py
if errorlevel 1 (
  echo.
  echo [提示] 服务异常退出。若为“端口被占用”，可改用：
  echo        python run.py --port 9000
)
pause
