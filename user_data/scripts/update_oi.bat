@echo off
cd /d e:\freqtrade
docker compose run --rm --entrypoint python freqtrade //freqtrade/user_data/scripts/download_oi.py
