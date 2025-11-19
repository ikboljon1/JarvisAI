import os
import google.generativeai as genai
import numpy as np
import traceback
import time

# Используем модель text-embedding-004 (она стабильнее для кода)
EMBEDDING_MODEL = 'models/text-embedding-004'


class ProjectIndexer:
    def __init__(self, api_key):
        self.chunks = []
        self.embeddings = []
        self.is_indexed = False

        # Чистим ключ и инициализируем
        if api_key:
            genai.configure(api_key=api_key.strip())

    def index_project(self, root_path, progress_callback=None):
        print("\n=== НАЧАЛО ИНДЕКСАЦИИ (RETRY MODE) ===")
        self.chunks = []
        self.embeddings = []
        self.is_indexed = False

        extensions = {'.py', '.js', '.ts', '.html', '.css', '.java', '.cpp', '.h', '.c', '.cs', '.json', '.md', '.txt'}

        if progress_callback: progress_callback("Scanning files...")

        files_content = []
        print(f"[DEBUG] Сканирование папки: {root_path}")

        # 1. Сбор файлов
        for root, _, files in os.walk(root_path):
            if '.git' in root or '__pycache__' in root or 'node_modules' in root or 'venv' in root or '.idea' in root:
                continue

            for file in files:
                if os.path.splitext(file)[1].lower() in extensions:
                    full_path = os.path.join(root, file)
                    try:
                        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                            text = f.read()
                            if text.strip():
                                rel_path = os.path.relpath(full_path, root_path)
                                files_content.append((rel_path, text))
                    except Exception as e:
                        print(f"[ERROR] Не удалось прочитать файл {file}: {e}")

        if not files_content:
            print("[DEBUG] Файлы кода не найдены.")
            return "No code files found."

        print(f"[DEBUG] Найдено {len(files_content)} файлов.")

        # 2. Чанкинг (Разбиение на части)
        if progress_callback: progress_callback(f"Chunking {len(files_content)} files...")

        temp_chunks = []
        CHUNK_SIZE = 2000

        for fname, text in files_content:
            if len(text) < CHUNK_SIZE:
                formatted = f"File: {fname}\nCode:\n{text}"
                temp_chunks.append(formatted)
            else:
                for i in range(0, len(text), CHUNK_SIZE):
                    chunk_text = text[i:i + CHUNK_SIZE]
                    formatted = f"File: {fname}\nCode:\n{chunk_text}"
                    temp_chunks.append(formatted)

        total_chunks = len(temp_chunks)
        print(f"[DEBUG] Создано {total_chunks} чанков. Начинаем отправку...")

        if progress_callback: progress_callback(f"Embedding {total_chunks} chunks...")

        valid_embeddings = []
        valid_chunks = []

        # 3. Отправка с повторными попытками (Retry Logic)
        for i, chunk in enumerate(temp_chunks):
            file_name_in_chunk = chunk.split('\n')[0]

            # --- ЦИКЛ ПОВТОРНЫХ ПОПЫТОК ---
            max_retries = 3
            success = False

            for attempt in range(max_retries):
                try:
                    # Лог текущей попытки
                    attempt_msg = f"(Попытка {attempt + 1})" if attempt > 0 else ""
                    print(f"[DEBUG] {i + 1}/{total_chunks} | {file_name_in_chunk} {attempt_msg} ... ", end='')

                    if progress_callback and attempt == 0:
                        progress_callback(f"Embedding: {i + 1}/{total_chunks}...")
                    elif progress_callback and attempt > 0:
                        progress_callback(f"Retrying {i + 1}/{total_chunks} (Error 500/429)...")

                    # Пауза: 1.5 сек обычно, 5 сек если была ошибка
                    wait_time = 1.5 if attempt == 0 else 5.0
                    time.sleep(wait_time)

                    print("API... ", end='')

                    # ЗАПРОС К GOOGLE
                    result = genai.embed_content(
                        model=EMBEDDING_MODEL,
                        content=chunk,
                        task_type="retrieval_document"
                    )

                    if 'embedding' in result:
                        print("OK!")
                        embedding = result['embedding']
                        valid_embeddings.append(embedding)
                        valid_chunks.append(chunk)
                        success = True
                        break  # Выходим из цикла attempt, идем к следующему чанку
                    else:
                        print("FAIL (Empty response)")
                        # Если ответ пустой, пробуем еще раз (цикл продолжится)

                except Exception as e:
                    error_str = str(e)
                    print(f"\n   [WARN] Ошибка API: {error_str}")
                    # Если это последняя попытка, то всё, сдаемся по этому чанку
                    if attempt == max_retries - 1:
                        print(f"   [ERROR] Пропуск чанка {i + 1} после {max_retries} попыток.")
                    else:
                        print("   [INFO] Ждем 5 секунд и пробуем снова...")

            if not success:
                # Если даже после 3 попыток не вышло, идем дальше, но в лог записали
                pass

        # Итог
        print(f"[DEBUG] ИТОГ: Успешно {len(valid_embeddings)} из {total_chunks}")

        if valid_embeddings:
            self.embeddings = np.array(valid_embeddings)
            self.chunks = valid_chunks
            self.is_indexed = True
            return f"Success! Indexed {len(self.chunks)} chunks."
        else:
            return "Indexing failed."

    def search(self, query, top_k=4):
        if not self.is_indexed or len(self.embeddings) == 0:
            return []
        try:
            query_emb = genai.embed_content(
                model=EMBEDDING_MODEL,
                content=query,
                task_type="retrieval_query"
            )['embedding']

            scores = np.dot(self.embeddings, np.array(query_emb))
            top_indices = np.argsort(scores)[-top_k:][::-1]

            results = []
            for idx in top_indices:
                results.append(self.chunks[idx])
            return results
        except Exception as e:
            print(f"Search error: {e}")
            return []