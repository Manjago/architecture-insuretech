from locust import HttpUser, task, between


class ScaleTestUser(HttpUser):
    """
    Виртуальный пользователь для нагрузочного тестирования scaletestapp.
    Отправляет GET / для:
    - увеличения потребления памяти (триггер HPA по memory)
    - увеличения счётчика http_requests_total (триггер HPA по RPS)
    """
    wait_time = between(0.1, 0.3)

    @task
    def get_pod_id(self):
        """GET / — получение идентификатора пода."""
        self.client.get("/")
