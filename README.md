# Pratta Thailand — Bitrix24 Automation

## Деплой на Railway (5 минут)

1. Зайди на https://railway.app → создай аккаунт
2. Нажми "New Project" → "Deploy from GitHub repo"
3. Загрузи эти файлы в новый GitHub репозиторий
4. Railway автоматически задеплоит сервер
5. Скопируй URL сервера (например: https://pratta-auto.up.railway.app)

## Настройка переменных окружения

В Railway → твой проект → Variables добавь:

```
BITRIX_WEBHOOK = https://pratta.bitrix24.ru/rest/1/ВАШ_ТОКЕН
BOT_SECRET     = pratta2025
USER_ROP       = ID руководителя продаж
USER_BUH       = ID бухгалтера
USER_SKLAD     = ID кладовщика
USER_KOLER     = ID колеровщика
USER_LOGIST    = ID логиста
```

## Где найти ID пользователей?

CRM → Сотрудники → нажми на сотрудника → смотри URL:
https://pratta.bitrix24.ru/company/personal/user/5/ → ID = 5

## Подключение к Битрикс24

После деплоя отправь POST запрос:

```bash
curl -X POST https://ВАШ_URL/setup \
  -H "Content-Type: application/json" \
  -d '{"server_url": "https://ВАШ_URL"}'
```

Или открой https://ВАШ_URL/setup в браузере через Postman.

## API для бота

Переместить сделку:
```bash
curl -X POST https://ВАШ_URL/move \
  -H "Content-Type: application/json" \
  -H "X-Bot-Secret: pratta2025" \
  -d '{"deal_id": 123, "stage": "Квалификация", "pipeline": "Paint — Продажа краски"}'
```

## Что автоматизировано

- Новая сделка → задача менеджеру (15 мин) + эскалация РОПу
- Бот-стадии → уведомление через 24ч если нет ответа
- КП → задача 4ч + напоминания через 4ч и 8ч
- Оплата → контроль + эскалация при просрочке
- Колеровка → задачи кладовщику и колеровщику
- Закрыто → причина отказа + повторный контакт через 30 дней
- И многое другое по каждой стадии всех воронок
