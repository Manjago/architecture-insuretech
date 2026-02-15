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
| **Task 3** | Event-Driven Архитектура (EDA) | ⏳ Ожидает | |
| **Task 4** | Отказоустойчивость (Resilience Patterns) | ⏳ Ожидает | |
| **Task 5** | Проектирование GraphQL API | ⏳ Ожидает | |
| **Task 6** | Настройка Rate Limiting (Nginx) | ⏳ Ожидает | |

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
*Автор: Кирилл Темненков*