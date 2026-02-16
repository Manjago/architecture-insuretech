# Проектная работа: Архитектура InsureTech (Sprint 8)

Репозиторий содержит архитектурные решения, диаграммы и конфигурации для обеспечения масштабируемости и отказоустойчивости системы **InsureTech**.

**Роль:** Senior System Architect / SRE
**Цель:** Трансформация MVP-монолита в Highload-систему, устойчивую к нагрузкам и сбоям.
**Инструменты моделирования:** PlantUML (C4 Model).

---

## 📚 Навигация по заданиям

| Задание | Тема | Статус | Артефакты |
| :--- | :--- | :--- | :--- |
| **Task 1** | **Технологическая архитектура (Deployment)** | ✅ Готово | [Схема To-Be](Task1/InsureTech_Deployment_To-Be.png), [ADR](Task1/ADR_Architecture.md) |
| **Task 2** | **Динамическое масштабирование (K8s HPA)** | ✅ Готово | [Манифесты](Task2/), [Скриншоты](Task2/screenshots/) |
| **Task 3** | **Event-Driven Архитектура (EDA)** | ✅ Готово | [Анализ проблем](Task3/Analysis.md), [C4 To-Be](Task3/InsureTech_C4_EDA.png) |
| **Task 4** | **Проектирование продажи ОСАГО** | ✅ Готово | [Анализ решений](Task4/Analysis_OSAGO.md), [C4 ОСАГО](Task4/InsureTech_C4_OSAGO.png) |
| **Task 5** | **Проектирование GraphQL API** | ✅ Готово | [GraphQL Schema](Task5/schema.graphql) |
| **Task 6** | **Настройка Rate Limiting (Nginx)** | ✅ Готово | [nginx.conf](Task6/nginx.conf) |

---

## 🛠 Детальное описание решений

### Task 1. Проектирование технологической архитектуры

**Проблема (As-Is):**
Текущая инсталляция представляет собой единую точку отказа (SPOF). Все компоненты (приложения и БД) находятся в одной зоне доступности на одной виртуальной машине. При падении зоны сервис становится полностью недоступен. Пользователи из регионов жалуются на долгую загрузку.

**Решение (To-Be):**
Разработана отказоустойчивая архитектура **Single Region, Multi-AZ** (Yandex Cloud `ru-central1`).

*   **High Availability:** Кластер Kubernetes распределен по 3 зонам доступности (A, B, C). Отказ одной зоны не останавливает обслуживание.
*   **База данных:** Кластер PostgreSQL в конфигурации Master + 2 Replicas (Sync/Async). Обеспечивает RPO ≈ 0 и RTO < 5 мин.
*   **География:** Внедрен CDN для кэширования статического контента (JS, CSS, Images) на Edge-серверах по всей РФ.

**Артефакты:**
1.  🖼 **[Диаграмма развертывания (To-Be)](Task1/InsureTech_Deployment_To-Be.png)**
2.  📝 **[ADR: Обоснование архитектурного решения](Task1/ADR_Architecture.md)** — анализ требований RTO/RPO и SLA 99.9%.
3.  ⚙️ *Исходный код диаграммы:* [`Task1/InsureTech_Deployment_To-Be.puml`](Task1/InsureTech_Deployment_To-Be.puml)

---

### Task 2. Динамическое масштабирование контейнеров

**Проблема:**
При пиковых нагрузках система не справляется и перезагружает поды из-за нехватки памяти. Держать запас реплик постоянно — экономически невыгодно.

**Решение:**
Настроено автоматическое масштабирование через Horizontal Pod Autoscaler (HPA) в двух режимах.

*   **Часть 1 — HPA по памяти:** При утилизации > 80% от requests (20Mi) HPA автоматически увеличивает количество подов (до 10). Под нагрузкой утилизация достигала 109%, HPA поднял дополнительные реплики, утилизация стабилизировалась на ~66%.
*   **Часть 2★ — HPA по RPS (custom metrics):** Через цепочку Prometheus → Prometheus Adapter → Custom Metrics API метрика `http_requests_per_second` (rate от `http_requests_total`) доставляется в HPA. При > 5 RPS на под HPA масштабирует Deployment. Под нагрузкой ~660 RPS на под HPA вывел кластер на максимальные 10 реплик.

**Нагрузочное тестирование:** Locust, 200 виртуальных пользователей, пиковый RPS ~1200.

**Артефакты:**
1.  ⚙️ [`Task2/deployment.yaml`](Task2/deployment.yaml) — Deployment (1 реплика, limit 30Mi)
2.  ⚙️ [`Task2/service.yaml`](Task2/service.yaml) — Service (NodePort)
3.  ⚙️ [`Task2/hpa-memory.yaml`](Task2/hpa-memory.yaml) — HPA по памяти (часть 1)
4.  ⚙️ [`Task2/hpa-rps.yaml`](Task2/hpa-rps.yaml) — HPA по RPS (часть 2★)
5.  ⚙️ [`Task2/prometheus-adapter-values.yaml`](Task2/prometheus-adapter-values.yaml) — Конфигурация Prometheus Adapter
6.  🐍 [`Task2/locustfile.py`](Task2/locustfile.py) — Скрипт нагрузочного тестирования
7.  📸 [`Task2/screenshots/`](Task2/screenshots/) — Скриншоты масштабирования и метрик

---

### Task 3. Переход на Event-Driven архитектуру

**Проблема (As-Is):**
Взаимодействие между сервисами построено на синхронном REST и polling. При росте числа страховых компаний с 5 до 10 — линейный рост латентности, каскадные отказы, устаревшие данные (до 15 мин для тарифов, до 24 ч для страховок). Суточный batch-запрос за оформленными страховками хрупок при росте объёмов.

**Решение (To-Be):**
Внедрение **Event-Driven Architecture** с Apache Kafka. Три взаимодействия переведены с REST polling на Event Streaming:

*   **`products.updated` (Event-Carried State Transfer):** `ins-product-aggregator` по расписанию собирает тарифы из 10 компаний и публикует событие в Kafka. `core-app` и `ins-comp-settlement` подписаны — обновляют локальные реплики в near real-time (вместо polling каждые 15 мин / раз в сутки).
*   **`insurance.issued` (ECST + Transactional Outbox):** `core-app` при оформлении страховки публикует событие через Transactional Outbox (атомарно с записью в БД). `ins-comp-settlement` получает данные в real-time (вместо суточного batch REST-запроса).

**Transactional Outbox:** Применяется для `core-app` → `InsuranceIssued`. Событие пишется в outbox-таблицу в `core-db` в одной транзакции с бизнес-данными. Polling Publisher (1 сек) отправляет в Kafka. Consumer (`ins-comp-settlement`) идемпотентен.

**Остаётся синхронным (REST):** Пользовательские запросы (Web → core-app), CRUD клиентских данных (Web/core-app → client-info), внешние API страховых компаний, оплата.

**Артефакты:**
1.  📝 **[Анализ проблем и рисков](Task3/Analysis.md)** — 5 проблем с оценкой приоритетов и предлагаемыми решениями.
2.  🖼 **[Обновлённая C4-диаграмма контейнеров (To-Be)](Task3/InsureTech_C4_EDA.png)** — Event-Driven архитектура с Kafka, Outbox, ECST.
3.  ⚙️ *Исходный код диаграммы:* [`Task3/InsureTech_C4_EDA.puml`](Task3/InsureTech_C4_EDA.puml)

---

### Task 4. Проектирование продажи ОСАГО

**Задача:**
Запуск нового продукта — онлайн-оформление ОСАГО. Клиент заполняет заявку, система запрашивает предложения у 10 страховых компаний, предложения отображаются **в реальном времени** по мере поступления. Максимальное время ожидания — 60 секунд. Пиковая нагрузка — 2 500 одновременных пользователей.

**Решение (To-Be):**
Выделен новый сервис **osago-aggregator**, реализующий паттерн **Fan-out / Fan-in** с progressive-доставкой результатов через **SSE** и **Kafka**.

*   **osago-aggregator** (Kotlin, Spring Boot, JDK 25, Virtual Threads): параллельно отправляет заявки в 10 страховых компаний (fan-out), опрашивает решения (polling, до 60 сек), публикует предложения в Kafka по мере поступления (progressive fan-in).
*   **Хранилище:** Redis (ephemeral state, TTL 5 мин) — состояние ОСАГО-сессий: какие компании ответили, какие в процессе, результаты.
*   **Интеграция osago-aggregator ↔ core-app:** REST (POST — создание заявки) + Kafka (асинхронно — результаты через топики `osago.proposal.received`, `osago.proposals.complete`).
*   **Интеграция core-app ↔ Web:** SSE (Server-Sent Events) — предложения отображаются на экране по мере поступления. Поток конечный (≤10 событий, ≤60 сек), встроенный reconnect через EventSource API.
*   **Virtual Threads (JDK 25 LTS):** обеспечивают масштабирование до 25 000 in-flight HTTP-запросов (2 500 пользователей × 10 компаний) без исчерпания пула потоков.

**Паттерны отказоустойчивости:**

| Паттерн | Где применяется | Параметры |
| :--- | :--- | :--- |
| ⏱ **Timeout** | osago-aggregator → СК | 60 сек (бизнес-требование) |
| ⏱ **Timeout** | core-app → osago-aggregator | 5 сек (fire-and-forget) |
| 🔄 **Retry** | osago-aggregator → СК | До 3 попыток, exp. backoff + jitter |
| 🔒 **Circuit Breaker** | osago-aggregator → каждая СК | Threshold 50%, window 10, wait 30 сек |
| 📊 **Rate Limiter** | core-app ← партнёры | Nginx L7 + Bucket4j/Redis |

**Артефакты:**
1.  📝 **[Анализ решений и обоснования](Task4/Analysis_OSAGO.md)** — 5 решений: реализация агрегатора, Redis, API, SSE, паттерны.
2.  🖼 **[Обновлённая C4-диаграмма контейнеров (ОСАГО)](Task4/InsureTech_C4_OSAGO.png)** — osago-aggregator, Redis, SSE, Kafka-топики, обозначения паттернов.
3.  ⚙️ *Исходный код диаграммы:* [`Task4/InsureTech_C4_OSAGO.puml`](Task4/InsureTech_C4_OSAGO.puml)

---

### Task 5. Проектирование GraphQL API

**Проблема:**
Сервис `client-info` хранит карточку клиента с **500 атрибутами**. Потребители (Web, core-app) в разных сценариях (витрина, оформление страховки, ОСАГО, взаиморасчёты) требуют абсолютно разные наборы данных. REST API предоставляет три отдельных эндпоинта (`/clients/{id}`, `/clients/{id}/documents`, `/clients/{id}/relatives`), что приводит к:

*   **Overfetching** — каждый эндпоинт возвращает все поля, даже если нужны 2–3.
*   **Underfetching** — для одного сценария требуется 3 отдельных запроса (3× RPS на client-info).

**Решение:**
Перевод REST API сервиса `client-info` на **GraphQL**.

*   **Один query вместо трёх эндпоинтов.** `client(id: ID!)` возвращает клиента с возможностью запросить связанные `documents` и `relatives` в одном запросе.
*   **Клиент выбирает поля.** Из 500 атрибутов запрашиваются только нужные в конкретном сценарии — нет лишних данных, нет лишних запросов.
*   **Resolvers по требованию.** Resolver для `documents` / `relatives` выполняется (и обращается к БД) только если поле запрошено клиентом.
*   **Горизонтальное масштабирование.** GraphQL-сервер stateless — работает за балансировщиком так же, как REST. Масштабирование не зависит от количества экземпляров.

**Артефакты:**
1.  📝 **[GraphQL Schema](Task5/schema.graphql)** — типы (Client, Document, Relative) и query с примерами запросов для разных сценариев.

---

### Task 6. Настройка Rate Limiting (Nginx)

**Проблема:**
Один из партнёров генерирует аномально высокий трафик, снижая производительность API для остальных. Динамическое масштабирование (Task 2) помогает, но ресурсы не бесконечны — нужна защита на уровне инфраструктуры.

**Решение:**
Настроен **Rate Limiting** в Nginx с помощью директивы `limit_req`.

*   **Лимит:** 10 запросов в минуту на IP-адрес (`rate=10r/m`).
*   **Burst:** 5 дополнительных запросов сверх лимита обрабатываются с задержкой (leaky bucket), сглаживая кратковременные всплески.
*   **Превышение лимита:** HTTP **429 Too Many Requests** (вместо дефолтного 503).
*   **Алгоритм:** Leaky Bucket — запросы сверх лимита ставятся в очередь (до burst), при переполнении — отклоняются с 429.

**Артефакты:**
1.  ⚙️ **[nginx.conf](Task6/nginx.conf)** — конфигурация Nginx с Rate Limiting.

---
*Автор: Кирилл Темненков*
