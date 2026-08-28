# ITMO schedule to Google Calendar sync

Сервис берет расписание с my.itmo.ru и синхронизирует его напрямую в Google Calendar через API.

Вместо `.ics` теперь используется односторонняя синхронизация:
- новые пары создаются как события Google Calendar;
- изменения расписания обновляют существующие события;
- удаленные пары удаляются из Google Calendar;
- если вы вручную удалили синхронизированное событие, сервис **не создаст его заново**;
- в описание каждого синхронизированного события добавляется `ITMO_SYNC_ID: ...`.

Информация о синхронизации хранится в PostgreSQL (`synced_events`), поэтому сервис знает, какие события уже обрабатывались.

## Что нужно

- Docker + Docker Compose
- `credentials.json` одного из форматов:
  - Service Account key JSON (`"type": "service_account"`), или
  - OAuth Authorized User JSON (`"type": "authorized_user"`), или
  - OAuth Client JSON (`"installed"` / `"web"`) + `ITMO_ICAL_GOOGLE_REFRESH_TOKEN`
- логин и пароль ИСУ
- ID календаря Google (`ITMO_ICAL_GOOGLE_CALENDAR_ID`)

## Быстрый запуск

1. Создайте `.env` рядом с `docker-compose.yml`:

```env
ITMO_ICAL_ISU_USERNAME=100000
ITMO_ICAL_ISU_PASSWORD=XXXXXXXXXXXXX
ITMO_ICAL_GOOGLE_CALENDAR_ID=primary
ITMO_ICAL_DATABASE_URL=<postgres-connection-url>
ITMO_ICAL_GOOGLE_REFRESH_TOKEN=<refresh-token> # только для credentials.json с "installed"/"web"
```

2. Положите `credentials.json` в корень проекта.

3. Поднимите сервис:

```bash
docker compose up -d --build
```

4. Получите путь синхронизации:

```bash
SYNC_PATH=$(docker logs $(docker compose ps -q app) 2>&1 | grep -oh '/sync/.*' | tail -n 1)
HOST_IP=$(curl -s ipinfo.io/ip)
echo "http://$HOST_IP:35601$SYNC_PATH"
```

5. Вызовите URL (`GET` или `POST`) для запуска синхронизации.

Пример ответа:

```json
{
  "created": 3,
  "deleted": 1,
  "skipped_manual_delete": 2,
  "source_events": 42,
  "unchanged": 34,
  "updated": 2
}
```

## Разработка

Используются:
- Python 3.11 + poetry
- ruff, black, mypy, vulture
