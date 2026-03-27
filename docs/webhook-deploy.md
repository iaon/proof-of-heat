# Deploy через adnanh/webhook + nginx

Ниже описан минимальный процесс деплоя через **adnanh/webhook**. При входящем GitHub webhook будет выполняться:

```
git pull
docker compose up --build -d app
```

Основной сервис и webhook-контейнер разделены:
- `docker-compose.yml` содержит только `app`;
- `docker-compose.webhook.yml` содержит только `webhook`;
- `webhook` запускается вручную отдельной командой.

## 1. Webhook контейнер через Docker Compose

Файл `docker-compose.webhook.yml` содержит сервис `webhook`, который:
- слушает локальный порт `9000` (`127.0.0.1:9000`);
- читает хуки из `conf/webhook/hooks.yaml`;
- имеет доступ к репозиторию и Docker socket для выполнения команд;
- читает SSH-ключ из `conf/webhook/ssh/id_rsa` для `git pull` по `git@github.com:...`;
- монтирует репозиторий по тому же абсолютному пути, что и на хосте, чтобы `docker compose up ... app` из webhook-контейнера использовал корректные bind mounts.

Сервис запускается с `uid=1000`. Для доступа к `docker.sock` используется `group_add` через переменную `DOCKER_SOCKET_GID` (по умолчанию `992`, как в worker node). Если на целевом хосте GID группы Docker другой, переопределите эту переменную перед запуском `docker compose`.

Если путь к репозиторию на хосте отличается от `/home/iaon/git/proof-of-heat`, задайте `PROJECT_ROOT` перед запуском webhook:
```bash
PROJECT_ROOT=/absolute/path/to/proof-of-heat docker compose -f docker-compose.yml -f docker-compose.webhook.yml up --build -d webhook
```

Основной сервис запускается как обычно:
```bash
docker compose up --build -d app
```

Webhook запускается вручную отдельной командой:
```bash
docker compose -f docker-compose.yml -f docker-compose.webhook.yml up --build -d webhook
```

Конфиг хука расположен в `conf/webhook/hooks.yaml` и вызывает:
```
git pull && docker compose up --build -d app
```

Если нужно изменить команды или рабочую директорию — правьте этот файл.

## 1.1. SSH-ключ для `git pull`

Так как `origin` настроен как SSH remote (`git@github.com:...`), контейнеру `webhook` нужен приватный ключ.

1. Создайте локальную директорию для ключа:

```bash
mkdir -p conf/webhook/ssh
chmod 700 conf/webhook/ssh
```

2. Положите туда deploy key или другой SSH private key с доступом к репозиторию:

```bash
cp /path/to/id_rsa conf/webhook/ssh/id_rsa
chmod 600 conf/webhook/ssh/id_rsa
```

В `conf/webhook/ssh/.gitignore` уже настроено игнорирование содержимого директории, поэтому ключ не попадет в репозиторий.

Альтернатива: вместо файла можно передать ключ через переменную окружения `WEBHOOK_SSH_PRIVATE_KEY`, но файл-монтирование безопаснее и проще для эксплуатации.

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
