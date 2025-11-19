import sys
import os
import re
from PyQt6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget,
                             QFileDialog, QTextBrowser, QLineEdit, QPushButton,
                             QTreeView, QTabWidget, QSplitter, QLabel,
                             QCompleter, QMessageBox)
from PyQt6.QtGui import QAction, QFileSystemModel, QColor, QTextCursor, QKeySequence, QShortcut
from PyQt6.QtCore import Qt, QDir, QStringListModel, QThread, pyqtSignal, QProcess

from PyQt6.Qsci import QsciScintilla, QsciLexerPython, QsciLexerJavaScript

# Импорты (rag_engine.py должен лежать рядом)
from llm_client import get_chat_response, build_context_prompt, API_KEY
from rag_engine import ProjectIndexer


# --- Worker ---
class IndexerWorker(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str)

    def __init__(self, indexer, folder_path):
        super().__init__()
        self.indexer = indexer
        self.folder_path = folder_path

    def run(self):
        res = self.indexer.index_project(self.folder_path, lambda m: self.progress_signal.emit(m))
        self.finished_signal.emit(res)


# --- Editor ---
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


# --- Terminal ---
class TerminalPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        l = QVBoxLayout(self);
        l.setContentsMargins(0, 0, 0, 0);
        l.setSpacing(0)

        self.console_output = QTextBrowser()
        self.console_output.setStyleSheet(
            "background:#1e1e1e; color:#d4d4d4; font-family:'Consolas'; border:none; border-top:1px solid #333;")
        self.console_output.setOpenExternalLinks(True)
        l.addWidget(self.console_output)

        self.input_line = QLineEdit()
        self.input_line.setStyleSheet(
            "background:#1e1e1e; color:#fff; font-family:'Consolas'; border:none; border-top:1px solid #333; padding:4px;")
        self.input_line.setPlaceholderText("> Terminal...")
        self.input_line.returnPressed.connect(self.send_command)
        l.addWidget(self.input_line)

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.start("cmd.exe")

    def set_working_directory(self, path):
        if self.process.state() == QProcess.ProcessState.Running:
            # Меняем диск и папку
            drive = os.path.splitdrive(path)[0]
            if drive: self.process.write(f"{drive}\n".encode('cp866'))
            self.process.write(f"cd \"{path}\"\n".encode('cp866'))

    def send_command(self):
        cmd = self.input_line.text()
        if not cmd: return
        self.input_line.clear()
        full = cmd + "\n"
        try:
            self.process.write(full.encode('cp866'))
        except:
            self.process.write(full.encode('utf-8'))

    def read_output(self):
        data = self.process.readAllStandardOutput().data()
        try:
            text = data.decode('cp866')
        except:
            text = data.decode('utf-8', errors='ignore')
        c = self.console_output.textCursor();
        c.movePosition(QTextCursor.MoveOperation.End)
        c.insertText(text);
        self.console_output.setTextCursor(c);
        self.console_output.ensureCursorVisible()


# --- Main Window ---
class AIEditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cursor Clone (Fixed Save & Clean Code)")
        self.resize(1400, 900)
        self.current_project_path = None
        self.rag_engine = ProjectIndexer(API_KEY)
        self.indexer_thread = None

        # Layout setup
        self.v_split = QSplitter(Qt.Orientation.Vertical);
        self.setCentralWidget(self.v_split)
        self.top_split = QSplitter(Qt.Orientation.Horizontal);
        self.v_split.addWidget(self.top_split)

        # 1. Files
        self._setup_file_explorer()
        # 2. Tabs
        self.tabs = QTabWidget();
        self.tabs.setTabsClosable(True);
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.top_split.addWidget(self.tabs)
        # 3. Chat
        self._setup_chat_panel()
        self.top_split.setSizes([250, 800, 400])

        # 4. Terminal
        self.terminal = TerminalPanel();
        self.v_split.addWidget(self.terminal)
        self.v_split.setSizes([800, 200])

        self._create_menu()

        # Горячая клавиша Ctrl+S для сохранения
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_shortcut.activated.connect(self.save_current_file)

        self.setStyleSheet("QMainWindow, QWidget { background-color: #252526; color: #ccc; }")
        self._set_tree_root(os.getcwd())

    def _setup_file_explorer(self):
        self.file_model = QFileSystemModel()
        self.file_model.setFilter(QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs | QDir.Filter.Files)
        self.file_model.setRootPath(QDir.rootPath())
        self.tree_view = QTreeView();
        self.tree_view.setModel(self.file_model)
        self.tree_view.setHeaderHidden(True)
        for i in range(1, 4): self.tree_view.setColumnHidden(i, True)
        self.tree_view.doubleClicked.connect(self.on_file_double_clicked)
        self.top_split.addWidget(self.tree_view)

    def _setup_chat_panel(self):
        w = QWidget();
        l = QVBoxLayout(w);
        l.setContentsMargins(5, 5, 5, 5)
        self.status_label = QLabel("Ready");
        l.addWidget(self.status_label)
        self.chat_history = QTextBrowser();
        self.chat_history.setOpenLinks(False)
        self.chat_history.anchorClicked.connect(self.on_chat_link_clicked)
        l.addWidget(self.chat_history)
        self.chat_input = QLineEdit();
        self.chat_input.setPlaceholderText("Ask AI...")
        self.chat_input.returnPressed.connect(self.send_chat_message)
        l.addWidget(self.chat_input)
        self.completer = QCompleter(self);
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer_model = QStringListModel([], self.completer)
        self.completer.setModel(self.completer_model);
        self.chat_input.setCompleter(self.completer)
        self.chat_input.textEdited.connect(self.check_at_symbol)
        self.top_split.addWidget(w)

    def _create_menu(self):
        m = self.menuBar().addMenu("&File")

        # Save Action
        save_act = QAction("Save", self)
        save_act.setShortcut("Ctrl+S")
        save_act.triggered.connect(self.save_current_file)
        m.addAction(save_act)

        op = QAction("Open Project...", self)
        op.triggered.connect(self.open_folder_dialog)
        m.addAction(op)

    # --- SAVE FUNCTION ---
    def save_current_file(self):
        """Сохраняет текущий открытый файл."""
        editor = self.tabs.currentWidget()
        if not editor: return

        # Путь к файлу хранится в подсказке вкладки
        file_path = self.tabs.tabToolTip(self.tabs.currentIndex())

        if file_path and os.path.exists(os.path.dirname(file_path)):
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(editor.text())
                self.status_label.setText(f"Saved: {os.path.basename(file_path)}")
                # Мигаем цветом в консоли (опционально)
                self.chat_history.append(f"<small style='color:gray'>File saved: {os.path.basename(file_path)}</small>")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not save: {e}")

    # --- LOGIC ---
    def open_folder_dialog(self):
        f = QFileDialog.getExistingDirectory(self, "Open Project")
        if f: self.open_project(f)

    def open_project(self, folder):
        self.current_project_path = folder
        self.setWindowTitle(f"Cursor Clone - {os.path.basename(folder)}")
        self._set_tree_root(folder)
        self.start_background_indexing(folder)
        self.terminal.set_working_directory(folder)

    def _set_tree_root(self, path):
        self.tree_view.setRootIndex(self.file_model.index(path))

    def send_chat_message(self):
        text = self.chat_input.text().strip()
        if not text: return
        self.append_chat_msg("You", text, True)
        self.chat_input.clear()
        self.status_label.setText("Thinking...")
        QApplication.processEvents()

        rag = []
        if self.rag_engine.is_indexed: rag = self.rag_engine.search(text)

        # <-- ИЗМЕНЕНИЕ ЗДЕСЬ:
        active_file_data = self.get_active_file_info()
        # Передаем кортеж (имя, код) в prompt builder
        prompt = build_context_prompt(text, {}, active_file_data, rag)

        resp = get_chat_response(prompt)
        self.process_ai_response(resp)
        self.status_label.setText("Ready")

    def process_ai_response(self, text):
        pattern = re.compile(r"### FILE: (.*?)\n(.*?)### END_FILE", re.DOTALL)
        files_created = []

        def repl(m):
            fn = m.group(1).strip()
            content = m.group(2)

            # === ГЛАВНОЕ ИСПРАВЛЕНИЕ: ЧИСТКА КОДА ===
            # Удаляем ```python, ```, ```bash и т.д.
            content = content.replace("```python", "").replace("```bash", "").replace("```", "").strip()

            if self.current_project_path:
                try:
                    full_p = os.path.join(self.current_project_path, fn)
                    with open(full_p, 'w', encoding='utf-8') as f:
                        f.write(content)
                    files_created.append(fn)
                    return f'<br><div style="color:#4ec9b0; font-weight:bold">✅ Created: <a href="{fn}" style="color:#4ec9b0">{fn}</a></div><br>'
                except Exception as e:
                    return str(e)
            return "[Error: Open folder first]"

        new_text = re.sub(pattern, repl, text)
        self.append_chat_msg("AI", new_text, False)
        if files_created:
            self.status_label.setText("Re-indexing...")
            self.start_background_indexing(self.current_project_path)

    def append_chat_msg(self, role, text, is_user):
        c = "#569cd6" if is_user else "#ce9178"
        text = text.replace("```", "<hr>").replace("\n", "<br>")
        self.chat_history.append(f"<div><b style='color:{c}'>{role}:</b><br>{text}</div><hr>")
        sb = self.chat_history.verticalScrollBar();
        sb.setValue(sb.maximum())

    def on_chat_link_clicked(self, url):
        if self.current_project_path: self.add_file_tab(os.path.join(self.current_project_path, url.toString()))

    def start_background_indexing(self, folder):
        if self.indexer_thread: self.indexer_thread.terminate()
        self.indexer_thread = IndexerWorker(self.rag_engine, folder)
        self.indexer_thread.progress_signal.connect(lambda m: self.status_label.setText(m))
        self.indexer_thread.start()

    def on_file_double_clicked(self, idx):
        p = self.file_model.filePath(idx)
        if not os.path.isdir(p): self.add_file_tab(p)

    def add_file_tab(self, path):
        for i in range(self.tabs.count()):
            if self.tabs.tabToolTip(i) == path: self.tabs.setCurrentIndex(i); return
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

    def close_tab(self, i):
        self.tabs.removeTab(i)

    def get_active_file_info(self):
        """Возвращает (имя_файла, код)"""
        widget = self.tabs.currentWidget()
        if widget:
            # Получаем полный путь из подсказки
            full_path = self.tabs.tabToolTip(self.tabs.currentIndex())
            # Берем только имя файла (например snake_game.py)
            filename = os.path.basename(full_path) if full_path else "untitled.py"
            return (filename, widget.text())
        return ("None", "")

    def check_at_symbol(self, t):
        if t.endswith("@"): self.completer.complete()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = AIEditorWindow()
    w.show()
    sys.exit(app.exec())