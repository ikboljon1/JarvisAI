# llm_client.py
import os
import json
import google.generativeai as genai
import traceback

# --- КОНФИГУРАЦИЯ ---
try:
    # Пытаемся импортировать ключ из config.py
    from config import API_KEY
except ImportError:
    API_KEY = "AIzaSyBlaBFxx2cwnKpmG-OR9Nu32OUPvM1Zeis"

# !!! ВАЖНО: Используем существующую и быструю модель !!!
# gemini-2.5-pro еще не вышла публично, используем 1.5-flash
CHAT_MODEL_NAME = 'gemini-2.5-pro'

# --- ИНИЦИАЛИЗАЦИЯ ---
is_api_ready = False
chat_model = None

try:
    # Если ключ не в конфиге, ищем в переменных окружения
    if "ВАШ_" in API_KEY or not API_KEY:
        API_KEY = os.environ.get("GEMINI_API_KEY", "")

    if API_KEY:
        genai.configure(api_key=API_KEY)
        chat_model = genai.GenerativeModel(CHAT_MODEL_NAME)
        is_api_ready = True
        print(f">>> LLM Client: Модель {CHAT_MODEL_NAME} готова к работе.")
    else:
        print(">>> LLM Client: API Key не найден.")
except Exception as e:
    print(f"Init Error: {e}")


# ======================================================
# 1. ФУНКЦИИ АГЕНТА (ПЛАНИРОВАНИЕ И ВЫПОЛНЕНИЕ)
# ======================================================

def get_strategic_plan(user_request: str) -> dict:
    """
    Анализирует запрос и создает пошаговый план разработки в формате JSON.
    """
    if not is_api_ready or chat_model is None:
        return {"error": "API Key not set", "steps": []}

    prompt = f"""
    Ты — Tech Lead и Архитектор ПО.
    Твоя задача — разбить задачу пользователя на логические этапы разработки.

    ЗАДАЧА ПОЛЬЗОВАТЕЛЯ: {user_request}

    ТРЕБОВАНИЯ:
    1. Верни ответ СТРОГО в формате JSON.
    2. Не пиши никакого кода, только план действий.
    3. Разбей задачу на 3-6 шагов (например: Структура проекта, База данных, Логика, UI).

    ФОРМАТ ОТВЕТА (JSON):
    {{
        "project_name": "Название проекта",
        "steps": [
            "Шаг 1: Создать базовую структуру файлов...",
            "Шаг 2: Реализовать модели данных...",
            "Шаг 3: Настроить маршрутизацию..."
        ]
    }}
    """

    try:
        response = chat_model.generate_content(prompt)
        text = response.text
        # Очистка от Markdown (если модель вернула ```json ... ```)
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"Plan Error: {e}")
        # Возвращаем аварийный план, чтобы программа не упала
        return {
            "project_name": "Task Execution",
            "steps": [f"Выполнить задачу: {user_request}"]
        }


def execute_step(current_step: str, full_task: str, rag_context: list) -> str:
    """
    Пишет код для конкретного шага, учитывая найденный контекст (RAG).
    """
    if not is_api_ready or chat_model is None:
        return "Error: API Key not set."

    # Собираем промпт для исполнителя
    parts = [
        "Ты — AI Developer (Cursor Agent). Мы разрабатываем проект поэтапно.",
        f"ГЛАВНАЯ ЦЕЛЬ ПРОЕКТА: {full_task}",
        f"ТЕКУЩАЯ ЗАДАЧА (ЭТАП): {current_step}",
        "",
        "ИНСТРУКЦИЯ:",
        "1. Напиши или измени файлы, необходимые ТОЛЬКО для этого этапа.",
        "2. Если нужно создать файл, используй формат:",
        "### FILE: path/to/filename.ext",
        "код...",
        "### END_FILE",
        "3. ВАЖНО:",
        "   - Всегда указывай полный путь (например: app/models.py).",
        "   - Не используй ```python или ``` внутри блока ### FILE.",
        "   - Пиши полностью рабочий код."
    ]

    # Добавляем контекст из RAG (чтобы агент видел существующий код)
    if rag_context:
        parts.append("\n=== 🗄️ КОНТЕКСТ ПРОЕКТА (Существующий код) ===")
        parts.append("(Используй этот код, чтобы понимать структуру и не дублировать файлы)")
        for chunk in rag_context:
            parts.append(f"{chunk}\n---")

    prompt = "\n".join(parts)

    try:
        response = chat_model.generate_content(prompt)
        return response.text
    except Exception as e:
        traceback.print_exc()
        return f"Error executing step: {e}"


# ======================================================
# 2. ФУНКЦИИ ОБЫЧНОГО ЧАТА (HELPER METHODS)
# ======================================================

def get_chat_response(full_prompt: str) -> str:
    """Базовая функция отправки сообщения (для простых вопросов)."""
    if not is_api_ready or chat_model is None:
        return "⚠️ Ошибка: API Key не установлен."

    try:
        response = chat_model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        traceback.print_exc()
        return f"API Error: {e}"


def build_context_prompt(user_message: str, context_files: dict, active_file_data: tuple, rag_context: list) -> str:
    """
    Собирает промпт для обычного режима чата (не агентного).
    Принимает кортеж (имя_файла, код).
    """
    active_filename = "None"
    active_code = ""

    # Безопасная распаковка
    try:
        if active_file_data and isinstance(active_file_data, tuple):
            active_filename = active_file_data[0]
            active_code = active_file_data[1]
    except:
        pass

    parts = [
        "Ты — Помощник по коду.",
        "Отвечай на вопросы пользователя, используя контекст.",
        f"Пользователь сейчас смотрит файл: {active_filename}"
    ]

    if rag_context:
        parts.append("\n=== RAG Context ===")
        for chunk in rag_context:
            parts.append(f"{chunk}\n---")

    if active_code:
        parts.append(f"\n=== Active File Content ({active_filename}) ===\n{active_code}")

    parts.append(f"\n=== ВОПРОС ===\n{user_message}")

    return "\n".join(parts)


def get_code_review(code: str) -> str:
    """Запрос на ревью кода."""
    prompt = (
        "Выполни Code Review этого фрагмента. "
        "Найди баги, уязвимости и проблемы со стилем. "
        "Будь краток.\n\n"
        f"{code}"
    )
    return get_chat_response(prompt)