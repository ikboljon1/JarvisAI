import sys
import os
import re
import time
import shutil

# Проверка библиотеки для красоты текста
try:
    import markdown
except ImportError:
    # Если нет библиотеки, используем заглушку, чтобы программа не упала
    print("Warning: 'markdown' library not found. Install with: pip install markdown")


    class markdown:
        @staticmethod
        def markdown(text, **kwargs): return text

from PyQt6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget,
                             QFileDialog, QTextBrowser, QLineEdit, QPushButton,
                             QTreeView, QTabWidget, QSplitter, QLabel,
                             QCompleter, QMessageBox, QMenu, QInputDialog, QFileIconProvider)
from PyQt6.QtGui import QAction, QFileSystemModel, QColor, QFont, QKeySequence, QPixmap, QPainter, QIcon
from PyQt6.QtCore import Qt, QDir, QStringListModel, QThread, pyqtSignal, QProcess

from PyQt6.Qsci import QsciScintilla, QsciLexerPython, QsciLexerJavaScript, QsciLexerHTML, QsciLexerCPP

# Импорты модулей
import llm_client
from llm_client import get_chat_response, build_context_prompt, API_KEY
from rag_engine import ProjectIndexer

# ==========================================
# 0. СТИЛИ (CSS)
# ==========================================
CHAT_CSS = """
<style>
    body { font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #d4d4d4; background-color: #1e1e1e; }
    h1, h2, h3 { color: #4ec9b0; margin-top: 12px; margin-bottom: 6px; }
    strong, b { color: #569cd6; font-weight: bold; }
    em, i { color: #9cdcfe; font-style: italic; }
    pre { background-color: #252526; border: 1px solid #333; padding: 8px; border-radius: 4px; margin: 6px 0; }
    code { font-family: 'Consolas', monospace; color: #ce9178; background-color: rgba(255,255,255,0.05); padding: 2px 4px; border-radius: 3px; }
    pre code { background-color: transparent; color: #9cdcfe; padding: 0; }
    ul, ol { margin-left: 20px; padding-left: 0; margin-bottom: 8px; }
    li { margin-bottom: 4px; }
    a { color: #3794ff; text-decoration: none; }
    hr { border: 0; border-top: 1px solid #333; margin: 15px 0; }
</style>
"""


# ==========================================
# 1. ВОРКЕР АГЕНТА
# ==========================================
class AgentWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, user_request, project_path, rag_engine):
        super().__init__()
        self.request = user_request
        self.path = project_path
        self.rag_engine = rag_engine
        self.all_modified_files = []

    def run(self):
        # 1. ПЛАНИРОВАНИЕ
        self.log_signal.emit(
            f"<div style='background:#2d2d2d; border-left:4px solid #a371f7; padding:10px;'><b>🧠 PLANNING PHASE:</b> <i style='color:#ccc'>Architecture design...</i></div>")

        plan_data = llm_client.get_strategic_plan(self.request)
        steps = plan_data.get("steps", [])
        proj_name = plan_data.get("project_name", "Project")

        if not steps:
            self.log_signal.emit(
                f"<span style='color:red'>Failed to generate plan. Error: {plan_data.get('error')}</span>")
            self.finished_signal.emit()
            return

        steps_html = "".join([f"<li>{s}</li>" for s in steps])
        self.log_signal.emit(
            f"<div style='border:1px solid #444; background:#1e1e1e; padding:10px; margin:10px 0;'><h3 style='margin:0; color:#a371f7'>📋 {proj_name}</h3><ul style='color:#ccc; padding-left:20px;'>{steps_html}</ul></div>")

        # 2. ВЫПОЛНЕНИЕ
        total_steps = len(steps)
        for i, step in enumerate(steps):
            self.log_signal.emit(f"<hr><div style='color:#61afef'><b>🚀 PHASE {i + 1}/{total_steps}:</b> {step}</div>")

            rag_context = []
            if self.rag_engine.is_indexed:
                rag_context = self.rag_engine.search(step, top_k=4)

            response_text = llm_client.execute_step(step, self.request, rag_context)
            self.process_files(response_text)

            time.sleep(1.5)  # Пауза

        # 3. ОТЧЕТ
        self.log_signal.emit("<br><i>📊 Generating final report...</i>")
        report_html = llm_client.generate_final_report(self.request, steps, self.all_modified_files)

        # Рендер Markdown отчета
        try:
            rendered_report = markdown.markdown(report_html, extensions=['fenced_code'])
            self.log_signal.emit(CHAT_CSS + f"<div>{rendered_report}</div>")
        except:
            self.log_signal.emit(report_html)

        self.finished_signal.emit()

    def process_files(self, text):
        pattern = re.compile(r"### FILE: (.*?)\n(.*?)### END_FILE", re.DOTALL)
        matches = list(pattern.finditer(text))

        if not matches: return

        for m in matches:
            fn = m.group(1).strip()
            content = m.group(2).replace("```python", "").replace("```", "").strip()
            full_p = os.path.join(self.path, fn)

            try:
                os.makedirs(os.path.dirname(full_p), exist_ok=True)
                status = "📝 Updated" if os.path.exists(full_p) else "✨ Created"
                color = "#e5c07b" if os.path.exists(full_p) else "#98c379"

                with open(full_p, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.all_modified_files.append(fn)

                self.log_signal.emit(
                    f"<div style='margin-left:15px; border-left:3px solid {color}; padding-left:8px; background:#252526;'><b style='color:{color}'>{status}:</b> <span style='font-family:Consolas;'>{fn}</span></div>")
            except Exception as e:
                self.log_signal.emit(f"<span style='color:red'>Error writing {fn}: {e}</span>")


# ==========================================
# 2. UI КОМПОНЕНТЫ
# ==========================================
class CustomIconProvider(QFileIconProvider):
    def icon(self, info):
        if info.isDir(): return super().icon(info)
        filename = info.fileName().lower()
        color = QColor("#cccccc")
        if filename.endswith(".py"):
            color = QColor("#3572A5")
        elif filename.endswith(".js"):
            color = QColor("#F1E05A")
        elif filename.endswith(".html"):
            color = QColor("#E34C26")
        elif filename.endswith(".css"):
            color = QColor("#563d7c")
        elif filename.endswith(".json"):
            color = QColor("#F0E68C")

        pixmap = QPixmap(14, 14)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(color);
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 10, 10);
        painter.end()
        return QIcon(pixmap)


class IndexerWorker(QThread):
    finished_signal = pyqtSignal(str)

    def __init__(self, indexer, folder_path):
        super().__init__()
        self.indexer = indexer
        self.folder_path = folder_path

    def run(self):
        self.indexer.index_project(self.folder_path)
        self.finished_signal.emit("Done")


class CodeEditor(QsciScintilla):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setUtf8(True)
        self.setFont(QFont("Consolas", 11))
        self.setMarginType(0, QsciScintilla.MarginType.NumberMargin)
        self.setMarginWidth(0, "0000")
        self.setColor(QColor("#d4d4d4"));
        self.setPaper(QColor("#1e1e1e"))
        self.setCaretForegroundColor(QColor("white"))

    def set_lexer_by_filename(self, filename):
        ext = os.path.splitext(filename)[1].lower()
        if ext == '.py':
            self.setLexer(QsciLexerPython(self))
        elif ext in ['.js', '.ts', '.json']:
            self.setLexer(QsciLexerJavaScript(self))
        elif ext in ['.html', '.xml']:
            self.setLexer(QsciLexerHTML(self))
        elif ext in ['.cpp', '.c', '.h']:
            self.setLexer(QsciLexerCPP(self))
        else:
            self.setLexer(None)
        if self.lexer():
            self.lexer().setDefaultFont(self.font())
            self.lexer().setPaper(QColor("#1e1e1e"))
            self.lexer().setColor(QColor("#d4d4d4"), -1)


class TerminalPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        l = QVBoxLayout(self);
        l.setContentsMargins(0, 0, 0, 0);
        l.setSpacing(0)
        self.console = QTextBrowser();
        self.console.setStyleSheet("background:#1e1e1e; color:#ccc; border:none;")
        l.addWidget(self.console)
        self.inp = QLineEdit();
        self.inp.setStyleSheet("background:#252526; color:white; border:none; padding:5px;")
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

    def run_external(self, cmd):
        try:
            self.proc.write((cmd + "\n").encode('cp866'))
        except:
            pass


# ==========================================
# 3. ГЛАВНОЕ ОКНО
# ==========================================
class AIEditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cursor Clone (Ultimate)")
        self.resize(1400, 900)
        self.current_project_path = None
        self.rag_engine = ProjectIndexer(llm_client.API_KEY)

        # !!! ЗАЩИТА ОТ СБОРЩИКА МУСОРА (FIX CRASH) !!!
        self.active_threads = []

        self.v_split = QSplitter(Qt.Orientation.Vertical);
        self.setCentralWidget(self.v_split)
        self.top_split = QSplitter(Qt.Orientation.Horizontal);
        self.v_split.addWidget(self.top_split)

        # Files
        self.fmodel = QFileSystemModel();
        self.fmodel.setIconProvider(CustomIconProvider())
        self.fmodel.setRootPath(QDir.rootPath())
        self.tree = QTreeView();
        self.tree.setModel(self.fmodel);
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.open_context_menu)
        for i in range(1, 4): self.tree.setColumnHidden(i, True)
        self.tree.doubleClicked.connect(self.open_file)
        self.top_split.addWidget(self.tree)

        # Tabs
        self.tabs = QTabWidget();
        self.tabs.setTabsClosable(True);
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(lambda i: self.tabs.removeTab(i))
        self.top_split.addWidget(self.tabs)

        # Chat
        chat_w = QWidget();
        cl = QVBoxLayout(chat_w);
        cl.setContentsMargins(5, 5, 5, 5)
        self.chat_out = QTextBrowser();
        self.chat_out.setOpenLinks(False)
        cl.addWidget(self.chat_out)
        self.chat_in = QLineEdit();
        self.chat_in.setPlaceholderText("Ask or Assign Task...")
        self.chat_in.returnPressed.connect(self.start_agent)
        cl.addWidget(self.chat_in)
        self.top_split.addWidget(chat_w)
        self.top_split.setSizes([250, 800, 400])

        # Terminal
        self.term = TerminalPanel();
        self.v_split.addWidget(self.term)
        self.v_split.setSizes([800, 200])

        self._create_menu()
        self.setStyleSheet(
            "QMainWindow {background:#252526; color:#ccc;} QTextBrowser {font-family:'Segoe UI'; font-size:13px;}")
        self.tree.setRootIndex(self.fmodel.index(os.getcwd()))

    # --- ИСПРАВЛЕННОЕ МЕНЮ (FIX TYPE ERROR) ---
    def _create_menu(self):
        m = self.menuBar().addMenu("&File")

        # Open
        open_act = QAction("Open Project...", self)
        open_act.triggered.connect(self.open_folder)
        m.addAction(open_act)

        # Save
        save_act = QAction("Save", self)
        save_act.setShortcut("Ctrl+S")
        save_act.triggered.connect(self.save_file)
        m.addAction(save_act)

        # Edit
        e = self.menuBar().addMenu("&Edit")
        edit_act = QAction("AI Edit", self)
        edit_act.setShortcut("Ctrl+K")
        edit_act.triggered.connect(self.open_ai_edit_dialog)
        e.addAction(edit_act)

    def open_folder(self):
        f = QFileDialog.getExistingDirectory(self, "Open")
        if f:
            self.current_project_path = f
            self.tree.setRootIndex(self.fmodel.index(f))
            self.term.set_cwd(f)
            self.start_indexing(f)

    def start_agent(self):
        text = self.chat_in.text().strip()
        if not text: return

        if not self.current_project_path:
            # Режим чата без проекта
            self.chat_in.clear();
            self.append_msg("You", text, True)
            QApplication.processEvents()
            resp = llm_client.get_chat_response(text)
            self.append_msg("AI", resp, False)
            return

        self.chat_in.clear();
        self.append_msg("You", text, True);
        self.chat_in.setEnabled(False)

        # РОУТЕР: Вопрос или Задача?
        intent = llm_client.classify_intent(text)

        if intent == "QUESTION":
            self.chat_out.append("<i>🔎 Searching codebase...</i>")
            QApplication.processEvents()

            rag_ctx = self.rag_engine.search(text, top_k=5) if self.rag_engine.is_indexed else []
            active_info = self.get_active_file_info()
            prompt = build_context_prompt(text, {}, active_info, rag_ctx)

            resp = get_chat_response(prompt)
            self.process_simple_response(resp)  # Красивый Markdown
            self.chat_in.setEnabled(True);
            self.chat_in.setFocus()

        else:
            # АГЕНТ (Задача)
            self.chat_out.append("<i>🤖 Initializing Agent...</i>")
            worker = AgentWorker(text, self.current_project_path, self.rag_engine)
            worker.log_signal.connect(self.append_html)
            worker.finished_signal.connect(lambda: self.on_agent_done(worker))

            # Добавляем в список активных потоков (FIX 0xC0000409)
            self.active_threads.append(worker)
            worker.start()

    def on_agent_done(self, worker):
        self.chat_in.setEnabled(True);
        self.chat_in.setFocus()
        if worker in self.active_threads: self.active_threads.remove(worker)
        self.start_indexing(self.current_project_path)  # Обновляем память

    def start_indexing(self, path):
        idx = IndexerWorker(self.rag_engine, path)
        self.active_threads.append(idx)
        idx.finished.connect(lambda: self.active_threads.remove(idx) if idx in self.active_threads else None)
        idx.start()

    # --- КРАСИВЫЙ ВЫВОД ---
    def append_msg(self, role, text, is_user):
        style = "background:#0e639c; color:white; padding:8px; border-radius:8px;" if is_user else ""
        align = "right" if is_user else "left"

        if is_user:
            self.chat_out.append(
                f"<div style='text-align:{align}; margin:5px;'><span style='{style}'>{text}</span></div>")
        else:
            self.process_simple_response(text)

        self.chat_out.verticalScrollBar().setValue(self.chat_out.verticalScrollBar().maximum())

    def process_simple_response(self, text):
        # Рендеринг Markdown
        try:
            html_content = markdown.markdown(text, extensions=['fenced_code', 'tables'])
        except:
            html_content = text

        full_html = CHAT_CSS + f"<div>{html_content}</div><hr>"
        self.append_html(full_html)

    def append_html(self, html):
        self.chat_out.append(html)
        self.chat_out.verticalScrollBar().setValue(self.chat_out.verticalScrollBar().maximum())

    # --- FILE UTILS ---
    def open_file(self, idx):
        p = self.fmodel.filePath(idx)
        if not os.path.isdir(p): self.add_tab(p)

    def add_tab(self, path):
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

    def save_file(self):
        ed = self.tabs.currentWidget()
        if ed:
            with open(self.tabs.tabToolTip(self.tabs.currentIndex()), 'w', encoding='utf-8') as f: f.write(ed.text())
            self.chat_out.append("<small style='color:gray'>Saved</small>")

    def get_active_file_info(self):
        w = self.tabs.currentWidget()
        if w: return (os.path.basename(self.tabs.tabToolTip(self.tabs.currentIndex())), w.text())
        return None

    def open_context_menu(self, pos):
        idx = self.tree.indexAt(pos);
        if not idx.isValid(): return
        menu = QMenu();
        run = menu.addAction("Run");
        delete = menu.addAction("Delete")
        action = menu.exec(self.tree.viewport().mapToGlobal(pos))
        path = self.fmodel.filePath(idx)
        if action == run and path.endswith(".py"): self.term.run_external(f"python {os.path.basename(path)}")
        if action == delete:
            if QMessageBox.question(self, "Del", "Delete?") == QMessageBox.StandardButton.Yes:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)

    def open_ai_edit_dialog(self):
        ed = self.tabs.currentWidget()
        if not ed or not ed.hasSelectedText(): return
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox
        d = QDialog(self);
        d.setWindowTitle("AI Edit");
        l = QVBoxLayout(d)
        i = QLineEdit();
        i.setPlaceholderText("Instruct...");
        i.setFocus();
        l.addWidget(i)
        b = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        b.accepted.connect(d.accept);
        l.addWidget(b)
        if d.exec() == QDialog.DialogCode.Accepted and i.text():
            self.chat_out.append("<i>Editing...</i>");
            QApplication.processEvents()
            try:
                nc = llm_client.edit_code_fragment(ed.selectedText(), i.text())
                if nc: ed.replaceSelectedText(nc)
            except:
                pass


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = AIEditorWindow()
    w.show()
    sys.exit(app.exec())