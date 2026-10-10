"""
Бот для Telegram-канала «Нейросети на практике».

Что делает за один запуск:
1. Собирает свежие новости об ИИ из RSS.
2. Выбирает рубрику по дню недели.
3. Просит нейросеть написать красивый пост.
4. Режим draft: присылает черновик вам в личку.
   Режим auto: публикует сразу в канал.
"""

import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path

import feedparser
import requests

# ───────────────────────── Настройки (из переменных окружения) ─────────────────────────

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]        # например @neuro_praktika
OWNER_ID = os.environ.get("TELEGRAM_OWNER_ID", "")    # ваш числовой id (для черновиков)
MODE = os.environ.get("MODE", "draft").lower()        # draft или auto
FOOTER = os.environ.get("CHANNEL_FOOTER", "")         # например: 📲 @neuro_praktika

PROVIDER = os.environ.get("LLM_PROVIDER", "groq").lower()   # groq, gemini или anthropic
GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip().strip('"').strip("'")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

STATE_FILE = Path(__file__).parent / "posted.json"

# Источники новостей. Можно добавлять свои RSS-ссылки.
FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://venturebeat.com/category/ai/feed/",
    "https://huggingface.co/blog/feed.xml",
    "https://openai.com/news/rss.xml",
]

# Темы для рубрики «Промпт дня» (крутятся по кругу).
PROMPT_TOPICS = [
    "составить план обучения на месяц",
    "написать резюме и сопроводительное письмо",
    "разобрать сложную тему простыми словами",
    "придумать идеи для контента в соцсетях",
    "подготовиться к собеседованию",
    "составить меню и список покупок на неделю",
    "улучшить и отредактировать текст",
    "проанализировать идею бизнеса",
    "перевести и адаптировать текст для другой аудитории",
    "создать чек-лист для любой рутинной задачи",
]

# ───────────────────────── Рубрики ─────────────────────────

BASE_RULES = """Ты редактор Telegram-канала «Нейросети на практике». Пиши по-русски, живо и просто, без воды и канцелярита.
Формат: Telegram HTML. Разрешены ТОЛЬКО теги <b>, <i>, <code>, <pre>, <a href="...">. Никакого Markdown, никаких звёздочек.
Используй уместные эмодзи, но не перегружай. Длина: до 900 символов.
Используй ТОЛЬКО факты из переданного материала. Не выдумывай цены, функции и цифры. Если чего-то не знаешь, не упоминай.
Верни только готовый текст поста, без пояснений и без обрамления в кавычки или ```."""

RUBRICS = {
    "news": """Рубрика: свежая новость.
Структура:
🔥 <b>Название сервиса или новости</b>

<b>Что это:</b> одно предложение
<b>Зачем:</b> главная польза для обычного человека
<b>Как попробовать:</b> 1-2 шага (если из материала понятно)

В конце строка: 🔗 <a href="ССЫЛКА">Источник</a>""",
    "tool": """Рубрика: «Инструмент недели» (понедельник). Разбери инструмент подробнее.
Структура:
🛠 <b>Инструмент недели: название</b>

<b>Что это:</b> 1-2 предложения
<b>Что умеет:</b> 3 коротких пункта через «•»
<b>Кому подойдёт:</b> одна строка

В конце строка: 🔗 <a href="ССЫЛКА">Источник</a>""",
    "prompt": """Рубрика: «Промпт дня» (среда). Материала-новости нет, придумай полезный готовый промпт.
Тема: {topic}.
Структура:
💬 <b>Промпт дня: короткое название задачи</b>

Скопируйте и вставьте в любую нейросеть:

<pre>сам промпт на русском, 3-6 строк, с местами для подстановки в [квадратных скобках]</pre>

<b>Что получите:</b> одно предложение""",
    "digest": """Рубрика: «Итоги недели» (пятница). Тебе дан список из нескольких новостей.
Структура:
📌 <b>Итоги недели в мире нейросетей</b>

Пять пунктов, каждый: эмодзи, <b>название</b> и одна короткая строка пользы. Если сервис бесплатный (и это явно указано в материале), добавь пометку 🆓.
В конце: «Сохраняйте пост, чтобы не потерять».""",
}


def rubric_for_today() -> str:
    weekday = dt.datetime.utcnow().weekday()  # 0 = понедельник
    return {0: "tool", 2: "prompt", 4: "digest"}.get(weekday, "news")


# ───────────────────────── Состояние (чтобы не повторяться) ─────────────────────────


def load_state() -> list:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def save_state(links: list) -> None:
    STATE_FILE.write_text(
        json.dumps(links[-500:], ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ───────────────────────── Сбор новостей ─────────────────────────


def clean(text: str, limit: int = 1500) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


def fetch_entries() -> list:
    items = []
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as exc:  # один сломанный источник не должен ронять бота
            print(f"[warn] не удалось прочитать {url}: {exc}")
            continue
        for e in feed.entries[:15]:
            link = getattr(e, "link", None)
            if not link:
                continue
            published = getattr(e, "published_parsed", None) or getattr(
                e, "updated_parsed", None
            )
            ts = dt.datetime(*published[:6]) if published else dt.datetime.min
            items.append(
                {
                    "title": clean(getattr(e, "title", ""), 200),
                    "summary": clean(getattr(e, "summary", "")),
                    "link": link,
                    "ts": ts,
                }
            )
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


# ───────────────────────── Нейросеть ─────────────────────────


def ask_llm(system: str, user: str) -> str:
    if PROVIDER == "groq":
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "temperature": 0.6,
                "max_tokens": 1000,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=90,
        )
        if not r.ok:
            print(f"[groq] ошибка {r.status_code}: {r.text[:300]}")
            print(
                f"[диагностика] ключ задан: {bool(GROQ_KEY)}, "
                f"длина: {len(GROQ_KEY)}, начинается с gsk_: {GROQ_KEY.startswith('gsk_')}"
            )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    if PROVIDER == "gemini":
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
        )
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
        }
        r = requests.post(url, json=body, timeout=90)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 1000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=90,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def tidy(text: str) -> str:
    text = re.sub(r"^```\w*\n?|```$", "", text.strip()).strip()
    return text


# ───────────────────────── Telegram ─────────────────────────


def tg_send(chat_id: str, text: str) -> None:
    text = text[:4000]
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=30,
    )
    if not r.ok:
        # если нейросеть сломала HTML-разметку, отправляем простым текстом
        plain = html.unescape(re.sub(r"<[^>]+>", "", text))
        r = requests.post(url, json={"chat_id": chat_id, "text": plain}, timeout=30)
    r.raise_for_status()


# ───────────────────────── Главная логика ─────────────────────────


def build_post(rubric: str, fresh: list) -> tuple:
    """Возвращает (текст поста, список использованных ссылок)."""
    if rubric == "prompt":
        topic = PROMPT_TOPICS[dt.datetime.utcnow().timetuple().tm_yday % len(PROMPT_TOPICS)]
        text = ask_llm(BASE_RULES, RUBRICS["prompt"].format(topic=topic))
        return tidy(text), []

    if rubric == "digest":
        chosen = fresh[:5]
        material = "\n\n".join(
            f"{i + 1}. {x['title']}\n{x['summary']}\n{x['link']}"
            for i, x in enumerate(chosen)
        )
        text = ask_llm(BASE_RULES, f"{RUBRICS['digest']}\n\nМАТЕРИАЛ:\n{material}")
        return tidy(text), [x["link"] for x in chosen]

    item = fresh[0]
    material = f"Заголовок: {item['title']}\nОписание: {item['summary']}\nСсылка: {item['link']}"
    text = ask_llm(BASE_RULES, f"{RUBRICS[rubric]}\n\nМАТЕРИАЛ:\n{material}")
    return tidy(text), [item["link"]]


def main() -> int:
    posted = load_state()
    rubric = rubric_for_today()

    fresh = [x for x in fetch_entries() if x["link"] not in posted]
    needed = 5 if rubric == "digest" else 1
    if rubric != "prompt" and len(fresh) < needed:
        print("Нет свежих материалов, пропускаем запуск.")
        return 0

    post, used_links = build_post(rubric, fresh)
    if FOOTER:
        post = f"{post}\n\n{FOOTER}"

    if MODE == "auto":
        tg_send(CHANNEL_ID, post)
        print("Опубликовано в канал.")
    else:
        if not OWNER_ID:
            print("Для режима draft нужен TELEGRAM_OWNER_ID.")
            return 1
        tg_send(OWNER_ID, f"📝 <b>Черновик</b> (рубрика: {rubric})\n\n{post}")
        print("Черновик отправлен владельцу.")

    save_state(posted + used_links)
    return 0


if __name__ == "__main__":
    sys.exit(main())
