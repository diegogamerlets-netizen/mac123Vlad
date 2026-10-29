#!/usr/bin/env bash
# Первичная настройка сервера (Ubuntu 22.04/24.04). Запускать на сервере от root:
#   bash setup.sh ваш-домен.ru email@для-сертификата.ru
# Скрипт можно запускать повторно: он ничего не ломает.
set -euo pipefail

DOMAIN="${1:?Укажите домен: bash setup.sh example.ru you@example.ru}"
EMAIL="${2:?Укажите email для сертификата (Lets Encrypt)}"
APP=/opt/oblik/app
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx python3 certbot python3-certbot-nginx ufw cron

echo "==> Пользователь и папки"
id oblik >/dev/null 2>&1 || useradd --system --home /opt/oblik --shell /usr/sbin/nologin oblik
mkdir -p "$APP/data" /opt/oblik/backups
chown -R oblik:oblik /opt/oblik

echo "==> Служба приложения"
install -m 644 "$HERE/oblik.service" /etc/systemd/system/oblik.service
systemctl daemon-reload
systemctl enable oblik

echo "==> nginx (пока без HTTPS)"
sed "s/__DOMAIN__/$DOMAIN/g" "$HERE/nginx.conf.template" > /etc/nginx/sites-available/oblik
ln -sf /etc/nginx/sites-available/oblik /etc/nginx/sites-enabled/oblik
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> Файрвол: только SSH, HTTP, HTTPS"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "==> Ежедневные задачи (очистка и резервные копии)"
cat > /etc/cron.d/oblik <<CRON
30 3 * * * oblik cd $APP && /usr/bin/python3 cleanup.py >> /opt/oblik/cleanup.log 2>&1
45 3 * * * oblik cd $APP && /usr/bin/python3 deploy/backup.py >> /opt/oblik/backup.log 2>&1
CRON
chmod 644 /etc/cron.d/oblik

if [ -f "$APP/server.py" ]; then
  systemctl restart oblik
  echo "==> Приложение запущено"
else
  echo "!! Файлов сайта ещё нет: загрузите их скриптом deploy.sh, он же перезапустит службу."
fi

echo
echo "Готово. Дальше, когда DNS домена уже указывает на этот сервер, выпустите сертификат:"
echo "  certbot --nginx -d $DOMAIN -d www.$DOMAIN -m $EMAIL --agree-tos --no-eff-email --redirect"
