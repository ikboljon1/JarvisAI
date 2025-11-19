import sys
import os
import re
import time
from PyQt6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget,
                             QFileDialog, QTextBrowser, QLineEdit, QPushButton,
                             QTreeView, QTabWidget, QSplitter, QLabel,
                             QCompleter, QMessageBox, QProgressBar)
from PyQt6.QtGui import QAction, QFileSystemModel, QColor, QFont, QKeySequence
from PyQt6.QtCore import Qt, QDir, QStringListModel, QThread, pyqtSignal, QProcess

from PyQt6.Qsci import QsciScintilla, QsciLexerPython, QsciLexerJavaScript

# Импорты модулей (должны лежать рядом)
import llm_client
from llm_client import get_chat_response, build_context_prompt, API_KEY
from rag_engine import ProjectIndexer


# ==========================================
# 1. АГЕНТ-ВОРКЕР (МОЗГ: ПЛАНИРОВАНИЕ + ИСПОЛНЕНИЕ)
# ==========================================
class AgentWorker(QThread):
    """
    Этот поток реализует цикл:
    1. Получить План (JSON).
    2. Показать План.
    3. Для каждого шага: Поиск в RAG -> Генерация кода -> Сохранение файлов.
    """
    log_signal = pyqtSignal(str)  # Отправка HTML в чат
    finished_signal = pyqtSignal()  # Сигнал завершения

    def __init__(self, user_request, project_path, rag_engine):
        super().__init__()
        self.request = user_request
        self.path = project_path
        self.rag_engine = rag_engine

    def run(self):
        # --- ФАЗА 1: ПЛАНИРОВАНИЕ ---
        self.log_signal.emit(f"""
        <div style='background:#2d2d2d; border-left:4px solid #a371f7; padding:10px; margin:10px 0;'>
            <b>🧠 PLANNING PHASE:</b> <i style='color:#ccc'>Thinking about architecture...</i>
        </div>
        """)

        # Вызов Стратега из llm_client
        plan_data = llm_client.get_strategic_plan(self.request)

        steps = plan_data.get("steps", [])
        proj_name = plan_data.get("project_name", "Project")

        if not steps:
            self.log_signal.emit(
                f"<span style='color:red'>Failed to generate plan. Error: {plan_data.get('error')}</span>")
            self.finished_signal.emit()
            return

        # Визуализация плана
        steps_html = "".join([f"<li style='margin-bottom:5px;'>{step}</li>" for step in steps])
        self.log_signal.emit(f"""
        <div style='border:1px solid #444; background:#1e1e1e; padding:10px; margin:10px 0; border-radius:5px;'>
            <h3 style='color:#a371f7; margin-top:0;'>📋 STRATEGY: {proj_name}</h3>
            <ul style='color:#ccc; padding-left:20px;'>{steps_html}</ul>
        </div>
        """)

        # --- ФАЗА 2: ВЫПОЛНЕНИЕ ПО ШАГАМ ---
        total_steps = len(steps)
        for i, step in enumerate(steps):
            step_num = i + 1
            self.log_signal.emit(
                f"<hr><div style='color:#61afef'><b>🚀 EXECUTING PHASE {step_num}/{total_steps}:</b><br><i>{step}</i></div>")

            # 1. RAG ПОИСК (Чтение памяти проекта)
            rag_context = []
            if self.rag_engine.is_indexed:
                # Ищем код, связанный с текущей задачей, чтобы не дублировать и не ломать
                rag_context = self.rag_engine.search(step, top_k=4)
                if rag_context:
                    self.log_signal.emit(
                        f"<small style='color:#666'>🔍 Reading {len(rag_context)} related code blocks...</small>")

            # 2. ГЕНЕРАЦИЯ КОДА (Вызов Исполнителя)
            response_text = llm_client.execute_step(step, self.request, rag_context)

            # 3. СОХРАНЕНИЕ ФАЙЛОВ
            files_changed = self.process_files(response_text)

            if not files_changed:
                self.log_signal.emit("<span style='color:gray; font-size:10px;'>No files modified in this step.</span>")

            # Пауза, чтобы не перегрузить API Google
            time.sleep(2)

        self.log_signal.emit("<br><br><b style='color:#98c379'>✅ MISSION COMPLETE!</b>")
        self.finished_signal.emit()

    def process_files(self, text):
        """Парсит ответ, ищет блоки ### FILE и сохраняет их."""
        pattern = re.compile(r"### FILE: (.*?)\n(.*?)### END_FILE", re.DOTALL)
        matches = list(pattern.finditer(text))

        if not matches:
            return False

        for m in matches:
            fn = m.group(1).strip()
            content = m.group(2)

            # Очистка от Markdown блоков кода
            content = content.replace("```python", "").replace("```javascript", "").replace("```html", "").replace(
                "```", "").strip()

            full_p = os.path.join(self.path, fn)

            try:
                # Создаем вложенные папки, если их нет
                os.makedirs(os.path.dirname(full_p), exist_ok=True)

                # Проверяем статус (Создан или Обновлен)
                status = "📝 Updated" if os.path.exists(full_p) else "✨ Created"
                color = "#e5c07b" if os.path.exists(full_p) else "#98c379"

                # Записываем файл
                with open(full_p, 'w', encoding='utf-8') as f:
                    f.write(content)

                # Выводим красивую карточку в чат
                self.log_signal.emit(f"""
                <div style='margin-left:15px; border-left:3px solid {color}; padding-left:8px; margin-top:4px; background:#252526;'>
                    <b style='color:{color}'>{status}:</b> <span style='color:#ddd; font-family:Consolas;'>{fn}</span>
                </div>
                """)

            except Exception as e:
                self.log_signal.emit(f"<span style='color:red'>Error writing {fn}: {e}</span>")

        return True


# ==========================================
# 2. ВСПОМОГАТЕЛЬНЫЕ ВОРКЕРЫ
# ==========================================
class IndexerWorker(QThread):
    """Фоновая индексация проекта для RAG."""
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str)

    def __init__(self, indexer, folder_path):
        super().__init__()
        self.indexer = indexer
        self.folder_path = folder_path

    def run(self):
        res = self.indexer.index_project(self.folder_path, lambda m: self.progress_signal.emit(m))
        self.finished_signal.emit(res)


# ==========================================
# 3. ЭЛЕМЕНТЫ UI (РЕДАКТОР, ТЕРМИНАЛ)
# ==========================================
class CodeEditor(QsciScintilla):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setUtf8(True)
        font = self.font();
        font.setFamily("Consolas");
        font.setPointSize(11);
        self.setFont(font)
        self.setMarginType(0, QsciScintilla.MarginType.NumberMargin);
        self.setMarginWidth(0, "0000")
        self.setTabWidth(4);
        self.setAutoIndent(True)
        self.setColor(Qt.GlobalColor.white);
        self.setPaper(QColor("#1e1e1e"));
        self.setCaretForegroundColor(Qt.GlobalColor.white)

    def set_lexer_by_filename(self, filename):
        ext = os.path.splitext(filename)[1].lower()
        if ext == '.py':
            self.setLexer(QsciLexerPython(self))
        elif ext in ['.js', '.ts', '.json']:
            self.setLexer(QsciLexerJavaScript(self))
        else:
            self.setLexer(None)


class TerminalPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        l = QVBoxLayout(self);
        l.setContentsMargins(0, 0, 0, 0);
        l.setSpacing(0)
        self.console = QTextBrowser()
        self.console.setStyleSheet("background:#1e1e1e; color:#ccc; border:none; font-family:Consolas; font-size:12px;")
        l.addWidget(self.console)
        self.inp = QLineEdit()
        self.inp.setStyleSheet("background:#252526; color:white; border:none; padding:5px; font-family:Consolas;")
        self.inp.setPlaceholderText("> Terminal...")
        self.inp.returnPressed.connect(self.run_cmd);
        l.addWidget(self.inp)
        self.proc = QProcess(self);
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.read_out);
        self.proc.start("cmd.exe")

    def run_cmd(self):
        cmd = self.inp.text();
        self.inp.clear()
        try:
            self.proc.write((cmd + "\n").encode('cp866'))
        except:
            self.proc.write((cmd + "\n").encode('utf-8'))

    def read_out(self):
        try:
            t = self.proc.readAllStandardOutput().data().decode('cp866')
        except:
            t = ""
        self.console.append(t)

    def set_cwd(self, path):
        if self.proc.state() == QProcess.ProcessState.Running:
            drive = os.path.splitdrive(path)[0]
            if drive: self.proc.write(f"{drive}\n".encode('cp866'))
            self.proc.write(f"cd \"{path}\"\n".encode('cp866'))


# ==========================================
# 4. ГЛАВНОЕ ОКНО
# ==========================================
class AIEditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cursor Clone (Autonomous Agent)")
        self.resize(1400, 900)
        self.current_project_path = None
        self.rag_engine = ProjectIndexer(API_KEY)
        self.agent_worker = None

        # --- Layout ---
        self.v_split = QSplitter(Qt.Orientation.Vertical);
        self.setCentralWidget(self.v_split)
        self.top_split = QSplitter(Qt.Orientation.Horizontal);
        self.v_split.addWidget(self.top_split)

        # 1. Files
        self.fmodel = QFileSystemModel();
        self.fmodel.setRootPath(QDir.rootPath())
        self.tree = QTreeView();
        self.tree.setModel(self.fmodel);
        self.tree.setHeaderHidden(True)
        for i in range(1, 4): self.tree.setColumnHidden(i, True)
        self.tree.doubleClicked.connect(self.open_file)
        self.top_split.addWidget(self.tree)

        # 2. Tabs (Code)
        self.tabs = QTabWidget();
        self.tabs.setTabsClosable(True);
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(lambda i: self.tabs.removeTab(i))
        self.top_split.addWidget(self.tabs)

        # 3. Chat
        chat_w = QWidget();
        cl = QVBoxLayout(chat_w);
        cl.setContentsMargins(5, 5, 5, 5)
        self.chat_out = QTextBrowser();
        self.chat_out.setOpenLinks(False)
        self.chat_out.anchorClicked.connect(self.on_chat_link_clicked)
        cl.addWidget(self.chat_out)

        self.chat_in = QLineEdit();
        self.chat_in.setPlaceholderText("Agent instruction (e.g. 'Create a Tetris game')...")
        self.chat_in.returnPressed.connect(self.start_agent)
        cl.addWidget(self.chat_in)

        self.top_split.addWidget(chat_w)
        self.top_split.setSizes([250, 800, 400])

        # 4. Terminal
        self.term = TerminalPanel();
        self.v_split.addWidget(self.term)
        self.v_split.setSizes([800, 200])

        # Menu (ИСПРАВЛЕНО!)
        m = self.menuBar().addMenu("&File")

        # Open
        open_action = QAction("Open Project...", self)
        open_action.triggered.connect(self.open_folder)
        m.addAction(open_action)

        # Save
        save_action = QAction("Save", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_file)
        m.addAction(save_action)

        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #252526; color: #ccc; }
            QTextBrowser { font-family: 'Segoe UI', sans-serif; font-size: 13px; }
            QTreeView { border: none; background: #252526; }
            QLineEdit { background: #3c3c3c; border: 1px solid #555; padding: 5px; color: white; }
        """)
        self.tree.setRootIndex(self.fmodel.index(os.getcwd()))

    # --- LOGIC ---

    def open_folder(self):
        f = QFileDialog.getExistingDirectory(self, "Open Project")
        if f:
            self.current_project_path = f
            self.setWindowTitle(f"Agent - {os.path.basename(f)}")
            self.tree.setRootIndex(self.fmodel.index(f))
            self.term.set_cwd(f)
            # Запуск индексации при открытии
            self.idx_worker = IndexerWorker(self.rag_engine, f)
            self.idx_worker.start()

    def start_agent(self):
        text = self.chat_in.text().strip()
        if not text: return
        if not self.current_project_path:
            QMessageBox.warning(self, "Error", "Open a project folder first!")
            return

        self.chat_in.clear()
        # Отображаем вопрос
        self.chat_out.append(
            f"<div style='text-align:right; margin:10px;'><span style='background:#0e639c; color:white; padding:8px; border-radius:10px;'>{text}</span></div>")

        # Блокируем ввод
        self.chat_in.setEnabled(False)
        self.chat_in.setPlaceholderText("Agent is working... Please wait.")

        # ЗАПУСК АГЕНТА
        # Передаем rag_engine, чтобы агент мог видеть код
        self.agent_worker = AgentWorker(text, self.current_project_path, self.rag_engine)
        self.agent_worker.log_signal.connect(self.append_html)
        self.agent_worker.finished_signal.connect(self.on_agent_done)
        self.agent_worker.start()

    def on_agent_done(self):
        self.chat_in.setEnabled(True)
        self.chat_in.setPlaceholderText("Agent instruction...")
        self.chat_in.setFocus()
        # После завершения работы агента полезно переиндексировать новые файлы
        self.idx_worker = IndexerWorker(self.rag_engine, self.current_project_path)
        self.idx_worker.start()

    def append_html(self, html):
        self.chat_out.append(html)
        sb = self.chat_out.verticalScrollBar();
        sb.setValue(sb.maximum())

    def open_file(self, idx):
        p = self.fmodel.filePath(idx)
        if not os.path.isdir(p):
            self.add_tab(p)

    def on_chat_link_clicked(self, url):
        # Для кликов по ссылкам файлов в будущем (сейчас просто текст)
        pass

    def add_tab(self, path):
        # Проверка дубликатов
        for i in range(self.tabs.count()):
            if self.tabs.tabToolTip(i) == path:
                self.tabs.setCurrentIndex(i)
                return

        ed = CodeEditor();
        ed.set_lexer_by_filename(path)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                ed.setText(f.read())
            self.tabs.addTab(ed, os.path.basename(path))
            self.tabs.setTabToolTip(self.tabs.count() - 1, path)
            self.tabs.setCurrentWidget(ed)
        except:
            pass

    def save_file(self):
        ed = self.tabs.currentWidget()
        if ed:
            p = self.tabs.tabToolTip(self.tabs.currentIndex())
            with open(p, 'w', encoding='utf-8') as f: f.write(ed.text())
            self.chat_out.append(f"<small style='color:gray'>Saved: {os.path.basename(p)}</small>")

    def get_active_file_info(self):
        """Возвращает (имя, код) текущего файла. Используется для обычного чата."""
        w = self.tabs.currentWidget()
        if w:
            fp = self.tabs.tabToolTip(self.tabs.currentIndex())
            return (os.path.basename(fp), w.text())
        return None


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = AIEditorWindow()
    w.show()
    sys.exit(app.exec())