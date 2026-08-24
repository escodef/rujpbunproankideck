import json
import logging
import re
import sqlite3
import sys
from hashlib import md5
from http import HTTPStatus
from os import getenv
from pathlib import Path
from shutil import rmtree
from time import sleep
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import requests
from anki.collection import Collection
from anki.exporting import AnkiPackageExporter
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

from anki_template import CARD_CSS, T1_BACK, T1_FRONT, T2_BACK, T2_FRONT

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="{asctime} - {levelname} - {message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M",
)
logger = logging.getLogger("BUNPRO")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
}

INDEX_URL = "https://bunpro.jp/grammar_points"
DB_FILE = "bunpro.db"
OUTPUT_FILE = Path("bunpro_grammar.apkg")
CACHE_DIR = Path("bunpro_cache")
AUDIO_DIR = Path("bunpro_audio")

session = requests.Session()
session.headers.update(HEADERS)

api_key = getenv("API_KEY")
base_url = getenv("API_URL")
if api_key is None or base_url is None:
    logger.error("API_KEY или API_URL не найден в .env")
    sys.exit(1)

client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)

conn = sqlite3.connect(DB_FILE)
conn.row_factory = sqlite3.Row
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
    url TEXT,
    examples_raw TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS translated_grammar (
    source_title TEXT PRIMARY KEY,
    grammar TEXT,
    meaning TEXT,
    structure TEXT,
    jlpt TEXT,
    nuance TEXT,
    url TEXT,
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
            "Успешно найдено и сохранено грамматических точек в БД: %d",
            len(grammar_list),
        )

    except Exception:
        logger.exception("Ошибка при получении списка грамматики")
        conn.close()
        sys.exit(1)

else:
    logger.info("Главная страница уже пропаршена...")


cursor.execute("SELECT point_name, url FROM links")
grammar_list = cursor.fetchall()

CACHE_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str) -> str:
    return "".join([c for c in name if c.isalnum() or c in (" ", "_", "-")]).strip()


def normalize_jp(text: str) -> str:
    t = re.sub(r"（[^）]*）|\([^)]*\)", "", text)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"[\s、。！？\-_・\.,!?]", "", t)


def download_audio_file(audio_url: str) -> Path | None:
    if not audio_url:
        return None
    try:
        parts = urlsplit(audio_url)
        quoted_path = quote(unquote(parts.path))
        clean_url = urlunsplit(
            (parts.scheme, parts.netloc, quoted_path, parts.query, parts.fragment)
        )

        raw_filename = unquote(parts.path.split("/")[-1])
        base_name = sanitize_filename(Path(raw_filename).stem)
        if not base_name:
            base_name = md5(audio_url.encode(), usedforsecurity=False).hexdigest()[:12]
        local_path = AUDIO_DIR / f"{base_name}.mp3"

        if not local_path.exists() or local_path.stat().st_size == 0:
            res = session.get(clean_url, timeout=15)
            if res.status_code == HTTPStatus.OK:
                with local_path.open("wb") as f:
                    f.write(res.content)
            else:
                logger.debug(
                    "Не удалось скачать аудио: %s (код %d)", clean_url, res.status_code
                )
                return None
    except Exception:
        logger.exception("Ошибка загрузки аудио %s", audio_url)
        return None
    else:
        return local_path


def extract_questions_with_audio_from_html(html_content: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    soup = BeautifulSoup(html_content, "html.parser")
    next_data = soup.find("script", id="__NEXT_DATA__")
    if not next_data or not next_data.string:
        return results

    try:
        data = json.loads(next_data.string)
        study_questions = (
            data.get("props", {})
            .get("pageProps", {})
            .get("included", {})
            .get("studyQuestions", [])
        )
        for sq in study_questions:
            content = sq.get("content", "")
            ans = sq.get("kanji_answer") or sq.get("answer") or ""
            full_jp = content.replace("____", ans)
            audio_url = sq.get("female_audio_url") or sq.get("male_audio_url") or ""
            results.append(
                {
                    "full_jp": full_jp,
                    "normalized": normalize_jp(full_jp),
                    "audio_url": audio_url,
                }
            )
    except Exception:
        logger.exception("Ошибка при парсинге studyQuestions из Next.js")
    return results


downloaded = 0
skipped = 0

for point_name, url in grammar_list:
    safe_name = (
        sanitize_filename(point_name)
        or md5(point_name.encode(), usedforsecurity=False).hexdigest()[:8]
    )
    cache_path = CACHE_DIR / f"{safe_name}.html"

    if cache_path.exists():
        skipped += 1
        continue

    try:
        res = session.get(url, headers=HEADERS, timeout=15)
        if res.status_code == HTTPStatus.OK:
            with cache_path.open("w", encoding="utf-8") as f:
                f.write(res.text)
            downloaded += 1
            logger.debug("найдена статья %s", point_name)
            sleep(2.2)
        elif res.status_code == HTTPStatus.NOT_FOUND:
            logger.error("Страница не найдена: %s", url)
        else:
            logger.error("Ошибка при загрузке %s: Код %d", point_name, res.status_code)
    except Exception:
        logger.exception(
            "Ошибка сети на %s. Перезапустите ячейку для продолжения.", point_name
        )
        break

logger.info(
    "Процесс завершен. Загружено новых: %d, Пропущено (были в кэше): %d",
    downloaded,
    skipped,
)

grammar_raw_files = [file for file in CACHE_DIR.iterdir() if file.suffix == ".html"]

cursor.execute("SELECT filename FROM extracted_grammar")
processed_files = {row[0] for row in cursor.fetchall()}

files_to_process = [f for f in grammar_raw_files if f.name not in processed_files]

filename_to_url = {}
for point_name, url in grammar_list:
    p_name = point_name
    p_url = url
    s_name = (
        sanitize_filename(p_name)
        or md5(p_name.encode(), usedforsecurity=False).hexdigest()[:8]
    )
    filename_to_url[f"{s_name}.html"] = p_url

if files_to_process:
    logger.info("Очищаем новые страницы от мусора (%d шт.)...", len(files_to_process))
    for file in grammar_raw_files:
        with file.open(encoding="utf-8") as f:
            html = f.read()
            soup = BeautifulSoup(html, "html.parser")

            title_tag = soup.find("h1")
            title = (
                title_tag.get_text(" ", strip=True)
                if title_tag
                else file.name.replace(".html", "")
            )

            jlpt_level = "Non-JLPT"
            jlpt_element = soup.find(string=re.compile(r"JLPT\s*N[1-5]", re.IGNORECASE))
            if jlpt_element:
                match = re.search(r"N[1-5]", jlpt_element, re.IGNORECASE)
                if match:
                    jlpt_level = match.group(0).upper()

            if jlpt_level == "Non-JLPT":
                breadcrumb = soup.find(
                    ["nav", "div"],
                    class_=re.compile(r"breadcrumb|lesson", re.IGNORECASE),
                )
                if breadcrumb:
                    match = re.search(r"N[1-5]", breadcrumb.get_text(), re.IGNORECASE)
                    if match:
                        jlpt_level = match.group(0).upper()

            def get_section_text(soup: BeautifulSoup, search_regex: str) -> str:
                header = soup.find(
                    ["h2", "h3"], string=re.compile(search_regex, re.IGNORECASE)
                )
                if not header:
                    header = soup.find(string=re.compile(search_regex, re.IGNORECASE))
                    if header:
                        header = header.parent
                if not header:
                    return ""
                content = []
                for sibling in header.find_all_next():
                    if sibling.name in ["h1", "h2", "h3"] and not re.search(
                        search_regex, sibling.text, re.IGNORECASE
                    ):
                        break
                    if sibling.name in ["p", "ul", "ol", "div"] and (
                        sibling.name != "div" or not sibling.find(["p", "div", "ul"])
                    ):
                        txt = sibling.get_text(" ", strip=True)
                        if txt and txt not in ["English", "Japanese"]:
                            content.append(txt)
                return "\n".join(content)

            structure_text = get_section_text(soup, r"^Structure$")
            about_text = get_section_text(soup, r"About")

            examples: list[str] = []
            next_data_script = soup.find("script", id="__NEXT_DATA__")
            if next_data_script and next_data_script.string:
                try:
                    data = json.loads(next_data_script.string)
                    study_questions = (
                        data.get("props", {})
                        .get("pageProps", {})
                        .get("included", {})
                        .get("studyQuestions", [])
                    )
                    for sq in study_questions:
                        content = sq.get("content", "")
                        ans = sq.get("kanji_answer") or sq.get("answer") or ""
                        full_jp = content.replace("____", ans)
                        clean_txt = re.sub(r"<[^>]+>", "", full_jp).strip()
                        if len(clean_txt) > 5 and re.search(
                            r"[\u3040-\u30ff\u4e00-\u9faf]", clean_txt
                        ):
                            examples.append(clean_txt)
                except Exception:
                    logger.exception("Ошибка при извлечении примеров из JSON")

            if not examples:
                for el in soup.find_all(
                    ["span", "div"],
                    class_=re.compile(r"sentence|japanese", re.IGNORECASE),
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
            current_url = filename_to_url.get(file.name, "")

            cursor.execute(
                """
                INSERT OR REPLACE INTO extracted_grammar (filename, title, jlpt, structure, about, url, examples_raw)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file.name,
                    title,
                    jlpt_level,
                    structure_text,
                    about_text,
                    current_url,
                    examples_raw_json,
                ),
            )
            conn.commit()
            logger.info("Добавлена сырая статья: %s", title)

    logger.info("Все новые HTML-страницы успешно обработаны и внесены в БД.")
else:
    logger.info("Новых HTML-файлов для извлечения не обнаружено.")

system_prompt = """Ты — профессиональный переводчик и лингвист, эксперт в японском языке.
Твоя задача — перевести предоставленную справку о грамматической конструкции на русский
язык и вернуть результат строго в формате JSON.

Требуемая структура JSON:
{
"grammar": "грамматическая конструкция на японском и только на японском, очищенная
от мусора и пояснений в скобках, сама грамматика без других лишних символов,
кратко, до 10-15 символов, например ～てこそ или ～ようとしない",
"meaning": "краткое значение на русском (строго до 20-25 символов)",
"structure": "схема присоединения на русском, кратко и понятно (строго до 15-20 символов)",
"nuance": "важные нюансы использования, различия, выжимка из предоставленного описания",
"examples": [
    {"jp": "предложение на японском с восстановленной конструкцией вместо ____", "ru": "точный перевод на русский"}
]
}

Правила:

Ответ должен быть ТОЛЬКО валидным JSON, без комментариев и разметки markdown (вроде ```json).
В поле 'examples' для каждого примера замени символ '____' (если он есть) на правильную форму этой грамматики."""

user_prompt_template = """Переведи и структурируй следующую грамматику:

Грамматика: {title}
Уровень: {jlpt}
Структура (оригинал): {structure}
Описание (оригинал): {about}
Примеры (оригинал): {examples}"""

cursor.execute("""
    SELECT title, jlpt, structure, about, url, examples_raw
    FROM extracted_grammar
    WHERE title NOT IN (SELECT source_title FROM translated_grammar)
""")
to_process_llm = cursor.fetchall()

if to_process_llm:
    logger.info(
        "Запуск разметки через LLM. Ожидает обработки: %d ", len(to_process_llm)
    )

    for title, jlpt, structure, about, url, examples_raw_str in to_process_llm:
        examples_raw = json.loads(examples_raw_str)
        logger.info("Обработка: %s (%s)...", title, jlpt)

        user_content = user_prompt_template.format(
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
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    model="gpt-oss-120b",
                    temperature=0.3,
                    top_p=1,
                    stream=False,
                    reasoning_effort="medium",
                    response_format={"type": "json_object"},
                )

                raw_text = completion.choices[0].message.content

                if raw_text:
                    parsed_json = json.loads(raw_text)
                    parsed_json["jlpt"] = jlpt
                    success = True
                    break
                else:
                    logger.info(
                        "Попытка %d/%d: Получен пустой ответ от API.",
                        attempt + 1,
                        max_retries,
                    )
            except json.JSONDecodeError:
                logger.exception("Попытка %d/%d: Ошибка API.", attempt + 1, max_retries)

            if attempt < max_retries - 1:
                logger.info("Ожидание %d сек перед повторным запросом...", retry_delay)
                sleep(retry_delay)
                retry_delay *= 2

        if success and parsed_json:
            try:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO translated_grammar (source_title, grammar, meaning, structure, jlpt, nuance, url, examples)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        parsed_json.get("grammar", title),
                        parsed_json.get("meaning", ""),
                        parsed_json.get("structure", parsed_json.get("structure", "")),
                        jlpt,
                        parsed_json.get("nuance", ""),
                        url,
                        json.dumps(parsed_json.get("examples", []), ensure_ascii=False),
                    ),
                )
                conn.commit()
                logger.info("Успешно сохранено в БД.")
            except sqlite3.Error:
                logger.exception("Ошибка записи перевода в БД для %s", title)

            sleep(6)
        else:
            logger.error(
                "Не удалось обработать %s после %d попыток. Переходим к следующему.",
                title,
                max_retries,
            )
else:
    logger.info("Все извлеченные статьи уже переведены и сохранены в БД.")

cursor.execute("SELECT title, filename FROM extracted_grammar")
title_to_filename = {row["title"]: row["filename"] for row in cursor.fetchall()}

cursor.execute(
    "SELECT source_title, grammar, jlpt, meaning, structure, nuance, url, examples FROM translated_grammar"
)
grammar_items = cursor.fetchall()


def generate_guid(grammar: str, jlpt: str) -> str:
    return md5(f"{grammar}{jlpt}".encode(), usedforsecurity=False).hexdigest()


temp_db = Path("temp_bunpro.anki2")
if temp_db.exists():
    temp_db.unlink(missing_ok=True)

if OUTPUT_FILE.exists():
    OUTPUT_FILE.unlink()

col = Collection(temp_db.absolute().as_posix())
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
        col.models.add_field(model, col.models.new_field("URL"))

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

    for item in grammar_items:
        jlpt = item["jlpt"]

        deck_id_passive = col.decks.id(f"Bunpro::Распознавание::{jlpt}")
        deck_id_active = col.decks.id(f"Bunpro::Воспроизведение::{jlpt}")
        if deck_id_passive is None or deck_id_active is None:
            raise ValueError("Не удалось создать или получить ID колод в Anki.")

        try:
            parsed_examples = json.loads(item["examples"]) if item["examples"] else []
        except json.JSONDecodeError:
            logger.exception("Ошибка парсинга примеров для %s", item["grammar"])
            parsed_examples = []

        cached_fn = title_to_filename.get(item["source_title"])
        questions_audio_meta = []
        if cached_fn:
            cached_html_file = CACHE_DIR / cached_fn
            if cached_html_file.exists():
                questions_audio_meta = extract_questions_with_audio_from_html(
                    cached_html_file.read_text(encoding="utf-8")
                )

        rendered_examples = []
        for ex in parsed_examples[:4]:
            ex_jp = ex.get("jp", "")
            ex_ru = ex.get("ru", "")
            norm_ex_jp = normalize_jp(ex_jp)

            audio_tag = ""
            matched_audio_url = ""

            for q in questions_audio_meta:
                if not q["audio_url"]:
                    continue
                if (
                    norm_ex_jp == q["normalized"]
                    or norm_ex_jp in q["normalized"]
                    or q["normalized"] in norm_ex_jp
                ):
                    matched_audio_url = q["audio_url"]
                    break

            if matched_audio_url:
                local_audio_path = download_audio_file(matched_audio_url)
                if local_audio_path and local_audio_path.exists():
                    anki_media_filename = col.media.add_file(
                        local_audio_path.absolute().as_posix()
                    )
                    audio_tag = f" [sound:{anki_media_filename}]"

            rendered_examples.append(
                f'<div class="example-item">'
                f'<div class="ex-jp">{ex_jp}{audio_tag}</div>'
                f'<div class="ex-ru">{ex_ru}</div>'
                f"</div>"
            )

        examples_html = "".join(rendered_examples)

        note = col.new_note(model)
        note.guid = generate_guid(item["grammar"], item["jlpt"])
        note["Grammar"] = item["grammar"]
        note["JLPT"] = item["jlpt"]
        note["Meaning"] = item["meaning"]
        note["Structure"] = item["structure"]
        note["Nuance"] = item["nuance"]
        note["ExamplesHTML"] = examples_html
        note["URL"] = item["url"]

        col.add_note(note, deck_id_passive)

        cards = note.cards()
        if len(cards) > 1:
            card_active = cards[1]
            card_active.did = deck_id_active
            col.update_card(card_active)

    exporter = AnkiPackageExporter(col)
    exporter.exportInto(OUTPUT_FILE.absolute().as_posix())
    logger.info("Колода создана успешно! Имя файла: %s", OUTPUT_FILE.name)

finally:
    col.close()
    if temp_db.exists():
        temp_db.unlink()
    temp_db_log = Path(temp_db.as_posix() + ".log")
    if temp_db_log.exists():
        temp_db_log.unlink()
    temp_media_db = Path("temp_bunpro.media.db2")
    if temp_media_db.exists():
        temp_media_db.unlink()
    temp_media = Path("temp_bunpro.media")
    if temp_media.exists():
        rmtree(temp_media)
