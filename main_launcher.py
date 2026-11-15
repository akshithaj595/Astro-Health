#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Astronaut Health Monitoring System - Front Page / Launcher
==========================================================
Shows front.png as the home screen. The two cards on the image are clickable:

    HEALTH MONITOR               ->  astro_health_monitor.py
    MEDICAL EMERGENCY ASSISTANCE ->  astra_emergency.py

Keep these files in the SAME folder:
    main_launcher.py, front.png, astro_health_monitor.py, astra_emergency.py

Run:   python main_launcher.py
Needs: pip install PySide6
Back:  each module has a BACK TO MAIN MENU button that returns here.
Keys:  1 = Health Monitor, 2 = Medical Emergency, F11 = fullscreen, Esc = leave fullscreen
"""
import os
import sys
import subprocess

from PySide6.QtCore import Qt, QRectF, QPointF, QTimer
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QFont, QCursor
from PySide6.QtWidgets import QApplication, QWidget, QMessageBox

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE_FILE = os.path.join(HERE, "front.png")
DB_FILE = os.path.join(HERE, "astro_health.db")     # shared by both apps

IMG_W, IMG_H = 1672, 941                            # size of front.png

# Clickable areas, in ORIGINAL image pixels: (x, y, width, height)
BUTTONS = {
    "monitor": {
        "rect": (80, 345, 728, 362),
        "script": "astro_health_monitor.py",
        "args": [],
        "title": "Health Monitor",
    },
    "emergency": {
        "rect": (860, 345, 732, 364),
        "script": "astra_emergency.py",
        "args": ["--db", DB_FILE],                  # make both modules use the same database
        "title": "Medical Emergency Assistance",
    },
}
CORNER = 38                                         # corner radius of the cards (image px)


class FrontPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Astronaut Health Monitoring System")
        self.setMouseTracking(True)
        self.setMinimumSize(800, 450)
        self.setStyleSheet("background:#000;")

        self.source = QPixmap(IMAGE_FILE)
        if self.source.isNull():
            QMessageBox.critical(None, "Image not found", f"Cannot load:\n{IMAGE_FILE}")
            sys.exit(1)

        self.scaled = QPixmap()
        self.scale = 1.0
        self.offset = QPointF(0, 0)
        self.hover = None
        self.pressed = None
        self.status = ""
        self.procs = {}                             # key -> subprocess.Popen

        self.status_timer = QTimer(self)
        self.status_timer.setSingleShot(True)
        self.status_timer.timeout.connect(lambda: self.set_status(""))

        # watches the running module; when it closes (its BACK button) the front page returns
        self.watch = QTimer(self)
        self.watch.setInterval(300)
        self.watch.timeout.connect(self._check_children)

        screen = QApplication.primaryScreen().availableGeometry()
        w = min(1280, int(screen.width() * 0.9))
        h = int(w * IMG_H / IMG_W)
        if h > screen.height() * 0.9:
            h = int(screen.height() * 0.9)
            w = int(h * IMG_W / IMG_H)
        self.resize(w, h)

    # ------------------------------------------------------------ geometry
    def _recalc(self):
        self.scale = min(self.width() / IMG_W, self.height() / IMG_H)
        sw, sh = int(IMG_W * self.scale), int(IMG_H * self.scale)
        self.offset = QPointF((self.width() - sw) / 2, (self.height() - sh) / 2)
        self.scaled = self.source.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def _widget_rect(self, key):
        x, y, w, h = BUTTONS[key]["rect"]
        return QRectF(self.offset.x() + x * self.scale, self.offset.y() + y * self.scale,
                      w * self.scale, h * self.scale)

    def _hit(self, pos):
        for key in BUTTONS:
            if self._widget_rect(key).contains(pos):
                return key
        return None

    # ------------------------------------------------------------ painting
    def resizeEvent(self, e):
        self._recalc()
        super().resizeEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        if not self.scaled.isNull():
            p.drawPixmap(self.offset.toPoint(), self.scaled)

        for key in BUTTONS:
            if key == self.hover or key == self.pressed:
                r = self._widget_rect(key)
                rad = CORNER * self.scale
                down = key == self.pressed and key == self.hover
                # soft glow
                for i, a in ((6, 20), (4, 35), (2, 55)):
                    pen = QPen(QColor(80, 190, 255, a), i * self.scale * 1.5)
                    p.setPen(pen)
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(r, rad, rad)
                p.setPen(QPen(QColor(120, 215, 255, 230), max(2, 3 * self.scale)))
                p.setBrush(QColor(40, 140, 255, 55 if down else 28))
                p.drawRoundedRect(r, rad, rad)

        if self.status:
            f = QFont("Segoe UI", max(10, int(15 * self.scale + 6)))
            p.setFont(f)
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(self.status) + 50
            th = fm.height() + 20
            box = QRectF((self.width() - tw) / 2, self.height() - th - 28, tw, th)
            p.setPen(QPen(QColor(120, 215, 255, 200), 1.5))
            p.setBrush(QColor(5, 20, 50, 225))
            p.drawRoundedRect(box, th / 2, th / 2)
            p.setPen(QColor(235, 248, 255))
            p.drawText(box, Qt.AlignCenter, self.status)

    # ------------------------------------------------------------ mouse / keys
    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self.hover:
            self.hover = h
            self.setCursor(QCursor(Qt.PointingHandCursor if h else Qt.ArrowCursor))
            self.update()

    def leaveEvent(self, _):
        self.hover = None
        self.pressed = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.pressed = self._hit(e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            key = self._hit(e.position())
            was = self.pressed
            self.pressed = None
            self.update()
            if key and key == was:
                self.launch(key)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_1, Qt.Key_H):
            self.launch("monitor")
        elif k in (Qt.Key_2, Qt.Key_E):
            self.launch("emergency")
        elif k == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif k == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------ launching
    def set_status(self, text, ms=3500):
        self.status = text
        self.update()
        if text:
            self.status_timer.start(ms)

    def _check_children(self):
        if any(p.poll() is None for p in self.procs.values()):
            return
        self.watch.stop()
        crashed = [k for k, p in self.procs.items() if p.returncode not in (0, None)]
        self.show()
        self.raise_()
        self.activateWindow()
        if crashed:
            k = crashed[0]
            self.set_status(f"{BUTTONS[k]['title']} closed unexpectedly (code {self.procs[k].returncode})", 6000)

    def launch(self, key):
        info = BUTTONS[key]
        script = os.path.join(HERE, info["script"])

        proc = self.procs.get(key)
        if proc is not None and proc.poll() is None:        # already open -> don't start a second copy
            self.set_status(f"{info['title']} is already running")
            return

        if not os.path.isfile(script):
            QMessageBox.critical(self, "File not found",
                                 f"Cannot find {info['script']}\n\nPut it in the same folder as main_launcher.py:\n{HERE}")
            return

        try:
            # cwd=HERE so both modules find / create the same astro_health.db
            self.procs[key] = subprocess.Popen([sys.executable, script, *info["args"]], cwd=HERE)
            self.set_status(f"Opening {info['title']} ...")
            self.hide()                                     # front page hides while the module is open
            self.watch.start()
        except Exception as ex:
            QMessageBox.critical(self, "Launch failed", f"Could not start {info['script']}\n\n{ex}")


def main():
    app = QApplication(sys.argv)
    w = FrontPage()
    w.showMaximized()  # Open maximized by default
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
