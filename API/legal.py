from __future__ import annotations

from aiohttp import web

CONTACT_EMAIL = "ismoilmirzoqosimov@gmail.com"

PRIVACY_POLICY_HTML = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>LeadArmor Bot — Политика конфиденциальности</title>
</head>
<body style="font-family: sans-serif; max-width: 720px; margin: 40px auto; line-height: 1.5;">
<h1>Политика конфиденциальности LeadArmor Bot</h1>

<p>LeadArmor Bot — сервис для бизнеса, который перехватывает и обрабатывает лиды из
комментариев и Direct-сообщений Instagram по заказу подключённого бизнес-клиента
(владельца Instagram-аккаунта).</p>

<h2>Какие данные мы собираем</h2>
<ul>
<li>Instagram user ID и username автора комментария/сообщения</li>
<li>Текст комментария или сообщения в Direct</li>
<li>Номер телефона, если пользователь сам оставил его в переписке с бизнес-аккаунтом</li>
<li>Access-токен подключённого Instagram-аккаунта бизнес-клиента (для вызова Instagram Graph API от его имени)</li>
</ul>

<h2>Как мы используем эти данные</h2>
<p>Данные используются исключительно для того, чтобы уведомить менеджера подключённого
бизнес-клиента в Telegram о новом лиде и дать ему возможность связаться с этим
пользователем. Данные не продаются и не передаются третьим лицам, не связанным
с обработкой этого лида.</p>

<h2>Хранение</h2>
<p>Данные хранятся в базе данных PostgreSQL, привязанной к конкретному бизнес-клиенту,
пока действует его подписка на сервис.</p>

<h2>Удаление данных</h2>
<p>Чтобы запросить удаление своих данных, напишите на {CONTACT_EMAIL}.</p>

<h2>Контакты</h2>
<p>{CONTACT_EMAIL}</p>
</body>
</html>
"""


async def privacy_policy_handler(request: web.Request) -> web.Response:
    return web.Response(text=PRIVACY_POLICY_HTML, content_type="text/html")
