import logging
import re
import json
import requests
import sqlite3

from urllib.parse import unquote, urljoin
from shutil import rmtree
from time import sleep
from hashlib import md5
from os import getenv, listdir, makedirs, remove
from os.path import join, exists
from bs4 import BeautifulSoup
from sys import exit
from anki.collection import Collection
from anki.exporting import AnkiPackageExporter
from anki_template import CARD_CSS, T1_FRONT, T1_BACK, T2_FRONT, T2_BACK
from dotenv import load_dotenv
from cerebras.cloud.sdk import Cerebras
from cerebras.cloud.sdk.types.chat.chat_completion import ChatCompletionResponse

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="{asctime} - {levelname} - {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M",
)
logger = logging.getLogger("BUNPRO")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
}

INDEX_URL = "https://bunpro.jp/grammar_points"
DB_FILE = "bunpro.db"
CACHE_DIR = "bunpro_cache"

session = requests.Session()
session.headers.update(HEADERS)

api_key = getenv("API_KEY")
if api_key is None:
    logger.error("API_KEY не найден в .env")
    exit(1)

client = Cerebras(api_key=api_key, max_retries=0)

conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS links (
    point_name TEXT PRIMARY KEY,
    url TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS extracted_grammar (
    filename TEXT PRIMARY KEY,
    title TEXT,
    jlpt TEXT,
    structure TEXT,
    about TEXT,
    examples_raw TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS parsed_grammar (
    source_title TEXT PRIMARY KEY,
    grammar TEXT,
    meaning TEXT,
    structure TEXT,
    jlpt TEXT,
    nuance TEXT,
    examples TEXT
)
""")

conn.commit()

cursor.execute("SELECT count(*) FROM links")
links_count = cursor.fetchone()[0]

if links_count == 0:
    logger.info("Парсим главную страницу грамматики...")
    try:
        response = session.get(INDEX_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        all_links = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if isinstance(href, list):
                href = " ".join(href)
            match = re.search(r"/grammar_points/([^/?#]+)$", href)
            if match:
                point_name = unquote(match.group(1))
                if point_name not in [
                    "",
                    "grammar_points",
                    "search",
                    "paths",
                    "lessons",
                    "bookmarks",
                ]:
                    full_url = urljoin("https://bunpro.jp", href)
                    all_links.add((point_name, full_url))

        grammar_list = list(all_links)

        cursor.executemany(
            "INSERT OR IGNORE INTO links (point_name, url) VALUES (?, ?)", grammar_list
        )
        conn.commit()
        logger.info(
            f"Успешно найдено и сохранено грамматических точек в БД: {len(grammar_list)}"
        )

    except Exception as e:
        logger.error(f"Ошибка при получении списка грамматики: {e}")
        conn.close()
        exit(1)

else:
    logger.info("Главная страница уже пропаршена...")


cursor.execute("SELECT point_name, url FROM links")
grammar_list = cursor.fetchall()

makedirs(CACHE_DIR, exist_ok=True)


def sanitize_filename(name: str) -> str:
    return "".join(
        [c for c in name if c.isalpha() or c.isdigit() or c in (" ", "_", "-")]
    ).rstrip()


downloaded = 0
skipped = 0

for point_name, url in grammar_list:
    safe_name = (
        sanitize_filename(point_name) or md5(point_name.encode()).hexdigest()[:8]
    )
    cache_path = join(CACHE_DIR, f"{safe_name}.html")

    if exists(cache_path):
        skipped += 1
        continue

    try:
        res = session.get(url, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(res.text)
            downloaded += 1
            logger.debug(f"найдена статья {point_name}")
            sleep(2.2)
        elif res.status_code == 404:
            logger.error(f"Страница не найдена: {url}")
        else:
            logger.error(f"Ошибка при загрузке {point_name}: Код {res.status_code}")
    except Exception as e:
        logger.error(
            f"Ошибка сети на {point_name}: {e}. Перезапустите ячейку для продолжения."
        )
        break

logger.info(
    f"Процесс завершен. Загружено новых: {downloaded}, Пропущено (были в кэше): {skipped}"
)

grammar_raw_files = [f for f in listdir(CACHE_DIR) if f.endswith(".html")]

cursor.execute("SELECT filename FROM extracted_grammar")
processed_files = {row[0] for row in cursor.fetchall()}

files_to_process = [f for f in grammar_raw_files if f not in processed_files]


if files_to_process:
    logger.info(f"Очищаем новые страницы от мусора ({len(files_to_process)} шт.)...")
    for file in grammar_raw_files:
        with open(join(CACHE_DIR, file), "r", encoding="utf-8") as f:
            html = f.read()
            soup = BeautifulSoup(html, "html.parser")

            title_tag = soup.find("h1")
            title = (
                title_tag.get_text(" ", strip=True)
                if title_tag
                else file.replace(".html", "")
            )

            jlpt_level = "Non-JLPT"
            jlpt_element = soup.find(string=re.compile(r"JLPT\s*N[1-5]", re.I))
            if jlpt_element:
                match = re.search(r"N[1-5]", jlpt_element, re.I)
                if match:
                    jlpt_level = match.group(0).upper()

            if jlpt_level == "Non-JLPT":
                breadcrumb = soup.find(
                    ["nav", "div"], class_=re.compile(r"breadcrumb|lesson", re.I)
                )
                if breadcrumb:
                    match = re.search(r"N[1-5]", breadcrumb.get_text(), re.I)
                    if match:
                        jlpt_level = match.group(0).upper()

            def get_section_text(search_regex) -> str:
                header = soup.find(["h2", "h3"], string=re.compile(search_regex, re.I))
                if not header:
                    header = soup.find(string=re.compile(search_regex, re.I))
                    if header:
                        header = header.parent
                if not header:
                    return ""
                content = []
                for sibling in header.find_all_next():
                    if sibling.name in ["h1", "h2", "h3"] and not re.search(
                        search_regex, sibling.text, re.I
                    ):
                        break
                    if sibling.name in ["p", "ul", "ol", "div"]:
                        if sibling.name != "div" or not sibling.find(
                            ["p", "div", "ul"]
                        ):
                            txt = sibling.get_text(" ", strip=True)
                            if txt and txt not in ["English", "Japanese"]:
                                content.append(txt)
                return "\n".join(content)

            structure_text = get_section_text(r"^Structure$")
            about_text = get_section_text(r"About")

            examples: list[str] = []
            next_data_script = soup.find("script", id="__NEXT_DATA__")
            if next_data_script:
                try:
                    if next_data_script.string:
                        data = json.loads(next_data_script.string)

                    def extract_sentences(obj):
                        if isinstance(obj, dict):
                            jp_val = (
                                obj.get("japanese")
                                or obj.get("sentence")
                                or obj.get("content")
                            )
                            if isinstance(jp_val, str) and re.search(
                                r"[\u3040-\u30ff]", jp_val
                            ):
                                clean_txt = re.sub(r"<[^>]+>", "", jp_val).strip()
                                if len(clean_txt) > 5:
                                    examples.append(clean_txt)
                            for v in obj.values():
                                extract_sentences(v)
                        elif isinstance(obj, list):
                            for i in obj:
                                extract_sentences(i)

                    extract_sentences(data)
                except Exception as e:
                    logger.error(e)
                    pass

            if not examples:
                for el in soup.find_all(
                    ["span", "div"], class_=re.compile(r"sentence|japanese", re.I)
                ):
                    txt = el.get_text(strip=True)
                    if len(txt) > 10 and re.search(r"[\u3040-\u30ff]", txt):
                        examples.append(txt)

            seen = set()
            unique_examples = []
            for ex in examples:
                clean = re.sub(r"\s+", " ", ex).strip()
                if clean not in seen and len(clean) > 5:
                    unique_examples.append(clean)
                    seen.add(clean)

            examples_raw_json = json.dumps(unique_examples[:10], ensure_ascii=False)
            cursor.execute(
                """
                INSERT OR REPLACE INTO extracted_grammar (filename, title, jlpt, structure, about, examples_raw)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    file,
                    title,
                    jlpt_level,
                    structure_text,
                    about_text,
                    examples_raw_json,
                ),
            )
            conn.commit()
            logger.info(f"Добавлена сырая статья: {title}")

    logger.info("Все новые HTML-страницы успешно обработаны и внесены в БД.")
else:
    logger.info("Новых HTML-файлов для извлечения не обнаружено.")

prompt_template = """
Ты эксперт в японском языке. Я тебе присылаю справку о какой-то определенной 
грамматической конструкции. Переведи её с японского на русский. Выдели все необходимое в нюанс.
Для каждого примера приведи:
1. Оригинальное приложение на японском языке (замени ____ на грамматическую конструкцию или её нужную форму).
2. Перевод на русский язык.

Грамматика: {title}
Уровень JLPT: {jlpt}
Структура: {structure}
Пояснение к грамматике: {about}
Примеры: {examples}

В ответ жду ТОЛЬКО валидный JSON. Не используй комментарии, скрипты внтури JSON. 
В поле meaning должно быть всё на русском, но при этом кратко, по делу, не больше 20-25 симоволов. 
В structure тоже кратко, по делу, всё на русском, но без потери важной информации.
Требуемая структура:
{{
  "grammar": "...",
  "meaning": "...",
  "structure": "...",
  "jlpt": "{jlpt}",
  "nuance": "...",
  "examples": [
    {{"jp": "...", "ru": "..."}}
  ]
}}
"""

cursor.execute("""
    SELECT title, jlpt, structure, about, examples_raw 
    FROM extracted_grammar 
    WHERE title NOT IN (SELECT source_title FROM parsed_grammar)
""")
to_process_llm = cursor.fetchall()

if to_process_llm:
    logger.info(f"Запуск разметки через LLM. Ожидает обработки: {len(to_process_llm)}")

    for title, jlpt, structure, about, examples_raw_str in to_process_llm:
        examples_raw = json.loads(examples_raw_str)
        logger.info(f"Обработка: {title} ({jlpt})...")

        prompt = prompt_template.format(
            title=title,
            jlpt=jlpt,
            structure=structure,
            about=about,
            examples=" | ".join(examples_raw[:3]),
        )

        max_retries = 5
        retry_delay = 4
        success = False
        parsed_json = None

        for attempt in range(max_retries):
            try:
                completion = client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model="gpt-oss-120b",
                    temperature=0.5,
                    top_p=1,
                    stream=False,
                    response_format={"type": "json_object"},
                )
                raw_text = None
                if isinstance(completion, ChatCompletionResponse):
                    raw_text = completion.choices[0].message.content

                if raw_text:
                    parsed_json = json.loads(raw_text)
                    parsed_json["jlpt"] = jlpt
                    success = True
                    break
                else:
                    logger.info(
                        f"Попытка {attempt + 1}/{max_retries}: Получен пустой ответ от API."
                    )
            except Exception as e:
                logger.error(f"Попытка {attempt + 1}/{max_retries}: Ошибка API: {e}")

            if attempt < max_retries - 1:
                logger.info(f"Ожидание {retry_delay} сек перед повторным запросом...")
                sleep(retry_delay)
                retry_delay *= 2

        if success and parsed_json:
            try:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO parsed_grammar (source_title, grammar, meaning, structure, jlpt, nuance, examples)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        parsed_json.get("grammar", title),
                        parsed_json.get("meaning", ""),
                        parsed_json.get("structure", parsed_json.get("structure", "")),
                        jlpt,
                        parsed_json.get("nuance", ""),
                        json.dumps(parsed_json.get("examples", []), ensure_ascii=False),
                    ),
                )
                conn.commit()
                logger.info("Успешно сохранено в БД.")
            except Exception as e:
                logger.error(f"Ошибка записи перевода в БД для {title}: {e}")

            sleep(10)
        else:
            logger.error(
                f"Не удалось обработать {title} после {max_retries} попыток. Переходим к следующему."
            )
else:
    logger.info("Все извлеченные статьи уже переведены и сохранены в БД.")

cursor.execute(
    "SELECT grammar, jlpt, meaning, structure, nuance, examples FROM parsed_grammar"
)
grammar_items = cursor.fetchall()


def generate_guid(grammar: str, jlpt: str) -> str:
    return md5(f"{grammar}{jlpt}".encode()).hexdigest()


temp_db = "temp_bunpro.anki2"
if exists(temp_db):
    remove(temp_db)

col = Collection(temp_db)
try:
    model = col.models.by_name("bunpro_grammar_deck")
    if not model:
        model = col.models.new("bunpro_grammar_deck")
        col.models.add_field(model, col.models.new_field("Grammar"))
        col.models.add_field(model, col.models.new_field("JLPT"))
        col.models.add_field(model, col.models.new_field("Meaning"))
        col.models.add_field(model, col.models.new_field("Structure"))
        col.models.add_field(model, col.models.new_field("Nuance"))
        col.models.add_field(model, col.models.new_field("ExamplesHTML"))

    model["css"] = CARD_CSS

    t1 = col.models.new_template("JP - RU")
    t1["qfmt"] = T1_FRONT
    t1["afmt"] = T1_BACK
    col.models.add_template(model, t1)

    t2 = col.models.new_template("RU - JP")
    t2["qfmt"] = T2_FRONT
    t2["afmt"] = T2_BACK
    col.models.add_template(model, t2)

    col.models.add(model)

    deck_id_passive = col.decks.id("Bunpro::Распознавание")
    deck_id_active = col.decks.id("Bunpro::Воспроизведение")
    if deck_id_passive is None or deck_id_active is None:
        raise ValueError("Не удалось создать или получить ID колод в Anki.")

    for item in grammar_items[:10]:
        examples_html_list = []
        for ex in item["examples"][:4]:
            examples_html_list.append(
                f'<div class="example-item"><div class="ex-jp">{ex["jp"]}</div><div class="ex-ru">{ex["ru"]}</div></div>'
            )
        examples_html = "".join(examples_html_list)

        note = col.new_note(model)
        note.guid = generate_guid(item["grammar"], item["jlpt"])
        note["Grammar"] = item["grammar"]
        note["JLPT"] = item["jlpt"]
        note["Meaning"] = item["meaning"]
        note["Structure"] = item["structure"]
        note["Nuance"] = item["nuance"]
        note["ExamplesHTML"] = examples_html

        col.add_note(note, deck_id_passive)

        cards = note.cards()
        if len(cards) > 1:
            card_active = cards[1]
            card_active.did = deck_id_active
            col.update_card(card_active)

    exporter = AnkiPackageExporter(col)
    output_file = "bunpro_grammar.apkg"
    exporter.exportInto(output_file)
    logger.info(f"Колода создана успешно! Имя файла: {output_file}")

finally:
    col.close()
    if exists(temp_db):
        remove(temp_db)
    if exists(temp_db + ".log"):
        remove(temp_db + ".log")
    if exists("temp_col.media"):
        rmtree("temp_col.media")
