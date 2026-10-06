from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import sys
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtCore import QLocale
from .persistence import app_home
from .ui import MainWindow,CDD_SUFFIXES


def main():
    app=QApplication(sys.argv)
    app.setApplicationName("DiagAuthor")
    app.setOrganizationName("DiagAuthor")
    app.setStyle("Fusion")
    QLocale.setDefault(QLocale("ko_KR"))
    logs=app_home()/"logs";logs.mkdir(exist_ok=True)
    handler=RotatingFileHandler(logs/"application.log",maxBytes=2*1024*1024,backupCount=4,encoding="utf-8")
    logging.basicConfig(handlers=[handler],level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    def failure(kind,value,traceback):
        logging.error("unhandled_exception type=%s",kind.__name__)
        QMessageBox.critical(None,"예상하지 못한 오류",f"{kind.__name__}: 작업을 완료할 수 없습니다. 작업본은 복구를 위해 유지됩니다.")
    sys.excepthook=failure
    initial=sys.argv[1] if len(sys.argv)>1 and sys.argv[1].lower().endswith((".diagproj",)+CDD_SUFFIXES) else None
    window=MainWindow(initial)
    window.show()
    sys.exit(app.exec())
