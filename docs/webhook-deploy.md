# Deploy через adnanh/webhook + nginx

Ниже описан минимальный процесс деплоя через **adnanh/webhook**. При входящем GitHub webhook будет выполняться:

```
git pull
docker compose up --build -d
```

## 1. Webhook контейнер через Docker Compose

В текущем `docker-compose.yml` уже добавлен сервис `webhook`, который:
- слушает локальный порт `9000` (`127.0.0.1:9000`);
- читает хуки из `conf/webhook/hooks.yaml`;
- имеет доступ к репозиторию и Docker socket для выполнения команд.

Конфиг хука расположен в `conf/webhook/hooks.yaml` и вызывает:
```
git pull && docker compose up --build -d
```

Если нужно изменить команды или рабочую директорию — правьте этот файл.

## 2. Конфигурация nginx

Webhook слушает только локально, наружу его проксирует nginx через **секретный путь** и фильтрацию по IP GitHub. Готовая `location` секция:

```
location = /hooks/<SECRET_PATH>/deploy {
    allow 192.30.252.0/22;
    allow 185.199.108.0/22;
    allow 140.82.112.0/20;
    allow 143.55.64.0/20;
    allow 2a0a:a440::/29;
    allow 2606:50c0::/32;
    deny all;

    proxy_pass http://127.0.0.1:9000/hooks/deploy;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Замените `<SECRET_PATH>` на свой секрет (например, `very-secret-token`), после чего укажите этот путь в настройках GitHub webhook.

> Диапазоны `allow` взяты из `https://api.github.com/meta` (ключ `hooks`) — проверяйте их актуальность при необходимости.
