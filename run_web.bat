@echo off
chcp 65001 > nul
echo ===================================================
echo   Đang khởi động ứng dụng Web Tennis Vui...
echo ===================================================

:: Kiểm tra python và streamlit
python -c "import streamlit" >nul 2>nul
if %errorlevel% neq 0 (
    echo [INFO] Đang cài đặt thư viện Streamlit...
    pip install streamlit pandas openpyxl
)

echo Khởi chạy Streamlit...
streamlit run app_web.py

pause
