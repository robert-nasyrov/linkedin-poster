import asyncio
import os
import json
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import User, Chat, Channel
from anthropic import Anthropic
import httpx
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from digest_db import save_daily_summary, save_life_context

# =====================
# КОНФИГУРАЦИЯ
# =====================

API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MY_USER_ID = int(os.environ.get("MY_USER_ID", "271065518"))

# Service Account для личного календаря
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

# OAuth для рабочего календаря
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")

# Календари: личный через Service Account, рабочий через OAuth
PERSONAL_CALENDAR_ID = os.environ.get("PERSONAL_CALENDAR_ID", "primary")
WORK_CALENDAR_ID = os.environ.get("WORK_CALENDAR_ID", "robert@karakoram.co")

# Контекст для Claude
SYSTEM_PROMPT = """Ты – персональный ассистент Роберта. Анализируешь его Telegram переписки и календарь.

ПРОЕКТЫ РОБЕРТА:
1. TrabajaYa – рекрутинговая платформа, чат-боты. Ключевые люди: Мария (PM). Коммуникация в Slack, Telegram только срочное.
2. Plan Banan – детская анимация. Ключевые: Вадим (сценарии), Ирода (анимация), Стас (звук), Камила (озвучка), Махинур (перевод), Нигина (публикации), Саня (инвестор), Слава (таргет).
3. ZBS News – медиа, новости. Ключевые: Вадим, Даня (монтаж), Виктория (тексты), Ратмир (рэп-новости), Лазиза (блогеры), Сусанна (финансы). Блогеры: Джохейна, Ронин, Саброна, Алеш, Камила, Изи Йода.
4. Музыка – треки с Серёгой, дистрибуция через Каму.

ЦЕЛИ 2026:
- Доход $3-5K/мес
- Поездка в Китай апрель-май ($2K)
- MacBook Pro M6 ($3.5K)
- iPhone ($2K)

РОБЕРТ НЕ МИКРОМЕНЕДЖЕР. Ему важно:
- Видеть статус процессов
- Понимать, где без него не движется
- Получать ежедневный snapshot

ТВОЯ ЗАДАЧА – сделай утренний дайджест:

1. 📅 СЕГОДНЯ В КАЛЕНДАРЕ – встречи и события на сегодня с временем

2. 💡 РЕКОМЕНДАЦИИ – что подготовить к встречам, на что обратить внимание

3. 🚨 СРОЧНОЕ – процессы, которые стоят на Роберте. Что нужно сделать СЕГОДНЯ чтобы не тормозить других.

4. 📝 ДОГОВОРЁННОСТИ – кто что обещал, с дедлайнами
   Формат: [Имя] → [что] → [когда]

5. ⏳ ЖДЁМ ОТ ЛЮДЕЙ – особенно от блогеров и Стаса (забывает)
   Формат: [Имя] → [что ждём] → [сколько ждём]

6. 🔔 НАПОМНИТЬ – кому нужно напомнить (Стас, блогеры, другие)

7. ✅ ЗАКРЫТО – что завершено за сутки

8. 📊 СТАТУС ПРОЕКТОВ – кратко по ZBS News и Plan Banan

Формат: краткий, по делу, с именами. Без воды и Markdown.
ВАЖНО: НЕ используй звёздочки (**текст**), подчёркивания, решётки и другое форматирование. Telegram не поддерживает Markdown в HTML режиме. Используй только обычный текст и эмодзи.
Если по пункту ничего нет – пропусти его."""


def get_oauth_credentials():
    """Получает OAuth credentials для рабочего календаря"""
    if not GOOGLE_REFRESH_TOKEN:
        return None

    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=['https://www.googleapis.com/auth/calendar.readonly']
    )
    return creds


def get_service_account_credentials():
    """Получает Service Account credentials для личного календаря"""
    if not GOOGLE_CREDENTIALS:
        return None

    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    credentials = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=['https://www.googleapis.com/auth/calendar.readonly']
    )
    return credentials


def fetch_events_from_calendar(service, calendar_id, start_of_day, end_of_day, tz):
    """Получает события из одного календаря"""
    events = []
    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=start_of_day.isoformat(),
            timeMax=end_of_day.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        items = events_result.get('items', [])

        for event in items:
            start = event['start'].get('dateTime', event['start'].get('date'))
            if 'T' in start:
                event_time = datetime.fromisoformat(start.replace('Z', '+00:00'))
                time_str = event_time.astimezone(tz).strftime('%H:%M')
                sort_key = event_time
            else:
                time_str = 'Весь день'
                sort_key = start_of_day

            events.append({
                'time': time_str,
                'title': event.get('summary', 'Без названия'),
                'description': event.get('description', ''),
                'location': event.get('location', ''),
                'calendar': calendar_id,
                'sort_key': sort_key
            })

        print(f"   📅 {calendar_id}: {len(items)} событий")

    except Exception as e:
        print(f"   ⚠️ {calendar_id}: ошибка - {e}")

    return events


def get_calendar_events():
    """Получает события из личного и рабочего календарей"""
    all_events = []

    # Временной диапазон: сегодня (Ташкент UTC+5)
    tz = timezone(timedelta(hours=5))
    now = datetime.now(tz)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    # 1. Личный календарь через Service Account
    sa_creds = get_service_account_credentials()
    if sa_creds:
        try:
            service = build('calendar', 'v3', credentials=sa_creds)
            events = fetch_events_from_calendar(service, PERSONAL_CALENDAR_ID, start_of_day, end_of_day, tz)
            all_events.extend(events)
        except Exception as e:
            print(f"   ⚠️ Service Account ошибка: {e}")

    # 2. Рабочий календарь через OAuth
    oauth_creds = get_oauth_credentials()
    if oauth_creds:
        try:
            service = build('calendar', 'v3', credentials=oauth_creds)
            events = fetch_events_from_calendar(service, 'primary', start_of_day, end_of_day, tz)
            for e in events:
                e['calendar'] = WORK_CALENDAR_ID
            all_events.extend(events)
        except Exception as e:
            print(f"   ⚠️ OAuth ошибка: {e}")

    # Сортируем по времени
    all_events.sort(key=lambda x: x['sort_key'])

    # Убираем sort_key
    for event in all_events:
        del event['sort_key']

    return all_events


async def get_chats_data():
    """Собирает сообщения из личных чатов за 24 часа"""
    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH
    )
    await client.start()

    # Время: последние 24 часа
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    chats_data = []

    # Получаем диалоги
    async for dialog in client.iter_dialogs(limit=50):
        # Только личные чаты и небольшие группы
        if isinstance(dialog.entity, User) or (isinstance(dialog.entity, Chat) and dialog.entity.participants_count < 50):
            messages = []
            async for msg in client.iter_messages(dialog.entity, limit=100):
                if msg.date < since:
                    break
                if msg.text:
                    sender = "Я" if msg.out else dialog.name
                    messages.append({
                        "time": msg.date.strftime("%H:%M"),
                        "sender": sender,
                        "text": msg.text[:500]
                    })

            if messages:
                chats_data.append({
                    "chat_name": dialog.name,
                    "messages": list(reversed(messages))
                })

    await client.disconnect()
    return chats_data


def format_for_claude(chats_data, calendar_events):
    """Форматирует данные для анализа"""
    result = []

    # Календарь
    if calendar_events:
        result.append("=== КАЛЕНДАРЬ НА СЕГОДНЯ ===")
        for event in calendar_events:
            cal_label = "🏠" if event['calendar'] == PERSONAL_CALENDAR_ID else "💼"
            line = f"{cal_label} [{event['time']}] {event['title']}"
            if event['location']:
                line += f" ({event['location']})"
            result.append(line)
        result.append("")
    else:
        result.append("=== КАЛЕНДАРЬ НА СЕГОДНЯ ===")
        result.append("Нет запланированных событий")
        result.append("")

    # Переписки
    for chat in chats_data:
        result.append(f"=== ЧАТ: {chat['chat_name']} ===")
        for msg in chat['messages']:
            result.append(f"[{msg['time']}] {msg['sender']}: {msg['text']}")

    return "\n".join(result)


async def analyze_with_claude(messages_text, max_retries=3):
    """Анализирует сообщения через Claude с retry"""
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"Вот мои переписки за последние 24 часа и календарь на сегодня:\n\n{messages_text}\n\nСделай утренний дайджест."
                }]
            )
            return response.content[0].text
        except Exception as e:
            if "overloaded" in str(e).lower() or "529" in str(e):
                wait_time = 30 * (attempt + 1)
                print(f"⏳ API перегружен, жду {wait_time}с... (попытка {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
            else:
                raise e

    return "❌ Не удалось получить анализ – API перегружен. Попробуй запустить позже."


async def send_telegram(text):
    """Отправляет дайджест через бота"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "chat_id": MY_USER_ID,
            "text": text,
            "parse_mode": "HTML"
        })


async def main():
    print(f"🕐 Запуск дайджеста: {datetime.now()}")

    # Собираем данные из календаря
    print("📅 Получаю события из календарей...")
    calendar_events = get_calendar_events()
    print(f"   Всего: {len(calendar_events)} событий")

    # Собираем данные из Telegram
    print("⏳ Собираю сообщения из Telegram...")
    chats_data = await get_chats_data()
    print(f"📊 Найдено {len(chats_data)} активных чатов")

    if not chats_data and not calendar_events:
        await send_telegram("☁️ Утренний дайджест\n\nЗа последние сутки новых сообщений нет, календарь пуст.")
        return

    # Форматируем
    messages_text = format_for_claude(chats_data, calendar_events)

    # Анализируем
    print("⏳ Анализирую через Claude...")
    analysis = await analyze_with_claude(messages_text)

    # Убираем Markdown если Claude всё равно добавил
    analysis = analysis.replace("**", "").replace("__", "").replace("```", "")

    # Формируем дайджест
    today = datetime.now().strftime('%d.%m.%Y')
    digest = f"☁️ <b>УТРЕННИЙ ДАЙДЖЕСТ – {today}</b>\n\n{analysis}"

    # Отправляем
    print("⏳ Отправляю в Telegram...")
    await send_telegram(digest)

    # Сохраняем в базу для LinkedIn бота и других сервисов
    print("💾 Сохраняю в базу...")
    save_daily_summary(analysis)
    save_life_context(f"Digest {today}: {len(chats_data)} active chats, {len(calendar_events)} calendar events")

    print("✅ Готово!")


if __name__ == "__main__":
    asyncio.run(main())
