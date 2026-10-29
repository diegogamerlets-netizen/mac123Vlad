#!/usr/bin/env bash
# Загрузка (и обновление) сайта на сервер. Запускать на своём компьютере из папки oblik:
#   bash deploy/deploy.sh root@IP-сервера
# Ключ по умолчанию: ~/.ssh/oblik_deploy (другой: SSH_KEY=/путь/к/ключу bash deploy/deploy.sh ...)
# Не затрагивает на сервере: файл .env и папку data (заказы, макеты).
set -euo pipefail

TARGET="${1:?Укажите сервер: bash deploy/deploy.sh root@IP}"
KEY="${SSH_KEY:-$HOME/.ssh/oblik_deploy}"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new"

echo "==> Загружаю файлы сайта"
tar czf - --exclude='./data' --exclude='./.env' --exclude='__pycache__' --exclude='./.venv' --exclude='*.bak' . \
  | $SSH "$TARGET" "mkdir -p /opt/oblik/app && tar xzf - -C /opt/oblik/app && chown -R oblik:oblik /opt/oblik/app 2>/dev/null || true"

echo "==> Перезапускаю службу"
$SSH "$TARGET" "chown -R oblik:oblik /opt/oblik/app; systemctl restart oblik 2>/dev/null && systemctl is-active oblik || echo 'служба ещё не настроена: запустите deploy/setup.sh на сервере'"
echo "Готово."
