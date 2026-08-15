from __future__ import annotations

from aiohttp import web

from API.legal import CONTACT_EMAIL

LANDING_HTML = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LeadArmor — автоматический захват лидов из Instagram</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    max-width: 760px; margin: 0 auto; padding: 40px 20px;
    line-height: 1.6; color: #1c1e21;
  }}
  header {{ border-bottom: 1px solid #e4e6eb; padding-bottom: 24px; margin-bottom: 32px; }}
  h1 {{ margin: 0 0 8px; font-size: 32px; }}
  .tagline {{ color: #65676b; font-size: 18px; margin: 0; }}
  h2 {{ margin-top: 32px; font-size: 20px; }}
  ol, ul {{ padding-left: 20px; }}
  li {{ margin-bottom: 8px; }}
  footer {{
    border-top: 1px solid #e4e6eb; margin-top: 40px; padding-top: 20px;
    color: #65676b; font-size: 14px;
  }}
  footer a {{ color: #0866ff; margin-right: 16px; }}
</style>
</head>
<body>
<header>
  <h1>LeadArmor</h1>
  <p class="tagline">Автоматический захват заявок из комментариев Instagram</p>
</header>

<p>LeadArmor помогает бизнесу не терять клиентов, которые пишут «цена?» или «+»
под публикациями в Instagram. Пока менеджер занят, заявка остывает — бот отвечает
мгновенно, забирает контакт и передаёт его владельцу бизнеса.</p>

<h2>Как это работает</h2>
<ol>
  <li>Владелец Instagram-аккаунта подключает свой профиль и выбирает публикации,
      под которыми нужно ловить заявки</li>
  <li>Когда посетитель оставляет комментарий с ключевым словом, бот пишет ему
      в Instagram Direct и просит номер телефона</li>
  <li>Под рекламными публикациями комментарий скрывается, чтобы контакт клиента
      не достался конкурентам (по желанию владельца)</li>
  <li>Полученный номер сразу приходит владельцу в Telegram и записывается
      в его Google-таблицу</li>
</ol>

<h2>Для кого</h2>
<ul>
  <li>Автосалоны и автодилеры</li>
  <li>Агентства недвижимости</li>
  <li>Учебные центры и репетиторы</li>
  <li>Туристические компании</li>
  <li>Любой бизнес, который получает заявки в комментариях Instagram</li>
</ul>

<h2>Подключение</h2>
<p>Сервис работает через Telegram-бота. Чтобы подключить свой Instagram-аккаунт
или задать вопрос, напишите на <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p>

<footer>
  <a href="/legal/privacy">Политика конфиденциальности</a>
  <a href="/legal/terms">Условия использования</a>
  <a href="/legal/data-deletion">Удаление данных</a>
  <p>{CONTACT_EMAIL}</p>
</footer>
</body>
</html>
"""


async def landing_handler(request: web.Request) -> web.Response:
    return web.Response(text=LANDING_HTML, content_type="text/html")
