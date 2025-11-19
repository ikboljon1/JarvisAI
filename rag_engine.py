import os
import google.generativeai as genai
import numpy as np
import traceback
import time  # <--- ВАЖНО: Импорт для паузы

EMBEDDING_MODEL = 'models/text-embedding-004'


class ProjectIndexer:
    def __init__(self, api_key):
        self.chunks = []
        self.embeddings = []
        self.is_indexed = False

        if api_key and "ВАШ_" not in api_key:
            genai.configure(api_key=api_key)

    def index_project(self, root_path, progress_callback=None):
        self.chunks = []
        self.embeddings = []
        self.is_indexed = False

        extensions = {'.py', '.js', '.ts', '.html', '.css', '.java', '.cpp', '.h', '.c', '.cs', '.json'}

        if progress_callback: progress_callback("Scanning files...")

        files_content = []
        for root, _, files in os.walk(root_path):
            if '.git' in root or '__pycache__' in root or 'node_modules' in root or 'venv' in root:
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
                    except:
                        pass

        if not files_content:
            return "No code files found."

        # Чанкинг
        if progress_callback: progress_callback(f"Chunking {len(files_content)} files...")

        temp_chunks = []
        CHUNK_SIZE = 1500  # Чуть увеличим размер чанка

        for fname, text in files_content:
            # Если файл маленький, берем целиком
            if len(text) < CHUNK_SIZE:
                formatted = f"File: {fname}\nCode:\n{text}"
                temp_chunks.append(formatted)
            else:
                # Если большой, режем
                for i in range(0, len(text), CHUNK_SIZE):
                    chunk_text = text[i:i + CHUNK_SIZE]
                    formatted = f"File: {fname}\nCode:\n{chunk_text}"
                    temp_chunks.append(formatted)

        # Эмбеддинг с защитой от зависания
        if progress_callback: progress_callback(f"Embedding {len(temp_chunks)} chunks...")

        valid_embeddings = []
        valid_chunks = []

        for i, chunk in enumerate(temp_chunks):
            # Обновляем статус каждые 5 файлов
            if i % 5 == 0 and progress_callback:
                progress_callback(f"Embedding: {i}/{len(temp_chunks)}...")

            try:
                # <--- ГЛАВНОЕ ИСПРАВЛЕНИЕ: ПАУЗА --->
                time.sleep(0.5)  # Ждем 0.5 сек, чтобы не превысить лимит

                result = genai.embed_content(
                    model=EMBEDDING_MODEL,
                    content=chunk,
                    task_type="retrieval_document"
                )
                embedding = result['embedding']
                valid_embeddings.append(embedding)
                valid_chunks.append(chunk)

            except Exception as e:
                print(f"Error embedding chunk {i}: {e}")
                # Не прерываем процесс, просто пропускаем этот кусок
                continue

        if valid_embeddings:
            self.embeddings = np.array(valid_embeddings)
            self.chunks = valid_chunks
            self.is_indexed = True
            return f"Success! Indexed {len(self.chunks)} chunks."
        else:
            return "Indexing failed (API issues)."

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
        except:
            return []