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


TERMS_OF_SERVICE_HTML = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>LeadArmor Bot — Условия использования</title>
</head>
<body style="font-family: sans-serif; max-width: 720px; margin: 40px auto; line-height: 1.5;">
<h1>Условия использования LeadArmor Bot</h1>

<h2>Что делает сервис</h2>
<p>LeadArmor Bot — инструмент для владельцев Instagram бизнес-аккаунтов. По явному
указанию владельца аккаунта сервис отслеживает комментарии под выбранными им
публикациями, отвечает на них в Instagram Direct и передаёт полученные контакты
владельцу в Telegram.</p>

<h2>Кто может пользоваться</h2>
<p>Сервисом пользуется владелец Instagram бизнес-аккаунта, самостоятельно
подключивший свой аккаунт и предоставивший необходимые разрешения. Подключая
аккаунт, владелец подтверждает, что имеет право управлять им.</p>

<h2>Обязанности пользователя</h2>
<ul>
<li>Не использовать сервис для рассылки спама или сообщений без интереса получателя</li>
<li>Соблюдать правила платформы Instagram и применимое законодательство</li>
<li>Отвечать за содержание автоматических сообщений, которые он настраивает</li>
</ul>

<h2>Ограничение ответственности</h2>
<p>Сервис предоставляется «как есть». Мы не гарантируем непрерывную работу, так как
зависим от доступности Instagram Graph API и сторонних сервисов. Мы не несём
ответственности за упущенную выгоду от несработавшей автоматизации.</p>

<h2>Прекращение использования</h2>
<p>Владелец аккаунта может в любой момент отключить сервис, отозвав доступ в настройках
Instagram или написав на {CONTACT_EMAIL}. Мы можем прекратить обслуживание при
нарушении этих условий.</p>

<h2>Контакты</h2>
<p>{CONTACT_EMAIL}</p>
</body>
</html>
"""

DATA_DELETION_HTML = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>LeadArmor Bot — Удаление данных</title>
</head>
<body style="font-family: sans-serif; max-width: 720px; margin: 40px auto; line-height: 1.5;">
<h1>Как удалить свои данные из LeadArmor Bot</h1>

<h2>Если вы владелец подключённого Instagram-аккаунта</h2>
<p>Отключите приложение LeadArmor в настройках Instagram
(Настройки → Безопасность → Приложения и сайты), либо напишите на {CONTACT_EMAIL}
с указанием username вашего аккаунта. После этого мы удалим access-токен вашего
аккаунта, список отслеживаемых публикаций и все собранные по ним лиды.</p>

<h2>Если вы оставили комментарий или писали бизнесу в Direct</h2>
<p>Напишите на {CONTACT_EMAIL}, указав ваш Instagram username и, по возможности,
название бизнес-аккаунта, с которым вы взаимодействовали. Мы удалим ваш username,
Instagram user ID, текст сообщения и номер телефона, если он был оставлен.</p>

<h2>Сроки</h2>
<p>Запросы обрабатываются в течение 30 дней. После удаления данные не подлежат
восстановлению.</p>

<h2>Контакты</h2>
<p>{CONTACT_EMAIL}</p>
</body>
</html>
"""


async def privacy_policy_handler(request: web.Request) -> web.Response:
    return web.Response(text=PRIVACY_POLICY_HTML, content_type="text/html")


async def terms_of_service_handler(request: web.Request) -> web.Response:
    return web.Response(text=TERMS_OF_SERVICE_HTML, content_type="text/html")


async def data_deletion_handler(request: web.Request) -> web.Response:
    return web.Response(text=DATA_DELETION_HTML, content_type="text/html")
