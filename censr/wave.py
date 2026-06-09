# -*- coding: utf-8 -*-
"""Звуковая волна — единый индикатор всех экранов:
idle (главный, анимация) · progress (обработка) · full (готово) · stopped (прервано)."""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QSizePolicy, QWidget

from . import theme

# стабильная «форма» волны (псевдослучайная, но фиксированная)
_SHAPE = []
_seed = 1234567
for _i in range(48):
    _seed = (_seed * 1103515245 + 12345) & 0x7FFFFFFF
    _r = _seed / 0x7FFFFFFF
    _base = 0.35 + 0.55 * abs(math.sin(_i * 0.55))
    _SHAPE.append(max(0.16, min(1.0, _base * (0.65 + 0.5 * _r))))


class Wave(QWidget):
    clicked = Signal()

    def __init__(self, n: int = 38, height: int = 90, parent=None, interactive: bool = False):
        super().__init__(parent)
        self._n = n
        self._mode = "idle"
        self._frac = 0.0
        self._phase = 0.0
        self._interactive = interactive    # реагирует на клик/курсор только если True
        self._shape = _SHAPE               # форма волны (можно перерисовать randomize())
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def randomize(self):
        """Новая случайная форма волны (своя на каждый запуск обработки)."""
        ph = random.uniform(0, 6.28)
        freq = random.uniform(0.35, 0.8)
        shape = []
        for i in range(48):
            base = 0.32 + 0.55 * abs(math.sin(i * freq + ph))
            shape.append(max(0.16, min(1.0, base * (0.6 + 0.6 * random.random()))))
        self._shape = shape
        self.update()

    # — режимы
    def set_idle(self, glow: bool = True):
        self._mode = "idle"
        if glow:
            self._glow()
        self._anim.start(70)
        self.update()

    def set_progress(self, frac: float):
        self._stop()
        self._mode = "progress"
        self._frac = max(0.0, min(1.0, frac))
        self.update()

    # — служебное
    def _tick(self):
        self._phase += 0.3
        self.update()

    def _stop(self):
        self._anim.stop()
        self.setGraphicsEffect(None)

    def _glow(self):
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(24)
        eff.setOffset(0, 0)
        c = QColor(theme.ACCENT)
        c.setAlpha(120)
        eff.setColor(c)
        self.setGraphicsEffect(eff)

    def mousePressEvent(self, e):
        if self._interactive and e.button() == Qt.LeftButton:
            self.clicked.emit()
        else:
            super().mousePressEvent(e)

    def enterEvent(self, e):
        if self._interactive:
            self.setCursor(Qt.PointingHandCursor)

    # — отрисовка
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        cy = h / 2
        n = self._n
        gap = (w / n) * 0.42
        bw = max(1.0, (w - gap * (n - 1)) / n)   # полоски от края до края
        mint = QColor(theme.ACCENT)
        track = QColor(theme.SURFACE)
        x = 0.0
        animating = self._anim.isActive()
        for i in range(n):
            base = self._shape[i % len(self._shape)]
            if self._mode == "idle" and animating:
                hh = 0.28 + 0.72 * (0.5 + 0.5 * math.sin(self._phase + i * 0.5)) * base
            else:
                hh = base
            bh = max(3.0, h * 0.88 * hh)
            frac_i = (i + 0.5) / n
            if self._mode == "progress":
                col = mint if frac_i <= self._frac else track
            else:                                # idle / прочее — мятный
                col = mint
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x, cy - bh / 2, bw, bh), bw / 2, bw / 2)
            x += bw + gap
        p.end()
