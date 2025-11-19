# llm_client.py
import os
import google.generativeai as genai
import traceback
from config import API_KEY
# ==========================================
# ВСТАВЬТЕ СЮДА ВАШ КЛЮЧ API
API_KEY = API_KEY
# ==========================================

# Модели
CHAT_MODEL_NAME = 'gemini-2.5-pro'

# Инициализация
is_api_ready = False
try:
    # Пытаемся взять из переменных окружения, если в коде заглушка
    if "ВАШ_" in API_KEY or not API_KEY:
        API_KEY = os.environ.get("GEMINI_API_KEY", "")

    if API_KEY:
        genai.configure(api_key=API_KEY)
        chat_model = genai.GenerativeModel(CHAT_MODEL_NAME)
        is_api_ready = True
        print(f">>> LLM Client: Модель {CHAT_MODEL_NAME} готова.")
    else:
        chat_model = None
        print(">>> LLM Client: API Key не найден.")
except Exception as e:
    print(f"Init Error: {e}")
    chat_model = None


def get_chat_response(full_prompt: str) -> str:
    """Отправка сообщения в чат."""
    if not is_api_ready or chat_model is None:
        return "⚠️ Ошибка: API Key не установлен или модель не инициализирована."

    try:
        # stream=False для простоты, можно включить stream=True для эффекта печатания
        response = chat_model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        traceback.print_exc()
        return f"API Error: {e}"


# ИСПРАВЛЕНИЕ 2: Принимаем active_file_data (кортеж), а не просто строку
def build_context_prompt(user_message: str, context_files: dict, active_file_data: tuple, rag_context: list) -> str:
    """Сборка полного промпта из всех источников."""

    # Распаковываем данные активного файла
    # active_file_data приходит из main.py как (filename, code)
    if active_file_data and isinstance(active_file_data, tuple):
        active_filename, active_code = active_file_data
    else:
        active_filename, active_code = "None", ""

    parts = [
        "Ты — AI-Агент в редакторе кода (Cursor Clone).",
        "ТВОЯ СУПЕР-СПОСОБНОСТЬ: Ты можешь создавать и редактировать файлы.",
        "",
        "!!! ИНСТРУКЦИЯ ПО СОЗДАНИЮ И РЕДАКТИРОВАНИЮ !!!",
        "1. Формат ответа для кода:",
        "### FILE: filename.ext",
        "code content here...",
        "### END_FILE",
        "",
        "2. ЗАПРЕТЫ:",
        "   - ЗАПРЕЩЕНО использовать ```python, ```bash или ``` внутри блока файла.",
        "   - Не добавляй markdown-комментарии внутрь блока ### FILE.",
        "",
        "3. ПРАВИЛА РЕДАКТИРОВАНИЯ:",
        "   - Если пользователь просит изменить текущий файл, верни ВЕСЬ код файла целиком.",
        f"  - ОБЯЗАТЕЛЬНО используй имя файла: {active_filename} (если редактируешь его).",
        "   - Не придумывай новые имена файлов, если задача касается текущего.",
        "   - Будь краток в объяснениях."
    ]

    # 1. RAG Context (Найденное в проекте)
    if rag_context:
        parts.append("\n=== 🗄️ НАЙДЕНО В БАЗЕ ЗНАНИЙ (RAG) ===")
        for chunk in rag_context:
            parts.append(f"{chunk}\n---")

    # 2. Явно упомянутые файлы (@files)
    if context_files:
        parts.append("\n=== 📎 ФАЙЛЫ ИЗ КОНТЕКСТА (@) ===")
        for fname, content in context_files.items():
            parts.append(f"Файл: {fname}\n\n{content}\n")

    # 3. Активный файл (где курсор) - ИСПРАВЛЕННАЯ ЛОГИКА
    if active_code:
        parts.append(f"\n=== 📝 АКТИВНЫЙ ФАЙЛ (Пользователь смотрит сюда) ===")
        parts.append(f"Имя файла: {active_filename}")
        parts.append("Содержимое:")
        parts.append(f"{active_code}\n")
        parts.append(f"(Если меняешь этот код, верни блок ### FILE: {active_filename})")

    # 4. Вопрос
    parts.append(f"\n=== 👤 ВОПРОС ПОЛЬЗОВАТЕЛЯ ===\n{user_message}")

    return "\n".join(parts)


def get_code_review(code: str) -> str:
    """Запрос на ревью."""
    prompt = (
        "Выполни Code Review этого фрагмента. "
        "Найди баги, уязвимости, проблемы с производительностью и стилем. "
        "Предложи исправленный вариант кода.\n\n"
        f"{code}"
    )
    return get_chat_response(prompt)