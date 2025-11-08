#!/bin/bash

# エラーが発生したら即座に終了
set -e

echo "Starting application deployment..."

# マイグレーションの実行
echo "Running database migrations..."
python manage.py migrate --noinput

# 静的ファイルの収集
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Gunicornの起動
echo "Starting Gunicorn..."
gunicorn config.wsgi:application --bind=0.0.0.0:8000 --timeout 600 --workers 4
