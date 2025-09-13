# -*- coding: utf-8 -*-
"""
PySide6 + SQLAlchemy (PostgreSQL) — стабильная версия:
- Без QtSql / PyQt.
- QAbstractTableModel с beginResetModel/endResetModel.
- Кнопки: Подключиться/Отключиться, CREATE schema, INSERT demo.
- Переключатель драйвера: psycopg2 / psycopg (v3) / pg8000 (pure Python).
- Вместо parent().parent() используем self.window() для доступа к MainWindow.
"""

import sys
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import date
import faulthandler

faulthandler.enable()

from PySide6.QtCore import Qt, QDate, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QLineEdit, QPushButton, QMessageBox, QSpinBox,
    QDateEdit, QComboBox, QCheckBox, QTextEdit, QTableView, QGroupBox
)

# ===== SQLAlchemy =====
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Date,
    ForeignKey, UniqueConstraint, CheckConstraint, select, insert, delete
)
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


# -------------------------------
# Конфигурация подключения
# -------------------------------
@dataclass
class PgConfig:
    host: str = "localhost"
    port: int = 5432
    dbname: str = "university"
    user: str = "postgres"
    password: str = "root"
    sslmode: str = "prefer"       # для psycopg2/psycopg
    connect_timeout: int = 5      # секунды
    driver: str = "psycopg2"      # psycopg2 | psycopg | pg8000


# -------------------------------
# Создание Engine и схемы
# -------------------------------
def make_engine(cfg: PgConfig) -> Engine:
    drivername_map = {
        "psycopg2": "postgresql+psycopg2",
        "psycopg":  "postgresql+psycopg",
        "pg8000":   "postgresql+pg8000",
    }
    drivername = drivername_map.get(cfg.driver, "postgresql+psycopg2")

    if cfg.driver in ("psycopg2", "psycopg"):
        query = {
            "sslmode": cfg.sslmode,
            "application_name": "QtEduDemo",
            "connect_timeout": str(cfg.connect_timeout),
        }
    else:  # pg8000 — только app_name
        query = {"application_name": "QtEduDemo"}

    url = URL.create(
        drivername=drivername,
        username=cfg.user,
        password=cfg.password,
        host=cfg.host,
        port=cfg.port,
        database=cfg.dbname,
        query=query,
    )

    engine = create_engine(url, future=True, pool_pre_ping=True)
    # sanity ping
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    return engine


def build_metadata() -> (MetaData, Dict[str, Table]):
    md = MetaData()

    students = Table(
        "students", md,
        Column("student_id", Integer, primary_key=True, autoincrement=True),
        Column("full_name", String(200), nullable=False),
        Column("email", String(200), nullable=False, unique=True),
        Column("birth_date", Date),
        CheckConstraint("char_length(full_name) >= 3", name="chk_students_name"),
        CheckConstraint("position('@' in email) > 1", name="chk_students_email"),
        CheckConstraint("birth_date IS NULL OR birth_date >= DATE '1900-01-01'", name="chk_students_birth"),
    )

    courses = Table(
        "courses", md,
        Column("course_id", Integer, primary_key=True, autoincrement=True),
        Column("title", String(200), nullable=False),
        Column("credits", Integer, nullable=False),
        Column("code", String(50), nullable=False, unique=True),
        CheckConstraint("credits BETWEEN 1 AND 10", name="chk_courses_credits"),
        CheckConstraint("char_length(code) >= 3", name="chk_courses_code"),
    )

    enrollments = Table(
        "enrollments", md,
        Column("enrollment_id", Integer, primary_key=True, autoincrement=True),
        Column("student_id", Integer, ForeignKey("students.student_id",
                                                 onupdate="CASCADE", ondelete="CASCADE"), nullable=False),
        Column("course_id", Integer, ForeignKey("courses.course_id",
                                                onupdate="CASCADE", ondelete="RESTRICT"), nullable=False),
        Column("term", String(10), nullable=False),
        Column("grade", Integer),
        UniqueConstraint("student_id", "course_id", "term", name="uq_enr_student_course_term"),
        CheckConstraint("term IN ('autumn','spring','summer','winter')", name="chk_enr_term"),
        CheckConstraint("grade IS NULL OR (grade BETWEEN 0 AND 100)", name="chk_enr_grade"),
    )

    return md, {"students": students, "courses": courses, "enrollments": enrollments}


def drop_and_create_schema_sa(engine: Engine, md: MetaData) -> bool:
    try:
        md.drop_all(engine)
        md.create_all(engine)
        return True
    except SQLAlchemyError as e:
        print("SA schema error:", e)
        return False


def insert_demo_data_sa(engine: Engine, t: Dict[str, Table]) -> bool:
    try:
        with engine.begin() as conn:
            conn.execute(t["students"].insert(), [
                {"full_name": "Иван Петров",  "email": "ivan.petrov@example.com",   "birth_date": "2000-05-12"},
                {"full_name": "Анна Смирнова","email": "anna.smirnova@example.com", "birth_date": "1999-10-01"},
                {"full_name": "Жанна Д'Арк",  "email": "jeanne@example.com",        "birth_date": "1988-01-15"},
            ])
            conn.execute(t["courses"].insert(), [
                {"title": "Базы данных", "credits": 5, "code": "DB101"},
                {"title": "Алгоритмы",   "credits": 6, "code": "ALG201"},
                {"title": "Python для анализа данных", "credits": 4, "code": "PYDA301"},
            ])
            conn.execute(t["enrollments"].insert(), [
                {"student_id": 1, "course_id": 1, "term": "autumn", "grade": 90},
                {"student_id": 1, "course_id": 2, "term": "autumn", "grade": None},
                {"student_id": 2, "course_id": 1, "term": "spring", "grade": 78},
            ])
        return True
    except SQLAlchemyError as e:
        print("SA seed error:", e)
        return False


# -------------------------------
# QAbstractTableModel для SQLAlchemy
# -------------------------------
class SATableModel(QAbstractTableModel):
    """Универсальная модель для QTableView (SQLAlchemy)."""
    def __init__(self, engine: Engine, table: Table, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.table = table
        self.columns: List[str] = [c.name for c in self.table.columns]
        self.pk_col = list(self.table.primary_key.columns)[0]
        self._rows: List[Dict[str, Any]] = []
        self.refresh()

    def refresh(self):
        self.beginResetModel()
        try:
            with self.engine.connect() as conn:
                res = conn.execute(select(self.table).order_by(self.pk_col.asc()))
                self._rows = [dict(r._mapping) for r in res]
        finally:
            self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.EditRole):
            return None
        row = self._rows[index.row()]
        col_name = self.columns[index.column()]
        val = row.get(col_name)
        return "" if val is None else str(val)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        return self.columns[section] if orientation == Qt.Horizontal else section + 1

    def pk_value_at(self, row: int):
        return self._rows[row].get(self.pk_col.name) if 0 <= row < len(self._rows) else None


# -------------------------------
# Вкладка «Студенты»
# -------------------------------
class StudentsTab(QWidget):
    def __init__(self, engine: Engine, tables: Dict[str, Table], parent=None):
        super().__init__(parent)
        self.engine = engine
        self.t = tables
        self.model = SATableModel(engine, self.t["students"], self)

        self.name_edit = QLineEdit()
        self.email_edit = QLineEdit()
        self.birth_edit = QDateEdit()
        self.birth_edit.setCalendarPopup(True)
        self.birth_edit.setDisplayFormat("yyyy-MM-dd")
        self.birth_edit.setDate(QDate(2000, 1, 1))

        form = QFormLayout()
        form.addRow("ФИО:", self.name_edit)
        form.addRow("Email:", self.email_edit)
        form.addRow("Дата рождения:", self.birth_edit)

        self.add_btn = QPushButton("Добавить студента (INSERT)")
        self.add_btn.clicked.connect(self.add_student)
        self.del_btn = QPushButton("Удалить выбранного студента")
        self.del_btn.clicked.connect(self.delete_selected)

        btns = QHBoxLayout()
        btns.addWidget(self.add_btn)
        btns.addWidget(self.del_btn)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(btns)
        layout.addWidget(self.table)

    def _qdate_to_pydate(self, qd: QDate) -> date:
        return date(qd.year(), qd.month(), qd.day())

    def add_student(self):
        full_name = self.name_edit.text().strip()
        email = self.email_edit.text().strip()
        birth_date = self._qdate_to_pydate(self.birth_edit.date())
        if not full_name or not email:
            QMessageBox.warning(self, "Ввод", "ФИО и Email обязательны (NOT NULL)")
            return
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(self.t["students"]).values(
                    full_name=full_name, email=email, birth_date=birth_date
                ))
            self.model.refresh()
            self.name_edit.clear(); self.email_edit.clear()
            self.window().refresh_combos()    # <-- было parent().parent()
        except IntegrityError as e:
            QMessageBox.critical(self, "Ошибка INSERT (UNIQUE/CHECK)", str(e.orig))
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Ошибка INSERT", str(e))

    def delete_selected(self):
        idx = self.table.currentIndex()
        if not idx.isValid():
            QMessageBox.information(self, "Удаление", "Выберите студента")
            return
        sid = self.model.pk_value_at(idx.row())
        try:
            with self.engine.begin() as conn:
                conn.execute(delete(self.t["students"]).where(self.t["students"].c.student_id == sid))
            self.model.refresh()
            self.window().refresh_combos()    # <-- было parent().parent()
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Ошибка удаления", str(e))


# -------------------------------
# Вкладка «Курсы»
# -------------------------------
class CoursesTab(QWidget):
    def __init__(self, engine: Engine, tables: Dict[str, Table], parent=None):
        super().__init__(parent)
        self.engine = engine
        self.t = tables
        self.model = SATableModel(engine, self.t["courses"], self)

        self.title_edit = QLineEdit()
        self.credits_spin = QSpinBox(); self.credits_spin.setRange(1, 10)
        self.code_edit = QLineEdit()

        form = QFormLayout()
        form.addRow("Название курса:", self.title_edit)
        form.addRow("Кредиты (1..10):", self.credits_spin)
        form.addRow("Код курса:", self.code_edit)

        self.add_btn = QPushButton("Добавить курс (INSERT)")
        self.add_btn.clicked.connect(self.add_course)
        self.del_btn = QPushButton("Удалить выбранный курс")
        self.del_btn.clicked.connect(self.delete_selected)

        btns = QHBoxLayout()
        btns.addWidget(self.add_btn)
        btns.addWidget(self.del_btn)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(btns)
        layout.addWidget(self.table)

    def add_course(self):
        title = self.title_edit.text().strip()
        code = self.code_edit.text().strip()
        credits = self.credits_spin.value()
        if not title or not code:
            QMessageBox.warning(self, "Ввод", "Название и код курса обязательны (NOT NULL)")
            return
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(self.t["courses"]).values(
                    title=title, credits=credits, code=code
                ))
            self.model.refresh()
            self.title_edit.clear(); self.code_edit.clear()
            self.window().refresh_combos()    # <-- было parent().parent()
        except IntegrityError as e:
            QMessageBox.critical(self, "Ошибка INSERT (UNIQUE/CHECK)", str(e.orig))
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Ошибка INSERT", str(e))

    def delete_selected(self):
        idx = self.table.currentIndex()
        if not idx.isValid():
            QMessageBox.information(self, "Удаление", "Выберите курс")
            return
        cid = self.model.pk_value_at(idx.row())
        try:
            with self.engine.begin() as conn:
                conn.execute(delete(self.t["courses"]).where(self.t["courses"].c.course_id == cid))
            self.model.refresh()
            self.window().refresh_combos()    # <-- было parent().parent()
        except IntegrityError as e:
            QMessageBox.critical(self, "Удаление запрещено (RESTRICT)", str(e.orig))
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Ошибка удаления", str(e))


# -------------------------------
# Вкладка «Зачисления»
# -------------------------------
class EnrollmentsTab(QWidget):
    def __init__(self, engine: Engine, tables: Dict[str, Table], parent=None):
        super().__init__(parent)
        self.engine = engine
        self.t = tables
        self.model = SATableModel(engine, self.t["enrollments"], self)

        self.student_cb = QComboBox()
        self.course_cb = QComboBox()
        self.term_cb = QComboBox(); self.term_cb.addItems(["autumn", "spring", "summer", "winter"])

        self.grade_spin = QSpinBox(); self.grade_spin.setRange(0, 100)
        self.no_grade_chk = QCheckBox("Без оценки (NULL)"); self.no_grade_chk.setChecked(False)

        form = QFormLayout()
        form.addRow("Студент:", self.student_cb)
        form.addRow("Курс:", self.course_cb)
        form.addRow("Семестр (term):", self.term_cb)
        form.addRow("Оценка (0..100):", self.grade_spin)
        form.addRow("", self.no_grade_chk)

        self.add_btn = QPushButton("Зачислить (INSERT)")
        self.add_btn.clicked.connect(self.add_enrollment)
        self.del_btn = QPushButton("Удалить выбранную запись")
        self.del_btn.clicked.connect(self.delete_selected)

        btns = QHBoxLayout()
        btns.addWidget(self.add_btn)
        btns.addWidget(self.del_btn)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(btns)
        layout.addWidget(self.table)

        self.refresh_combos()

    def refresh_combos(self):
        self.student_cb.clear(); self.course_cb.clear()
        try:
            with self.engine.connect() as conn:
                res = conn.execute(select(self.t["students"].c.student_id, self.t["students"].c.full_name)
                                   .order_by(self.t["students"].c.full_name.asc()))
                for r in res:
                    self.student_cb.addItem(r.full_name, r.student_id)
                res = conn.execute(select(self.t["courses"].c.course_id, self.t["courses"].c.title)
                                   .order_by(self.t["courses"].c.title.asc()))
                for r in res:
                    self.course_cb.addItem(r.title, r.course_id)
        except SQLAlchemyError as e:
            print("refresh_combos error:", e)

    def add_enrollment(self):
        if self.student_cb.count() == 0 or self.course_cb.count() == 0:
            QMessageBox.warning(self, "Зачисление", "Сначала добавьте студентов и курсы")
            return
        student_id = int(self.student_cb.currentData())
        course_id = int(self.course_cb.currentData())
        term = self.term_cb.currentText()
        grade = None if self.no_grade_chk.isChecked() else int(self.grade_spin.value())
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(self.t["enrollments"]).values(
                    student_id=student_id, course_id=course_id, term=term, grade=grade
                ))
            self.model.refresh()
        except IntegrityError as e:
            QMessageBox.critical(self, "Ошибка INSERT (ограничения)", str(e.orig))
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Ошибка INSERT", str(e))

    def delete_selected(self):
        idx = self.table.currentIndex()
        if not idx.isValid():
            QMessageBox.information(self, "Удаление", "Выберите запись")
            return
        enr_id = self.model.pk_value_at(idx.row())
        try:
            with self.engine.begin() as conn:
                conn.execute(delete(self.t["enrollments"]).where(self.t["enrollments"].c.enrollment_id == enr_id))
            self.model.refresh()
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Ошибка удаления", str(e))


# -------------------------------
# Вкладка «Подключение и схема БД»
# -------------------------------
class SetupTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.log = QTextEdit(); self.log.setReadOnly(True)

        self.driver_cb = QComboBox()
        self.driver_cb.addItem("psycopg2 (binary)", "psycopg2")
        self.driver_cb.addItem("psycopg (v3, binary)", "psycopg")
        self.driver_cb.addItem("pg8000 (pure Python)", "pg8000")

        self.host_edit = QLineEdit("localhost")
        self.port_edit = QLineEdit("5432")
        self.db_edit   = QLineEdit("university")
        self.user_edit = QLineEdit("postgres")
        self.pw_edit   = QLineEdit(""); self.pw_edit.setEchoMode(QLineEdit.Password)
        self.ssl_edit  = QLineEdit("prefer")

        conn_form = QFormLayout()
        conn_form.addRow("Driver:", self.driver_cb)
        conn_form.addRow("Host:", self.host_edit)
        conn_form.addRow("Port:", self.port_edit)
        conn_form.addRow("DB name:", self.db_edit)
        conn_form.addRow("User:", self.user_edit)
        conn_form.addRow("Password:", self.pw_edit)
        conn_form.addRow("sslmode:", self.ssl_edit)

        conn_box = QGroupBox("Параметры подключения (SQLAlchemy)")
        conn_box.setLayout(conn_form)

        self.connect_btn = QPushButton("Подключиться")
        self.connect_btn.clicked.connect(self.do_connect)
        self.disconnect_btn = QPushButton("Отключиться")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self.do_disconnect)

        self.create_btn = QPushButton("Сбросить и создать БД (CREATE)")
        self.create_btn.setEnabled(False)
        self.create_btn.clicked.connect(self.reset_db)

        self.demo_btn = QPushButton("Добавить демо-данные (INSERT)")
        self.demo_btn.setEnabled(False)
        self.demo_btn.clicked.connect(self.add_demo)

        top_btns = QHBoxLayout()
        top_btns.addWidget(self.connect_btn)
        top_btns.addWidget(self.disconnect_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(conn_box)
        layout.addLayout(top_btns)
        layout.addWidget(self.create_btn)
        layout.addWidget(self.demo_btn)
        layout.addWidget(QLabel("Лог:"))
        layout.addWidget(self.log)

    def current_cfg(self) -> PgConfig:
        try:
            port = int(self.port_edit.text().strip())
        except ValueError:
            port = 5432
        return PgConfig(
            host=self.host_edit.text().strip() or "localhost",
            port=port,
            dbname=self.db_edit.text().strip() or "university",
            user=self.user_edit.text().strip() or "postgres",
            password=self.pw_edit.text(),
            sslmode=self.ssl_edit.text().strip() or "prefer",
            driver=self.driver_cb.currentData(),
        )

    def do_connect(self):
        main = self.window()  # <-- было parent().parent()
        # если уже подключены — просим отключиться
        if getattr(main, "engine", None) is not None:
            self.log.append("Уже подключено. Нажмите «Отключиться» для переподключения.")
            return
        cfg = self.current_cfg()
        try:
            engine = make_engine(cfg)
            md, tables = build_metadata()
            main.attach_engine(engine, md, tables)
            self.log.append(
                f"Успешное подключение: {cfg.driver} → {cfg.host}:{cfg.port}/{cfg.dbname} (user={cfg.user})"
            )
            self.create_btn.setEnabled(True)
            self.demo_btn.setEnabled(True)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            main.ensure_data_tabs()
        except SQLAlchemyError as e:
            self.log.append(f"Ошибка подключения: {e}")

    def do_disconnect(self):
        main = self.window()  # <-- было parent().parent()
        main.disconnect_db()
        self.log.append("Соединение закрыто.")

    def reset_db(self):
        main = self.window()  # <-- было parent().parent()
        if getattr(main, "engine", None) is None:
            QMessageBox.warning(self, "Схема", "Нет подключения к БД.")
            return
        if drop_and_create_schema_sa(main.engine, main.md):
            self.log.append("Схема БД создана: students, courses, enrollments.")
            main.refresh_all_models()
            main.refresh_combos()
        else:
            QMessageBox.critical(self, "Схема", "Ошибка при создании схемы. См. консоль/лог.")

    def add_demo(self):
        main = self.window()  # <-- было parent().parent()
        if getattr(main, "engine", None) is None:
            QMessageBox.warning(self, "Демо", "Нет подключения к БД.")
            return
        if insert_demo_data_sa(main.engine, main.tables):
            self.log.append("Добавлены демонстрационные данные (INSERT).")
            main.refresh_all_models()
            main.refresh_combos()
        else:
            QMessageBox.warning(self, "Демо", "Часть данных не добавлена. См. консоль.")


# -------------------------------
# Главное окно
# -------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PySide6 + SQLAlchemy: CREATE/INSERT/CHECK/UNIQUE/PK/FK")
        self.resize(1100, 740)

        self.engine: Optional[Engine] = None
        self.md: Optional[MetaData] = None
        self.tables: Optional[Dict[str, Table]] = None

        self.tabs = QTabWidget()
        self.setup_tab = SetupTab(self.tabs)
        self.tabs.addTab(self.setup_tab, "Подключение и схема БД")

        self.students_tab: Optional[StudentsTab] = None
        self.courses_tab: Optional[CoursesTab] = None
        self.enrollments_tab: Optional[EnrollmentsTab] = None

        self.setCentralWidget(self.tabs)

    def attach_engine(self, engine: Engine, md: MetaData, tables: Dict[str, Table]):
        self.engine = engine
        self.md = md
        self.tables = tables

    def ensure_data_tabs(self):
        if self.engine is None or self.tables is None:
            return
        if self.students_tab is None:
            self.students_tab = StudentsTab(self.engine, self.tables, self.tabs)
            self.tabs.addTab(self.students_tab, "Студенты")
        if self.courses_tab is None:
            self.courses_tab = CoursesTab(self.engine, self.tables, self.tabs)
            self.tabs.addTab(self.courses_tab, "Курсы")
        if self.enrollments_tab is None:
            self.enrollments_tab = EnrollmentsTab(self.engine, self.tables, self.tabs)
            self.tabs.addTab(self.enrollments_tab, "Зачисления")
        self.refresh_combos()

    def refresh_all_models(self):
        if self.students_tab:
            self.students_tab.model.refresh()
        if self.courses_tab:
            self.courses_tab.model.refresh()
        if self.enrollments_tab:
            self.enrollments_tab.model.refresh()

    def refresh_combos(self):
        if self.enrollments_tab:
            self.enrollments_tab.refresh_combos()

    def disconnect_db(self):
        # убрать вкладки (уничтожит модели)
        for attr in ("enrollments_tab", "courses_tab", "students_tab"):
            tab = getattr(self, attr)
            if tab is not None:
                idx = self.tabs.indexOf(tab)
                if idx != -1:
                    self.tabs.removeTab(idx)
                tab.deleteLater()
                setattr(self, attr, None)

        QApplication.processEvents()

        # закрыть engine
        if self.engine is not None:
            self.engine.dispose()
        self.engine = None; self.md = None; self.tables = None

        # кнопки в состояние "нет подключения"
        self.setup_tab.connect_btn.setEnabled(True)
        self.setup_tab.disconnect_btn.setEnabled(False)
        self.setup_tab.create_btn.setEnabled(False)
        self.setup_tab.demo_btn.setEnabled(False)


# -------------------------------
# Точка входа
# -------------------------------
def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
