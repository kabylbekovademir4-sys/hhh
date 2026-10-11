"""
Бот для Telegram-канала «Нейросети на практике» (версия 2).

Что умеет:
- 8 рубрик, чтобы канал не был однообразным (см. DAY_RUBRIC и RUBRICS);
- в каждом посте красивая картинка-обложка, которую бот рисует сам;
- проверка фактов: после написания поста вторая проверка сверяет его с текстом
  источника, неподтверждённое удаляется;
- режим auto: пост уходит в канал, только если проверка пройдена,
  иначе черновик с пометкой приходит вам в личку;
- режим draft: всё приходит только вам в личку.
"""

import datetime as dt
import html
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import feedparser
import requests
from PIL import Image, ImageDraw, ImageFont

# ───────────────────────── Настройки (из переменных окружения) ─────────────────────────


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = env("TELEGRAM_CHANNEL_ID")        # например @neuro_praktika
OWNER_ID = env("TELEGRAM_OWNER_ID")            # ваш числовой id
MODE = env("MODE", "draft").lower()            # draft или auto
FOOTER = env("CHANNEL_FOOTER")                 # например: 📲 @neuro_praktika
BRAND = env("CHANNEL_BRAND", "Нейросети на практике")
FORCE_RUBRIC = env("RUBRIC").lower()           # для ручных тестов

PROVIDER = env("LLM_PROVIDER", "groq").lower() # groq, gemini или anthropic
GROQ_KEY = env("GROQ_API_KEY").strip('"').strip("'")
GROQ_MODEL = env("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1"
GROQ_PREFERRED = ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"]
GEMINI_KEY = env("GEMINI_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-2.5-flash")
ANTHROPIC_KEY = env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-haiku-5-5")

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE = Path(__file__).parent / "posted.json"

# Источники новостей. Можно добавлять свои RSS-ссылки.
FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://venturebeat.com/category/ai/feed/",
    "https://huggingface.co/blog/feed.xml",
    "https://openai.com/news/rss.xml",
]

# ───────────────────────── Банки тем (без выдуманных фактов) ─────────────────────────

GLOSSARY_TERMS = [
    "нейросеть", "промпт", "токен", "галлюцинация ИИ", "контекстное окно",
    "ИИ-агент", "мультимодальная модель", "дообучение модели",
    "температура модели", "большая языковая модель", "RAG",
]

PROMPT_TOPICS = [
    "составить план обучения на месяц", "написать резюме и сопроводительное письмо",
    "разобрать сложную тему простыми словами", "придумать идеи для контента в соцсетях",
    "подготовиться к собеседованию", "составить меню и список покупок на неделю",
    "улучшить и отредактировать текст", "проанализировать идею бизнеса",
    "адаптировать текст для другой аудитории", "создать чек-лист для рутинной задачи",
]

LIFEHACK_TOPICS = [
    "быстро разобраться в длинной статье", "составить план поездки на выходные",
    "переписать письмо вежливее и короче", "выучить новую тему за один вечер",
    "составить расписание дня с учётом дел", "подготовиться к важному разговору",
    "превратить заметки со встречи в список задач",
]

POLLS = [
    ("Для чего вы чаще всего используете нейросети?", ["Работа", "Учёба", "Творчество", "Развлечение"]),
    ("Какие посты вам интереснее всего?", ["Новости", "Промпты", "Разборы инструментов", "Лайфхаки"]),
    ("Платите ли вы за нейросети?", ["Да, есть подписка", "Нет, только бесплатные", "Пока не пробовал(а)"]),
    ("Как часто вы пользуетесь нейросетями?", ["Каждый день", "Несколько раз в неделю", "Редко", "Только начинаю"]),
    ("Что в ИИ смущает вас больше всего?", ["Ошибки и выдумки", "Приватность", "Цена", "Ничего не смущает"]),
    ("Нужно ли больше готовых промптов?", ["Да, каждый день", "Раз в неделю хватит", "Лучше больше новостей"]),
]

QUESTIONS = [
    "Для какой задачи нейросеть сэкономила вам больше всего времени?",
    "Какой ИИ-инструмент вы хотели бы разобрать подробнее?",
    "Что вам не хватает в нейросетях, которыми вы пользуетесь?",
    "Какую рутинную задачу вы бы отдали ИИ в первую очередь?",
    "Какой промпт у вас получается лучше всего? Поделитесь идеей.",
]

# ───────────────────────── Рубрики и правила для нейросети ─────────────────────────

STYLE_RULES = """Ты редактор Telegram-канала «Нейросети на практике». Пиши по-русски, живо и просто, без воды и канцелярита.
Формат: Telegram HTML. Разрешены ТОЛЬКО теги <b>, <i>, <code>, <pre>. Никакого Markdown, никаких звёздочек, никаких ссылок.
Эмодзи уместны, но не перегружай. Длина: до 800 символов.
Верни только готовый текст поста, без пояснений и без обрамления в кавычки или ```."""

FACT_RULES = """Работа строго по материалу:
- Каждое утверждение в посте должно прямо следовать из блока МАТЕРИАЛ. Ничего не добавляй от себя и не додумывай.
- Не пиши даты, цены, цифры, названия мероприятий, сроки выхода и функции, если их нет в материале.
- Если материал не говорит, как попробовать, сколько стоит или когда будет доступно, НЕ пиши эту строку вообще. Общие фразы вроде «следите за анонсами» запрещены.
- Никаких оценок и обещаний («революция», «лучший», «изменит всё»). Не пиши «уже доступно» или «бесплатно», если это не сказано прямо.
- Ссылку на источник не пиши, её добавит система.
- Если фактов в материале слишком мало для полезного поста, ответь ровно одним словом: SKIP"""

NOFACT_RULES = """Материала-новости в этой рубрике нет.
Пиши только общеизвестные вещи. Не упоминай конкретные компании, продукты, даты, цены и цифры."""

CHECKER = """Ты строгий фактчекер. Тебе дан МАТЕРИАЛ и ПОСТ.
Найди в посте все утверждения, которые НЕ подтверждены материалом: даты, цифры, названия, обещания, функции, сроки, причины и выводы, которых там нет.
Если таких нет, ответь ровно одним словом: OK
Иначе перечисли каждое неподтверждённое утверждение списком, по одному на строку, без вступления."""

RUBRICS = {
    "news": """Рубрика: свежая новость.
Структура (строку пропускай, если в материале для неё нет данных):
🔥 <b>Заголовок: суть новости</b>

<b>Что случилось:</b> 1-2 предложения строго по материалу
<b>Почему это важно:</b> только если причина прямо названа в материале
<b>Как попробовать:</b> только если материал прямо об этом говорит""",
    "tool": """Рубрика: «Инструмент недели» (понедельник).
Структура (строку пропускай, если в материале для неё нет данных):
🛠 <b>Инструмент недели: название</b>

<b>Что это:</b> 1-2 предложения
<b>Что умеет:</b> до 3 коротких пунктов через «•», только из материала
<b>Цена и доступ:</b> только если указаны в материале""",
    "digest": """Рубрика: «Итоги недели» (пятница). В материале несколько пронумерованных новостей.
Структура:
📌 <b>Итоги недели в мире нейросетей</b>

Пять пунктов, нумерация как в материале (1., 2., ...). В каждом: эмодзи, <b>название</b> и одна короткая строка строго по материалу.
В конце: «Сохраняйте пост, чтобы не потерять».""",
    "prompt": """Рубрика: «Промпт дня» (среда). Придумай полезный готовый промпт.
Тема: {topic}.
Структура:
💬 <b>Промпт дня: короткое название задачи</b>

Скопируйте и вставьте в любую нейросеть:

<pre>сам промпт на русском, 3-6 строк, с местами для подстановки в [квадратных скобках]</pre>

<b>Что получите:</b> одно предложение""",
    "glossary": """Рубрика: «Нейро-словарик» (вторник). Объясни термин «{topic}».
Только общепризнанные определения.
Структура:
📖 <b>Нейро-словарик: {topic}</b>

<b>Простыми словами:</b> 1-2 предложения
<b>Пример из жизни:</b> короткая понятная аналогия
<b>Зачем это знать:</b> одна строка""",
    "lifehack": """Рубрика: «Лайфхак» (четверг). Задача: {topic}.
Покажи, как решить её с помощью нейросети-чата за 3 шага. Не называй конкретные сервисы, пиши «нейросеть».
Структура:
⚡️ <b>Лайфхак: короткое название</b>

1️⃣ первый шаг
2️⃣ второй шаг
3️⃣ третий шаг

<pre>готовый промпт для этой задачи, 2-4 строки, с [местами для подстановки]</pre>

<b>Итог:</b> одна строка""",
}

ALL_RUBRICS = ["news", "tool", "digest", "prompt", "glossary", "lifehack", "poll", "question"]
DAY_RUBRIC = {0: "tool", 1: "glossary", 2: "prompt", 3: "lifehack", 4: "digest", 5: "poll", 6: "question"}
ARTICLE_RUBRICS = ("news", "tool", "digest")

LABELS = {
    "news": "НОВОСТЬ", "tool": "ИНСТРУМЕНТ НЕДЕЛИ", "digest": "ИТОГИ НЕДЕЛИ",
    "prompt": "ПРОМПТ ДНЯ", "glossary": "НЕЙРО-СЛОВАРИК", "lifehack": "ЛАЙФХАК",
    "poll": "ОПРОС", "question": "ВОПРОС НЕДЕЛИ",
}
ACCENTS = {
    "news": (255, 99, 71), "tool": (255, 171, 64), "digest": (171, 71, 188),
    "prompt": (66, 165, 245), "glossary": (38, 198, 160), "lifehack": (255, 214, 64),
    "poll": (236, 64, 122), "question": (124, 179, 66),
}


def pick_rubric() -> str:
    """Утро: рубрика дня. Днём и вечером: свежие новости."""
    now = dt.datetime.now(dt.timezone.utc)
    slot = 0 if now.hour < 8 else (1 if now.hour < 13 else 2)
    return DAY_RUBRIC[now.weekday()] if slot == 0 else "news"


def pick(bank: list):
    week = dt.date.today().isocalendar()[1]
    return bank[week % len(bank)]


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
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
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
            published = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            ts = dt.datetime(*published[:6]) if published else dt.datetime.min
            items.append({
                "title": clean(getattr(e, "title", ""), 200),
                "summary": clean(getattr(e, "summary", "")),
                "link": link,
                "ts": ts,
            })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def article_text(item: dict) -> str:
    """Пробуем скачать полный текст статьи, чтобы у нейросети было больше фактов."""
    fallback = item["summary"]
    try:
        r = requests.get(
            item["link"],
            headers={"User-Agent": "Mozilla/5.0 (compatible; NeuroBot/2.0)"},
            timeout=20,
        )
        if r.ok and "html" in r.headers.get("content-type", ""):
            paras = re.findall(r"<p[^>]*>(.*?)</p>", r.text, flags=re.S | re.I)
            parts = [p for p in (clean(x, 2000) for x in paras) if len(p) > 60]
            text = " ".join(parts)
            if len(text) > 400:
                return text[:6000]
    except Exception as exc:
        print(f"[warn] не удалось скачать статью: {exc}")
    return fallback


# ───────────────────────── Нейросеть ─────────────────────────


def pick_groq_model():
    r = requests.get(f"{GROQ_URL}/models", headers={"Authorization": f"Bearer {GROQ_KEY}"}, timeout=30)
    if not r.ok:
        return None
    ids = [m["id"] for m in r.json().get("data", [])]
    print("[groq] доступные модели:", ", ".join(ids))
    for p in GROQ_PREFERRED:
        if p in ids:
            return p
    skip = ("whisper", "guard", "tts", "orpheus", "embed", "safeguard")
    for i in ids:
        if not any(s in i for s in skip):
            return i
    return None


def ask_groq(system: str, user: str) -> str:
    global GROQ_MODEL

    def call(model: str):
        body = {
            "model": model,
            "temperature": 0.5,
            "max_tokens": 3000,  # запас: часть моделей «думает» перед ответом
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"
        return requests.post(
            f"{GROQ_URL}/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=90,
        )

    r = call(GROQ_MODEL)
    for _ in range(3):  # лимит запросов в минуту: подождать и повторить
        if r.status_code != 429:
            break
        wait = int(float(r.headers.get("retry-after", "20")))
        print(f"[groq] лимит запросов, ждём {wait} с")
        time.sleep(min(wait, 60))
        r = call(GROQ_MODEL)
    if r.status_code in (400, 404):
        print(f"[groq] модель {GROQ_MODEL} не подошла: {r.text[:200]}")
        fallback = pick_groq_model()
        if fallback and fallback != GROQ_MODEL:
            GROQ_MODEL = fallback
            print(f"[groq] пробуем модель {GROQ_MODEL}")
            r = call(GROQ_MODEL)
    if not r.ok:
        print(f"[groq] ошибка {r.status_code}: {r.text[:300]}")
        print(f"[диагностика] ключ задан: {bool(GROQ_KEY)}, длина: {len(GROQ_KEY)}, начинается с gsk_: {GROQ_KEY.startswith('gsk_')}")
        r.raise_for_status()
    text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise RuntimeError("Groq вернул пустой ответ")
    return text


def ask_llm(system: str, user: str) -> str:
    if PROVIDER == "groq":
        return ask_groq(system, user)
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
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": ANTHROPIC_MODEL, "max_tokens": 2000, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=90,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def tidy(text: str) -> str:
    return re.sub(r"^```\w*\n?|```$", "", text.strip()).strip()


def write_post(rubric: str, topic: str = "") -> str:
    """Рубрики без новостей: тема из банка, фактов нет по задумке."""
    template = RUBRICS[rubric].format(topic=topic)
    return tidy(ask_llm(STYLE_RULES + "\n" + NOFACT_RULES, template))


def write_checked_post(rubric: str, material: str):
    """Пишет пост по материалу и сверяет его с источником.
    Возвращает (текст или None, замечания проверки; пусто если всё подтверждено)."""
    system = STYLE_RULES + "\n" + FACT_RULES
    base_user = f"{RUBRICS[rubric]}\n\nМАТЕРИАЛ:\n{material}"
    user = base_user
    draft, issues = "", ""
    for _ in range(2):
        draft = tidy(ask_llm(system, user))
        if re.fullmatch(r"\W*SKIP\W*", draft, flags=re.I):
            return None, "SKIP"
        verdict = tidy(ask_llm(CHECKER, f"МАТЕРИАЛ:\n{material}\n\nПОСТ:\n{draft}"))
        if re.fullmatch(r"\W*OK\W*", verdict, flags=re.I):
            return draft, ""
        issues = verdict
        user = (
            base_user
            + f"\n\nВ предыдущей версии были утверждения, которых нет в материале:\n{issues}\n\n"
            + f"Перепиши пост, УДАЛИВ их. Предыдущая версия:\n{draft}"
        )
    return draft, issues


# ───────────────────────── Картинка-обложка ─────────────────────────

EMOJI_RE = re.compile("[\U00010000-\U0010ffff\u2600-\u27bf\u2b50\ufe0f\u200d]+")
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
]


def load_font(size: int):
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def wrap_px(draw, text: str, font, max_w: int) -> list:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def headline(post: str) -> str:
    for line in post.splitlines():
        text = EMOJI_RE.sub("", html.unescape(re.sub(r"<[^>]+>", "", line))).strip()
        if text:
            return text if len(text) <= 90 else text[:87].rstrip() + "…"
    return BRAND


def make_card(rubric: str, title: str):
    """Рисует обложку 1280x720. Возвращает PNG в байтах или None при ошибке."""
    try:
        W, H = 1280, 720
        accent = ACCENTS.get(rubric, (90, 140, 255))
        top = (12, 16, 36)
        bottom = tuple(min(255, int(c * 0.30) + 14) for c in accent)
        img = Image.new("RGB", (W, H))
        d = ImageDraw.Draw(img)
        for y in range(H):
            t = y / H
            d.line([(0, y), (W, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))

        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.ellipse((W - 520, -200, W + 200, 520), fill=accent + (60,))
        od.ellipse((W - 320, H - 280, W + 280, H + 320), fill=accent + (38,))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        d = ImageDraw.Draw(img)

        d.rectangle((0, 0, 18, H), fill=accent)

        label = LABELS.get(rubric, "НЕЙРОСЕТИ")
        chip_font = load_font(32)
        tw = d.textlength(label, font=chip_font)
        d.rounded_rectangle((80, 64, 80 + tw + 56, 128), radius=32, fill=accent)
        d.text((108, 76), label, font=chip_font, fill=(15, 15, 30))

        title = title or BRAND
        for size in (92, 84, 76, 68, 60, 52, 46):
            font = load_font(size)
            lines = wrap_px(d, title, font, W - 200)
            if len(lines) <= 4 and len(lines) * size * 1.22 <= 380:
                break
        lines = lines[:4]
        y = 190
        for line in lines:
            d.text((80, y), line, font=font, fill=(255, 255, 255))
            y += int(size * 1.22)

        d.rectangle((80, H - 112, 170, H - 106), fill=accent)
        d.text((80, H - 92), BRAND, font=load_font(34), fill=(205, 210, 230))

        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()
    except Exception as exc:
        print(f"[warn] не удалось нарисовать картинку: {exc}")
        return None


# ───────────────────────── Telegram ─────────────────────────


def plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def tg_message(chat_id: str, text: str) -> int:
    payload = {
        "chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    r = requests.post(f"{API}/sendMessage", json=payload, timeout=30)
    if not r.ok:  # если нейросеть сломала HTML-разметку, шлём простым текстом
        print(f"[tg] разметка не принята ({r.status_code}), шлём простым текстом")
        payload = {"chat_id": chat_id, "text": plain(text)[:4000],
                   "link_preview_options": {"is_disabled": True}}
        r = requests.post(f"{API}/sendMessage", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def tg_photo(chat_id: str, image: bytes, caption: str = "") -> int:
    files = {"photo": ("card.png", image, "image/png")}
    data = {"chat_id": chat_id}
    if caption:
        data.update(caption=caption, parse_mode="HTML")
    r = requests.post(f"{API}/sendPhoto", data=data, files=files, timeout=60)
    if not r.ok and caption:
        print(f"[tg] подпись не принята ({r.status_code}), шлём простым текстом")
        data = {"chat_id": chat_id, "caption": plain(caption)[:1024]}
        r = requests.post(f"{API}/sendPhoto", data=data, files=files, timeout=60)
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def tg_poll(chat_id: str, question: str, options: list) -> int:
    r = requests.post(
        f"{API}/sendPoll",
        json={"chat_id": chat_id, "question": question,
              "options": [{"text": o} for o in options], "is_anonymous": True},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def send_post(chat_id: str, post: str, card) -> int:
    """Картинка с подписью, если текст влезает (лимит подписи 1024 знака), иначе картинка и текст отдельно."""
    if card and len(plain(post)) <= 1000:
        return tg_photo(chat_id, card, post)
    if card:
        tg_photo(chat_id, card)
    return tg_message(chat_id, post)


def post_link(message_id: int) -> str:
    if CHANNEL_ID.startswith("@"):
        return f"https://t.me/{CHANNEL_ID[1:]}/{message_id}"
    return ""


# ───────────────────────── Главная логика ─────────────────────────


def main() -> int:
    if not BOT_TOKEN or not CHANNEL_ID:
        print("Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID.")
        return 1
    auto = MODE == "auto"
    target = CHANNEL_ID if auto else OWNER_ID
    if not target:
        print("Для режима draft нужен TELEGRAM_OWNER_ID.")
        return 1

    posted = load_state()
    rubric = FORCE_RUBRIC if FORCE_RUBRIC in ALL_RUBRICS else pick_rubric()
    print(f"Рубрика: {rubric}, режим: {MODE}")
    draft_tag = "" if auto else f"📝 <b>Черновик</b> (рубрика: {rubric})\n\n"
    used: list = []

    # Опрос: картинка и встроенный опрос Telegram
    if rubric == "poll":
        question, options = pick(POLLS)
        card = make_card("poll", question)
        if card:
            tg_photo(target, card, draft_tag + "🗳 <b>Опрос дня</b>")
        tg_poll(target, question, options)
        print("Опрос отправлен.")
        return 0

    post, issues, source_line = "", "", ""

    if rubric == "question":
        post = f"💭 <b>Вопрос недели</b>\n\n{pick(QUESTIONS)}\n\nОтвечайте в комментариях 👇"

    elif rubric in ("prompt", "glossary", "lifehack"):
        bank = {"prompt": PROMPT_TOPICS, "glossary": GLOSSARY_TERMS, "lifehack": LIFEHACK_TOPICS}[rubric]
        post = write_post(rubric, pick(bank))

    elif rubric == "digest":
        fresh = [x for x in fetch_entries() if x["link"] not in posted][:5]
        if len(fresh) < 5:
            print("Мало свежих материалов для дайджеста, пропускаем запуск.")
            return 0
        material = "\n\n".join(f"{i + 1}. {x['title']}\n{x['summary']}" for i, x in enumerate(fresh))
        post, issues = write_checked_post("digest", material)
        used = [x["link"] for x in fresh]
        sources = " ".join(f'<a href="{html.escape(x["link"], quote=True)}">{i + 1}</a>' for i, x in enumerate(fresh))
        source_line = f"🔗 Источники: {sources}"

    else:  # news и tool: до двух кандидатов, берём первый, что прошёл проверку
        fresh = [x for x in fetch_entries() if x["link"] not in posted]
        if not fresh:
            print("Нет свежих материалов, пропускаем запуск.")
            return 0
        last_failed = None
        for item in fresh[:2]:
            used.append(item["link"])
            material = f"Заголовок: {item['title']}\nТекст: {article_text(item)}"
            text, problems = write_checked_post(rubric, material)
            link_line = f'🔗 <a href="{html.escape(item["link"], quote=True)}">Источник</a>'
            if text and not problems:
                post, issues, source_line = text, "", link_line
                break
            if text:
                last_failed = (text, problems, link_line)
            print(f"[проверка] материал не подошёл: {problems[:200]}")
        if not post and last_failed:
            post, issues, source_line = last_failed
        if not post:
            print("Подходящего поста не получилось, пропускаем запуск.")
            save_state(posted + used)
            return 0

    if source_line:
        post = f"{post}\n\n{source_line}"
    if FOOTER:
        post = f"{post}\n\n{FOOTER}"
    card = make_card(rubric, headline(post))

    if issues:  # проверка не пройдена: в канал не отправляем, шлём владельцу
        warning = (
            f"⚠️ <b>Пост не прошёл проверку фактов и в канал не отправлен</b> (рубрика: {rubric})\n\n"
            f"Сомнительные места:\n{html.escape(issues)[:1200]}\n\n— — —\n\n{post}"
        )
        if OWNER_ID:
            tg_message(OWNER_ID, warning)
        print("Проверка не пройдена, отправлено владельцу.")
    else:
        message_id = send_post(target, draft_tag + post, card)
        print("Опубликовано в канал." if auto else "Черновик отправлен владельцу.")
        if auto and OWNER_ID:
            link = post_link(message_id)
            tg_message(OWNER_ID, f"✅ Опубликовано (рубрика: {rubric}). {link}".strip())

    save_state(posted + used)
    return 0


if __name__ == "__main__":
    sys.exit(main())
