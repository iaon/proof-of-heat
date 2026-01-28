# Woodpecker CI: деплой на сервер с `docker compose up --build`

Ниже описан минимальный процесс настройки **Woodpecker CI**, при котором при каждом пуше в ветку `main` на вашем сервере выполняется `docker compose up --build` и контейнеры продолжают работать до следующего обновления.

## Что нужно настроить

### 1. На GitHub

1. **Создайте OAuth приложение** для Woodpecker:
   - GitHub → Settings → Developer settings → OAuth Apps → New OAuth App.
   - **Homepage URL**: `https://<ваш-woodpecker-сервер>`
   - **Authorization callback URL**: `https://<ваш-woodpecker-сервер>/authorize`
2. Сохраните `Client ID` и `Client Secret` — они понадобятся для конфигурации Woodpecker.
3. Дайте Woodpecker доступ к репозиторию:
   - В UI Woodpecker зайдите в репозиторий и включите его.

### 2. На сервере

#### 2.1 Установите Docker и Docker Compose

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker <user>
```

Перезайдите в сессию или выполните `newgrp docker`, чтобы применить изменения.

#### 2.2 Поднимите Woodpecker Server и Agent

Пример `docker-compose.yml` для Woodpecker (можно держать отдельно от проекта):

```yaml
version: "3"
services:
  woodpecker-server:
    image: woodpeckerci/woodpecker-server:latest
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      WOODPECKER_OPEN: "true"
      WOODPECKER_HOST: "https://<ваш-woodpecker-сервер>"
      WOODPECKER_GITHUB: "true"
      WOODPECKER_GITHUB_CLIENT: "<GITHUB_CLIENT_ID>"
      WOODPECKER_GITHUB_SECRET: "<GITHUB_CLIENT_SECRET>"
      WOODPECKER_ADMIN: "<ваш_github_username>"
    volumes:
      - woodpecker-data:/var/lib/woodpecker

  woodpecker-agent:
    image: woodpeckerci/woodpecker-agent:latest
    restart: unless-stopped
    environment:
      WOODPECKER_SERVER: "woodpecker-server:9000"
      WOODPECKER_AGENT_SECRET: "<AGENT_SECRET>"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock

volumes:
  woodpecker-data:
```

> **Важно:** значение `WOODPECKER_AGENT_SECRET` нужно взять из логов `woodpecker-server` при первом запуске.

Запуск:

```bash
docker compose up -d
```

#### 2.3 Подготовьте директорию с проектом

Пример структуры на сервере:

```
/opt/proof-of-heat
```

Клонируйте репозиторий:

```bash
sudo mkdir -p /opt/proof-of-heat
sudo chown -R <user>:<user> /opt/proof-of-heat
cd /opt/proof-of-heat

git clone <repo_url> .
```

#### 2.4 Включите HTTPS через Nginx + Let's Encrypt

Woodpecker требует доступный **callback URL** снаружи, поэтому на сервере должны быть открыты порты `80` и `443`, а домен должен указывать на ваш сервер.

1. Скопируйте пример конфигурации Nginx и замените домен:

   - `conf/nginx/proof-of-heat.conf` → замените `<your-domain.example>` на ваш домен.

2. Запустите Nginx и Certbot вместе с приложением:

```bash
docker compose -f docker-compose.yml -f docker-compose.nginx.yml up -d
```

3. Выпустите первый сертификат:

```bash
docker compose -f docker-compose.yml -f docker-compose.nginx.yml run --rm certbot \\
  certonly --webroot -w /var/www/certbot \\
  -d <your-domain.example> \\
  --email <your-email@example.com> \\
  --agree-tos --no-eff-email
```

4. Перезапустите Nginx, чтобы он подхватил сертификат:

```bash
docker compose -f docker-compose.yml -f docker-compose.nginx.yml restart nginx
```

Certbot внутри Compose будет автоматически выполнять `renew` каждые 12 часов.

## Пример Woodpecker pipeline

Добавьте файл `.woodpecker.yml` в корень репозитория. Он будет подключаться к серверу по SSH и запускать `docker compose up --build -d`.

```yaml
when:
  event: [push]
  branch: [main]

targets:
  deploy:
    steps:
      - name: deploy
        image: appleboy/ssh-action:latest
        settings:
          host:
            from_secret: deploy_host
          username:
            from_secret: deploy_user
          port:
            from_secret: deploy_port
          key:
            from_secret: deploy_key
          script: |
            set -e
            cd /opt/proof-of-heat
            git fetch origin main
            git reset --hard origin/main
            docker compose up --build -d
```

### 3. Секреты Woodpecker

В UI Woodpecker добавьте секреты для репозитория:

- `deploy_host` — IP или домен сервера
- `deploy_user` — пользователь для SSH
- `deploy_port` — SSH порт (обычно `22`)
- `deploy_key` — **private key** для SSH (без пароля, если не используете SSH agent)

#### Как подготовить SSH-ключ

На вашей машине:

```bash
ssh-keygen -t ed25519 -C "woodpecker-deploy" -f ~/.ssh/woodpecker_deploy
```

Добавьте публичный ключ на сервер в `~/.ssh/authorized_keys`:

```bash
cat ~/.ssh/woodpecker_deploy.pub | ssh <user>@<host> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

Приватный ключ `~/.ssh/woodpecker_deploy` добавьте в секрет `deploy_key`.

## Что будет происходить при пуше в main

1. GitHub сообщает Woodpecker о событии `push`.
2. Woodpecker запускает pipeline.
3. Pipeline подключается к серверу по SSH.
4. На сервере выполняется:
   - `git reset --hard origin/main`
   - `docker compose up --build -d`
5. Контейнеры продолжают работать до следующего пуша в `main`.

## Рекомендации

- Если у вас есть `.env` файл на сервере, убедитесь, что он **не перезаписывается** при `git reset --hard`.
- Для приватных репозиториев настройте SSH-ключ/токен для `git` на сервере.
- Если нужен простой rollback, используйте `git reset --hard <commit>` и снова выполните `docker compose up -d`.
