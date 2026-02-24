@echo off
setlocal
cd /d "C:\Users\HomePC\Desktop\My Personal Assistant"

python victor_os\ml_gpu_setup_check.py
python victor_os\ml_train_job.py --epochs 8 --device auto
python victor_os\ml_evaluate_job.py --accuracy-threshold 0.75
python victor_os\ml_shadow_test_job.py --min-shadow-accuracy 0.75
python victor_os\ml_monitoring_report.py

echo.
echo If shadow compare passed and governance is approved, promote manually:
echo python victor_os\ml_promote_job.py --safety-gate-passed --regression-delta 0.01
endlocal
