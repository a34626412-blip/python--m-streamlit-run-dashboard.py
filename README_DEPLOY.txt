CHIPS Boost Live v12 — public deployment

Что изменено относительно v11:
- Добавлен запуск фонового scanner при импорте приложения, поэтому он работает под Gunicorn/Render.
- Flask слушает 0.0.0.0 и PORT из окружения.
- Добавлен gunicorn.
- Добавлен render.yaml для Render.
- Логика Boost/API v11 сохранена: API карусели, только view=boosted_odds,
  объединение по event_id, максимальный multiplier, исключение начавшихся событий,
  название матча, время и кнопка ставки.
- Страница chips.gg/sports/ самим приложением не открывается.

Локально:
  run.bat

Render:
1. Создать GitHub repository.
2. Загрузить все файлы из этого архива в repository.
3. Render -> New -> Web Service.
4. Подключить repository.
5. Runtime: Python.
6. Build Command: pip install -r requirements.txt
7. Start Command:
   gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 30
8. Plan: Free.
9. Health Check Path: /api/state.
10. Deploy.

После deploy Render выдаст публичный адрес:
  https://<имя-сервиса>.onrender.com

Важно:
- Free web service может засыпать после периода без входящих запросов. При открытии страницы он просыпается.
- Для постоянного фонового сканирования 24/7 без посетителей нужен платный always-on сервис.
- Не запускайте несколько Gunicorn workers: каждый worker запустит свой scanner. В конфигурации намеренно стоит --workers 1.
