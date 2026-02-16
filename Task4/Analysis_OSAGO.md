# Task 4. Проектирование продажи ОСАГО — Анализ и обоснование решений

## Контекст

InsureTech запускает новый продукт: онлайн-оформление ОСАГО. Клиент заполняет заявку, система запрашивает предложения у 10 страховых компаний, предложения отображаются **в реальном времени** по мере поступления. Максимальное время ожидания — 60 секунд. Пиковая нагрузка — 2 500 одновременных пользователей.

Страховые компании предоставляют REST API с двумя эндпоинтами: создать заявку (POST) и получить предложение (GET).

---

## Решение 1. Реализация osago-aggregator

Выделен отдельный сервис **osago-aggregator** (Kotlin, Spring Boot, JDK 25).

Функциональность:
- **Fan-out:** при получении заявки от core-app параллельно отправляет POST-запросы во все 10 страховых компаний.
- **Polling:** для каждой компании запускает цикл опроса (GET каждые 2–3 секунды) до получения решения или истечения 60-секундного таймаута.
- **Fan-in:** по мере поступления ответов публикует события `osago.proposal.received` в Kafka.
- **Завершение:** после ответа всех компаний (или таймаута) публикует `osago.proposals.complete`.

Параллельность обеспечивается **Virtual Threads** (JDK 25 LTS). При 2 500 пользователях × 10 компаний = 25 000 in-flight запросов — Virtual Threads масштабируются без проблем (килобайт стека вместо мегабайта у platform thread). Spring Boot 3.2+ поддерживает Virtual Threads нативно (`spring.threads.virtual.enabled=true`).

---

## Решение 2. Хранилище данных osago-aggregator

**Выбор: Redis** (а не PostgreSQL).

osago-aggregator хранит состояние ОСАГО-сессий: какие компании получили заявку, какие ответили, какие в процессе, результаты.

Обоснование выбора Redis:
- **Ephemeral data.** Сессия живёт максимум 60 секунд, после чего данные не нужны. Встроенный TTL (5 мин с запасом) — автоматическая очистка.
- **Высокая частота обновлений.** 10 компаний × polling каждые 2–3 сек × 2 500 пользователей ≈ до 8 300 операций/сек. Для Redis это тривиальная нагрузка.
- **Простая модель данных.** Key-value, не нужны JOIN, транзакции, миграции схемы.
- **Восстановление при рестарте.** При перезапуске osago-aggregator — незавершённые сессии восстанавливаются из Redis (пока TTL не истёк).

---

## Решение 3. API osago-aggregator → core-app

**core-app → osago-aggregator:** REST (синхронный POST).

```
POST /api/v1/osago/applications
Body: { "carInfo": {...}, "driverInfo": {...}, "applicationId": "abc-123" }
Response: 202 Accepted
```

core-app отправляет заявку и **не ждёт результатов** (fire-and-forget). Результаты придут асинхронно.

**osago-aggregator → core-app:** Kafka (асинхронный, Event-Driven).

Топики:
- `osago.proposal.received` — событие с предложением от одной СК.
- `osago.proposals.complete` — все компании ответили (или таймаут).

Обоснование выбора Kafka (а не REST callback / WebHook):
- **Согласованность с архитектурой.** В Task 3 мы уже внедрили Kafka для `products.updated` и `insurance.issued`. Единый брокер для всех асинхронных взаимодействий.
- **Буферизация.** Если core-app временно недоступен — события сохраняются в Kafka и будут обработаны при восстановлении.
- **Масштабирование.** При появлении новых потребителей (аналитика, уведомления) — новый consumer group, нагрузка на osago-aggregator не растёт.

---

## Решение 4. API для Web-приложения (core-app → InsureTech Web)

**Выбор: SSE (Server-Sent Events).**

Бизнес-требование: предложения от страховых компаний должны появляться на экране **сразу, как только пришёл ответ**. REST polling не обеспечивает такой UX.

Реализация:
```
1. Web → core-app:  POST /api/osago/applications         → 202 Accepted {"applicationId": "abc-123"}
2. Web → core-app:  GET  /api/osago/applications/abc-123/proposals
                     Accept: text/event-stream            → 200 OK (SSE-поток)
3. core-app пушит события по мере получения из Kafka:
   event: proposal
   data: {"companyId": "alpha", "price": 12500, ...}
   
   event: complete
   data: {"total": 10}
```

Обоснование SSE (а не WebSocket):
- **Однонаправленность достаточна.** Клиент отправляет заявку одним POST. После этого только получает предложения — двусторонний канал не нужен.
- **Простота инфраструктуры.** SSE работает поверх HTTP — стандартные балансировщики, Ingress, прокси поддерживают без настройки. WebSocket потребовал бы sticky sessions.
- **Встроенный reconnect.** `EventSource` API в браузере автоматически переподключается при обрыве.
- **Конечный поток.** Максимум 10 событий за 60 секунд — SSE идеален для bounded streams.

Реализация в Spring Boot: `SseEmitter` или реактивный `Flux<ServerSentEvent>`.

---

## Решение 5. Паттерны отказоустойчивости

### Timeout

| Связь | Значение | Обоснование |
|:---|:---|:---|
| osago-aggregator → СК (polling-сессия) | 60 сек | Бизнес-требование |
| osago-aggregator → СК (один HTTP-запрос) | connect 3 сек, read 10 сек | Защита от зависания одного запроса |
| core-app → osago-aggregator (POST заявки) | 5 сек | Это fire-and-forget, ответ — только 202 |
| ins-product-aggregator → СК | 30 сек | Сбор тарифов по расписанию, некритично |

### Retry

osago-aggregator → страховые компании:
- До 3 попыток при transient errors (503, 429, connection timeout).
- Exponential backoff + jitter (1 сек → 2 сек → 4 сек).
- Общий бюджет retry укладывается в 60-секундный таймаут сессии.
- Только для идемпотентных операций (GET polling — всегда; POST заявки — с idempotency key).

### Circuit Breaker

osago-aggregator → каждая страховая компания (отдельный Circuit Breaker на каждую):
- Failure rate threshold: 50%.
- Sliding window: 10 вызовов.
- Wait duration in OPEN: 30 сек.
- Fallback: при OPEN — пропускаем компанию (клиент видит предложения от остальных 9).

Смысл: если «Компания Гамма» легла, не тратить 60 секунд × 2 500 пользователей на заведомо мёртвый API. Circuit Breaker мгновенно отклоняет запросы, освобождая ресурсы.

### Rate Limiter

core-app — входящий API от систем партнёров:
- **Nginx (L7):** грубый лимит по IP — защита от DDoS и аномалий (100 req/sec).
- **Application-level:** Bucket4j + Redis — бизнес-лимиты по API key партнёра (например, 100 req/min для партнёра X, 500 req/min для партнёра Y). Redis обеспечивает глобальный счётчик для кластера из N нод.

---

## Размещение паттернов на C4-диаграмме

| Стрелка | Паттерны |
|:---|:---|
| osago-aggregator → Системы страховых компаний | ⏱ Timeout 60s, 🔄 Retry 3x, 🔒 Circuit Breaker |
| core-app → osago-aggregator | ⏱ Timeout 5s |
| Системы партнёров → core-app | 📊 Rate Limiter |
| ins-product-aggregator → Системы страховых компаний | ⏱ Timeout 30s |

---

## Итоговый список новых компонентов

| Компонент | Тип | Технология | Назначение |
|:---|:---|:---|:---|
| osago-aggregator | Container (сервис) | Kotlin, Spring Boot, JDK 25, Virtual Threads | Fan-out/Fan-in заявок ОСАГО |
| osago-state | ContainerDb | Redis | Состояние ОСАГО-сессий (ephemeral) |
| osago.proposal.received | Kafka topic | — | Предложение от одной СК |
| osago.proposals.complete | Kafka topic | — | Завершение сессии ОСАГО |
| SSE-поток | Интеграция | text/event-stream | Realtime-доставка предложений на UI |

## Итоговый список новых связей

| Источник | Назначение | Протокол | Описание |
|:---|:---|:---|:---|
| core-app | osago-aggregator | REST (POST) | Создание заявки ОСАГО |
| osago-aggregator | Страховые компании | REST (POST + GET) | Fan-out заявок + Polling решений |
| osago-aggregator | Kafka | Async | Publish: proposal.received, proposals.complete |
| Kafka | core-app | Async | Consume: proposal.received, proposals.complete |
| core-app | InsureTech Web | SSE | Realtime-стрим предложений ОСАГО |
| osago-aggregator | Redis | TCP/6379 | Состояние сессий |
