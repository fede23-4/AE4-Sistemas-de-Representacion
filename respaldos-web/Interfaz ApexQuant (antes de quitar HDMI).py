# -*- coding: utf-8 -*-
"""
ApexQuant Scanner · Inspección NIR de productos de exportación (MVP)
=====================================================================
Sistemas de Representación — UCP 2026
Aguilera Ocampo, Máximo · Didolich, Federico Bautista

Interfaz de escritorio para el analizador NIR portátil ApexQuant, pensada para
el control de cargas de exportación en puertos y zonas primarias aduaneras.

Flujo de trabajo
    1. El inspector abre una inspección por lote/contenedor (producto,
       contenedor, permiso de embarque, exportador, destino, terminal).
    2. Escanea varias unidades del lote con el cabezal NIR.
    3. El sistema estima los parámetros de calidad de cada unidad (°Brix,
       materia seca, acidez, almidón, humedad, proteína, aceite), los compara
       con la especificación del producto y dictamina el lote:
            APTO PARA EXPORTACIÓN · OBSERVADO · RECHAZADO
    4. Se genera el acta de inspección en PDF.

En esta versión el escáner está SIMULADO (clase EscanerSimulado). Para usar el
hardware real (ESP32-S3 + Pynect NIR-M-R2 por USB-C serie) alcanza con otra
clase que implemente `nuevo_lote(producto)` y `medir(producto)`.

Los rangos de especificación son ILUSTRATIVOS: deben ajustarse a los
protocolos de SENASA y a los requisitos de cada mercado de destino.

Requisitos:   pip install PySide6
Ejecutar:     python "Interfaz ApexQuant.py"
"""

import csv
import datetime as dt
import json
import math
import random
import re
import sys
import tempfile
from pathlib import Path

try:
    from PySide6.QtCore import (QEasingCurve, QLocale, QMarginsF, QObject, QPointF, QPropertyAnimation, QRectF, QSize,
                                QTimer, QUrl, Qt, Signal)
    from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QLinearGradient,
                               QKeySequence, QPageLayout, QPageSize, QPainter, QPainterPath, QPalette,
                               QPdfWriter, QPen, QPixmap, QRadialGradient, QShortcut, QTextDocument)
    from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication,
                                   QButtonGroup, QComboBox, QCompleter, QDoubleSpinBox, QFileDialog, QFrame,
                                   QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                                   QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow,
                                   QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea,
                                   QSizePolicy, QSlider, QStackedWidget, QTableWidget, QTableWidgetItem,
                                   QVBoxLayout, QWidget)
except ImportError:
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _r = _tk.Tk()
        _r.withdraw()
        _mb.showerror("ApexQuant", "Falta la librería PySide6.\n\nInstalala con:\n\n    pip install PySide6")
    except Exception:
        print("Falta PySide6. Instalala con:  pip install PySide6")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
#  TEMA
# ═══════════════════════════════════════════════════════════════════════════

C = {
    "bg": "#0A0E14", "side": "#0C1119", "card": "#121923", "card2": "#18212D", "hover": "#202B3A",
    "border": "#232E3D", "text": "#E6EDF3", "muted": "#8A97A8", "faint": "#5B6778",
    "accent": "#2DD4BF", "accent_h": "#5EEAD4", "accent2": "#38BDF8",
    "ok": "#22C55E", "warn": "#F5A524", "bad": "#F04848",
}


def mezclar(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def qc(hexa, alfa=255):
    c = QColor(hexa)
    c.setAlpha(alfa)
    return c


if sys.platform.startswith("win"):
    FAMILIA = "Segoe UI"
elif sys.platform == "darwin":
    FAMILIA = "Helvetica Neue"
else:
    FAMILIA = "DejaVu Sans"


def fuente(px, negrita=False):
    f = QFont(FAMILIA)
    f.setPixelSize(int(px))
    f.setWeight(QFont.Weight.Bold if negrita else QFont.Weight.Normal)
    return f


ESTADOS = {
    "ok":     {"lote": "Apto",      "param": "OK",     "texto": "Dentro de especificación", "color": C["ok"],   "icono": "✓"},
    "limite": {"lote": "Observado", "param": "Límite", "texto": "Próximo al límite",        "color": C["warn"], "icono": "!"},
    "fuera":  {"lote": "Rechazado", "param": "Fuera",  "texto": "Fuera de especificación",  "color": C["bad"],  "icono": "✕"},
}
ORDEN = {"ok": 0, "limite": 1, "fuera": 2}
VEREDICTO = {
    "ok": ("APTO PARA EXPORTACIÓN", "El lote cumple la especificación del producto · liberar para embarque"),
    "limite": ("OBSERVADO", "Valores próximos al límite o unidades fuera de rango · ampliar el muestreo"),
    "fuera": ("RECHAZADO", "El lote no cumple la especificación · retener la carga e informar"),
}

# clave: (nombre, unidad, abreviatura, decimales)
PARAMS = {
    "brix":     ("Sólidos solubles", "°Bx", "SS", 1),
    "ms":       ("Materia seca", "%", "MS", 1),
    "acidez":   ("Acidez titulable", "%", "AT", 2),
    "almidon":  ("Almidón", "%", "AL", 1),
    "humedad":  ("Humedad", "%", "H", 1),
    "proteina": ("Proteína", "%", "P", 1),
    "aceite":   ("Aceite", "%", "AC", 1),
}

CATEGORIAS = ["Frutas", "Hortalizas", "Granos y oleaginosas"]

PRODUCTOS_BASE = [
    # Frutas
    {"nombre": "Banana", "icono": "", "categoria": "Frutas", "muestras": 10,
     "params": {"ms": [20.0, 25.0], "brix": [1.0, 4.0], "almidon": [16.0, 22.0]}},
    {"nombre": "Sandía", "icono": "", "categoria": "Frutas", "muestras": 8,
     "params": {"brix": [10.0, 13.0], "ms": [7.0, 10.0]}},
    {"nombre": "Melón", "icono": "", "categoria": "Frutas", "muestras": 8,
     "params": {"brix": [10.0, 15.0], "ms": [9.0, 14.0]}},
    {"nombre": "Ananá", "icono": "", "categoria": "Frutas", "muestras": 8,
     "params": {"brix": [12.0, 16.0], "acidez": [0.50, 1.00]}},
    {"nombre": "Mango", "icono": "", "categoria": "Frutas", "muestras": 10,
     "params": {"ms": [14.0, 18.0], "brix": [7.0, 12.0]}},
    {"nombre": "Manzana", "icono": "", "categoria": "Frutas", "muestras": 12,
     "params": {"brix": [11.0, 15.0], "acidez": [0.30, 0.80], "ms": [13.0, 17.0]}},
    {"nombre": "Naranja", "icono": "", "categoria": "Frutas", "muestras": 12,
     "params": {"brix": [9.0, 13.0], "acidez": [0.60, 1.20]}},
    {"nombre": "Pera", "icono": "", "categoria": "Frutas", "muestras": 12,
     "params": {"brix": [11.0, 14.0], "ms": [14.0, 17.0]}},
    {"nombre": "Limón", "icono": "", "categoria": "Frutas", "muestras": 12,
     "params": {"acidez": [4.50, 6.50], "brix": [7.0, 10.0]}},
    {"nombre": "Uva", "icono": "", "categoria": "Frutas", "muestras": 10,
     "params": {"brix": [16.0, 21.0], "acidez": [0.50, 0.90]}},
    # Hortalizas
    {"nombre": "Zapallo", "icono": "", "categoria": "Hortalizas", "muestras": 8,
     "params": {"ms": [12.0, 20.0], "brix": [7.0, 12.0]}},
    {"nombre": "Cebolla", "icono": "", "categoria": "Hortalizas", "muestras": 10,
     "params": {"ms": [9.0, 14.0], "brix": [8.0, 12.0]}},
    {"nombre": "Papa", "icono": "", "categoria": "Hortalizas", "muestras": 10,
     "params": {"ms": [18.0, 24.0], "almidon": [13.0, 18.0]}},
    {"nombre": "Batata", "icono": "", "categoria": "Hortalizas", "muestras": 10,
     "params": {"ms": [24.0, 32.0], "almidon": [15.0, 22.0], "brix": [6.0, 12.0]}},
    {"nombre": "Ajo", "icono": "", "categoria": "Hortalizas", "muestras": 12,
     "params": {"ms": [33.0, 42.0], "brix": [30.0, 40.0]}},
    # Granos y oleaginosas
    {"nombre": "Malta (cebada cervecera)", "icono": "", "categoria": "Granos y oleaginosas", "muestras": 5,
     "params": {"humedad": [11.0, 13.5], "proteina": [9.5, 12.5], "almidon": [58.0, 64.0]}},
    {"nombre": "Lino", "icono": "", "categoria": "Granos y oleaginosas", "muestras": 5,
     "params": {"humedad": [7.0, 9.5], "aceite": [38.0, 45.0], "proteina": [20.0, 25.0]}},
    {"nombre": "Trigo", "icono": "", "categoria": "Granos y oleaginosas", "muestras": 5,
     "params": {"humedad": [11.0, 14.0], "proteina": [10.5, 14.0]}},
    {"nombre": "Soja", "icono": "", "categoria": "Granos y oleaginosas", "muestras": 5,
     "params": {"humedad": [10.0, 13.5], "proteina": [33.0, 38.0], "aceite": [18.0, 21.0]}},
    {"nombre": "Maní", "icono": "", "categoria": "Granos y oleaginosas", "muestras": 6,
     "params": {"humedad": [6.0, 8.0], "aceite": [46.0, 52.0], "proteina": [23.0, 28.0]}},
    {"nombre": "Maíz", "icono": "", "categoria": "Granos y oleaginosas", "muestras": 5,
     "params": {"humedad": [12.0, 14.5], "almidon": [68.0, 74.0], "proteina": [7.0, 10.0]}},
]

# ── Listas usadas SOLO para generar datos de demostración (opción "Cargar datos demo").
#    La interfaz nunca las ofrece: en una inspección real todo se ingresa a mano.
DEMO_EXPORTADORES = ["Agroexport del Litoral S.A.", "Frutas del Valle S.R.L.", "Citrus Mesopotamia S.A.",
                "Granos Pampeanos S.A.", "Cooperativa Hortícola Norte Ltda.", "Malterías del Sur S.A.",
                "Tropical Andina S.A.", "Oleaginosas del Plata S.A."]
DEMO_DESTINOS = ["Brasil", "Países Bajos", "España", "Italia", "Estados Unidos", "China", "Rusia", "Chile",
            "Paraguay", "Uruguay", "Reino Unido", "Alemania", "Emiratos Árabes Unidos", "India"]
DEMO_TERMINALES = ["Puerto de Buenos Aires", "Puerto de Rosario", "Puerto de Zárate", "Puerto de Corrientes",
              "Puerto de San Antonio Este", "Paso de los Libres (frontera)"]
DEMO_NAVIERAS = ["MSCU", "MSKU", "HLXU", "CMAU", "TGHU", "MRKU", "SUDU", "HASU"]

MARGEN = 0.15      # 15 % del rango, en cada extremo, se considera "próximo al límite"
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]
RUTA_DATOS = Path(__file__).resolve().with_name("apexquant_aduana.json")


# ═══════════════════════════════════════════════════════════════════════════
#  UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════

def num(v, d=1):
    return f"{v:.{d}f}".replace(".", ",")


def val(k, v):
    return num(v, PARAMS[k][3])


def a_float(t):
    return float(str(t).strip().replace(",", "."))


def evaluar(v, mn, mx):
    if v < mn or v > mx:
        return "fuera"
    w = (mx - mn) * MARGEN
    return "limite" if (v < mn + w or v > mx - w) else "ok"


def peor(estados):
    estados = list(estados)
    return max(estados, key=lambda s: ORDEN[s]) if estados else "ok"


def fecha(r):
    return dt.datetime.fromisoformat(r["fecha"])


def hace(f):
    s = (dt.datetime.now() - f).total_seconds()
    if s < 60:
        return "recién"
    if s < 3600:
        return f"hace {int(s // 60)} min"
    if s < 86400:
        return f"hace {int(s // 3600)} h"
    return f"hace {int(s // 86400)} d"


def fecha_larga(f):
    return f"{DIAS[f.weekday()]} {f.day} de {MESES[f.month - 1]} de {f.year}"


# ── Número de contenedor según ISO 6346: 3 letras de dueño + U/J/Z + 6 dígitos + dígito verificador
def _valores_iso6346():
    valores, n = {}, 10
    for letra in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if n % 11 == 0:          # se saltean los múltiplos de 11
            n += 1
        valores[letra] = n
        n += 1
    return valores


VAL_ISO = _valores_iso6346()
RE_CONTENEDOR = re.compile(r"^([A-Z]{3}[UJZ])\s*(\d{6})\s*-?\s*(\d)$")


def digito_iso6346(codigo):
    """Dígito verificador para los 10 primeros caracteres (ej. 'MSCU482913')."""
    suma = sum((VAL_ISO[c] if c.isalpha() else int(c)) * 2 ** i for i, c in enumerate(codigo))
    return suma % 11 % 10


def validar_contenedor(texto):
    """Devuelve (número normalizado, None) o (None, mensaje de error)."""
    t = texto.strip().upper()
    if not t:
        return None, "Ingresá el número de contenedor"
    m = RE_CONTENEDOR.match(t)
    if not m:
        return None, "Formato ISO 6346: 4 letras (la última U, J o Z) + 6 números + dígito verificador"
    pref, nro, dv = m.groups()
    esperado = digito_iso6346(pref + nro)
    if int(dv) != esperado:
        return None, f"Dígito verificador incorrecto: para {pref} {nro} debería ser {esperado}"
    return f"{pref} {nro}-{dv}", None


def contenedor_demo():
    pref, nro = random.choice(DEMO_NAVIERAS), f"{random.randint(0, 999999):06d}"
    return f"{pref} {nro}-{digito_iso6346(pref + nro)}"


def evaluar_lote(muestras, limites):
    """Dictamen del lote a partir de las unidades escaneadas."""
    medias = {k: sum(m["resultados"][k] for m in muestras) / len(muestras) for k in limites}
    est_medias = {k: evaluar(v, *limites[k]) for k, v in medias.items()}
    fuera = sum(1 for m in muestras if "fuera" in m["estados"].values())
    conformidad = 100 * (len(muestras) - fuera) / len(muestras)
    if "fuera" in est_medias.values() or conformidad < 75:
        estado = "fuera"
    elif "limite" in est_medias.values() or conformidad < 90:
        estado = "limite"
    else:
        estado = "ok"
    return medias, est_medias, conformidad, estado


# ═══════════════════════════════════════════════════════════════════════════
#  MODELO: escáner simulado y almacenamiento
# ═══════════════════════════════════════════════════════════════════════════

class EscanerSimulado:
    """Imita al ApexQuant (ESP32-S3 + Pynect NIR-M-R2, 900–1700 nm).

    Cada lote tiene una composición central propia; cada unidad escaneada
    varía alrededor de ella. El espectro se arma con las bandas NIR típicas.
    """
    LONGITUDES = [900 + 4 * i for i in range(201)]
    BANDAS = [  # (centro nm, ancho nm, componente, absorbancia por unidad)
        (970, 24, "agua", 0.0035), (1190, 40, "agua", 0.0012), (1450, 46, "agua", 0.0085),
        (915, 28, "brix", 0.010), (1600, 60, "brix", 0.005),
        (1200, 34, "almidon", 0.004), (1580, 50, "almidon", 0.005),
        (1180, 30, "proteina", 0.0022), (1510, 48, "proteina", 0.006),
        (1210, 26, "aceite", 0.006), (1390, 30, "aceite", 0.003), (1690, 30, "aceite", 0.008),
        (1150, 40, "acidez", 0.02), (1260, 50, "ms", 0.002),
    ]

    def __init__(self):
        self.variabilidad = 0.35
        self._centro = {}

    def nuevo_lote(self, producto):
        self._centro = {}
        for k, (mn, mx) in producto["params"].items():
            medio = (mx - mn) / 2
            self._centro[k] = random.gauss(mn + medio, medio * (0.10 + 0.75 * self.variabilidad))

    def medir(self, producto, con_espectro=True):
        if not self._centro:
            self.nuevo_lote(producto)
        reales = {}
        for k, (mn, mx) in producto["params"].items():
            medio = (mx - mn) / 2
            reales[k] = max(0.05, random.gauss(self._centro[k], medio * 0.25 * (0.4 + self.variabilidad)))
        estimados = {k: round(v + random.gauss(0, 0.012 * max(1.0, v ** 0.5)), 3) for k, v in reales.items()}
        return (self.espectro(reales) if con_espectro else None), estimados

    def espectro(self, comp):
        comp = dict(comp)
        if "humedad" in comp:
            comp["agua"] = comp["humedad"]
        elif "ms" in comp:
            comp["agua"] = 100 - comp["ms"]
        else:
            comp["agua"] = 100 - 1.2 * comp.get("brix", 10) - 3
        base = 0.14 + random.uniform(-0.02, 0.02)
        pend = random.uniform(0.00008, 0.00013)
        out = []
        for wl in self.LONGITUDES:
            a = base + pend * (wl - 900)
            for c, w, k, s in self.BANDAS:
                if k in comp:
                    a += s * comp[k] * math.exp(-((wl - c) / w) ** 2)
            out.append(round(a + random.gauss(0, 0.0015), 4))
        return out


class Escaner(QObject):
    """Estado del escáner tal como lo ve la PC: conexión, batería, cargador y HDMI.

    La interfaz solo escucha estas señales; no sabe si del otro lado hay hardware
    real o el simulador. Para el equipo real se reemplazan los métodos que hoy se
    disparan desde los controles ocultos por la lectura del puerto serie USB-C.
    """
    conexion = Signal(bool)
    cargador = Signal(bool)
    bateria = Signal(float)
    hdmi = Signal(bool)
    aviso = Signal(str, str, str, str)          # título, detalle, tipo, ícono

    INTERFAZ = "USB-C (serie)"
    PUERTO = "COM3"
    FIRMWARE = "0.2"

    def __init__(self):
        super().__init__()
        self.motor = EscanerSimulado()
        self.conectado = False
        self.nivel = 78.0
        self.cargando = False
        self.pantalla = False
        self.desde = None
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(1000)

    # conexión
    def alternar_conexion(self):
        self.desconectar() if self.conectado else self.conectar()

    def conectar(self):
        if self.conectado:
            return
        if self.nivel <= 0 and not self.cargando:
            self.aviso.emit("No se pudo conectar", "La batería del escáner está agotada. Conectá el cargador USB-C.", "bad", "⇄")
            return
        self.conectado, self.desde = True, dt.datetime.now()
        self.conexion.emit(True)
        extra = "  ·  cargando" if self.cargando else ""
        self.aviso.emit("Escáner conectado", f"ApexQuant AQ-1 por {self.INTERFAZ} · batería {self.nivel:.0f} %{extra}", "ok", "⇄")

    def desconectar(self, motivo=None):
        if not self.conectado:
            return
        self.conectado, self.desde = False, None
        self.conexion.emit(False)
        self.aviso.emit("Escáner desconectado", motivo or "Se perdió la comunicación con el ApexQuant AQ-1", "bad", "⇄")

    # cargador
    def alternar_cargador(self):
        self.cargando = not self.cargando
        self.cargador.emit(self.cargando)
        if not self.conectado:
            return
        if self.cargando:
            self.aviso.emit("Cargador conectado", f"Cargando por USB-C · batería {self.nivel:.0f} %", "info", "⚡")
        else:
            self.aviso.emit("Cargador desconectado", f"Funcionando con batería · {self.nivel:.0f} %", "warn", "⚡")

    # HDMI
    def alternar_hdmi(self):
        self.pantalla = not self.pantalla
        self.hdmi.emit(self.pantalla)
        if self.conectado:
            self.aviso.emit("Pantalla externa " + ("detectada" if self.pantalla else "desconectada"),
                            "Salida HDMI del escáner " + ("activa" if self.pantalla else "inactiva"), "info", "▭")

    # batería
    def ajustar(self, delta):
        self._nivel(self.nivel + delta)

    def fijar(self, valor):
        self._nivel(valor)

    def _nivel(self, v):
        antes = self.nivel
        self.nivel = max(0.0, min(100.0, v))
        self.bateria.emit(self.nivel)
        if not self.conectado:
            return
        if self.nivel <= 0 and not self.cargando:
            self.desconectar("Batería agotada: el escáner se apagó")
        elif antes > 5 >= self.nivel:
            self.aviso.emit("Batería crítica", f"Queda {self.nivel:.0f} %. El escáner se apagará pronto.", "bad", "▮")
        elif antes >= 20 > self.nivel:
            self.aviso.emit("Batería baja", f"Queda {self.nivel:.0f} %. Conectá el cargador USB-C.", "warn", "▮")
        elif antes < 100 <= self.nivel and self.cargando:
            self.aviso.emit("Carga completa", "La batería del escáner está al 100 %", "ok", "▮")

    def _tick(self):
        if self.cargando:
            if self.nivel < 100:
                self._nivel(self.nivel + 0.5)
        elif self.conectado:
            self._nivel(self.nivel - 0.02)

    # medición
    def nuevo_lote(self, producto):
        self.motor.nuevo_lote(producto)

    def medir(self, producto):
        resultado = self.motor.medir(producto)
        self._nivel(self.nivel - 0.3)
        return resultado


class Almacen:
    """Persistencia local en JSON: los datos de las cargas no salen del equipo."""

    def __init__(self, ruta):
        self.ruta = ruta
        self.productos, self.inspecciones, self.contador = [], [], 0
        self.version = 0
        if not self._cargar():
            # primera ejecución: catálogo de productos y SIN inspecciones
            self.productos = json.loads(json.dumps(PRODUCTOS_BASE))
            self.guardar()

    def valores(self, campo):
        """Valores ya ingresados en inspecciones anteriores (para autocompletar)."""
        return sorted({r.get(campo, "").strip() for r in self.inspecciones if r.get(campo, "").strip()}, key=str.lower)

    def _cargar(self):
        try:
            d = json.loads(self.ruta.read_text(encoding="utf-8"))
            self.productos, self.inspecciones = d["productos"], d["inspecciones"]
            self.contador = d.get("contador", len(self.inspecciones))
            return bool(self.productos)
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def guardar(self):
        d = {"productos": self.productos, "inspecciones": self.inspecciones, "contador": self.contador}
        tmp = self.ruta.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.ruta)
        except OSError as e:
            print("No se pudo guardar:", e)

    def producto(self, nombre):
        return next((p for p in self.productos if p["nombre"] == nombre), None)

    def registrar(self, producto, datos, muestras, espectro, cuando=None, guardar=True):
        limites = {k: list(v) for k, v in producto["params"].items()}
        medias, est_medias, conf, estado = evaluar_lote(muestras, limites)
        self.contador += 1
        cuando = cuando or dt.datetime.now()
        r = {
            "id": f"INS-{cuando:%y}-{self.contador:05d}",
            "fecha": cuando.isoformat(timespec="seconds"),
            "producto": producto["nombre"], "icono": producto["icono"], "categoria": producto["categoria"],
            **datos,
            "limites": limites,
            "muestras": [{"resultados": m["resultados"], "estados": m["estados"]} for m in muestras],
            "medias": {k: round(v, 3) for k, v in medias.items()}, "estados_medias": est_medias,
            "conformidad": round(conf, 1), "estado": estado,
            "espectro": espectro,
        }
        self.inspecciones.append(r)
        if guardar:
            self.guardar()
        return r

    def sembrar(self, escaner):
        """Tres semanas de inspecciones de DEMOSTRACIÓN (solo si el usuario lo pide)."""
        self.inspecciones, self.contador = [], 0
        ahora = dt.datetime.now()
        eventos = []
        for d in range(20, -1, -1):
            dia = ahora - dt.timedelta(days=d)
            for _ in range(random.randint(2, 6)):
                f = dia.replace(hour=random.randint(6, 19), minute=random.randint(0, 59), second=0, microsecond=0)
                if f < ahora:
                    eventos.append(f)
        eventos.sort()
        populares = {"Banana": 5, "Naranja": 5, "Limón": 6, "Manzana": 5, "Pera": 5, "Ajo": 4, "Cebolla": 4,
                     "Malta (cebada cervecera)": 4, "Soja": 3, "Lino": 2, "Papa": 3, "Batata": 2}
        pesos = [populares.get(p["nombre"], 1) for p in self.productos]
        for f in eventos:
            p = random.choices(self.productos, weights=pesos)[0]
            escaner.nuevo_lote(p)
            muestras = []
            for _ in range(p["muestras"]):
                _, est = escaner.medir(p, con_espectro=False)
                muestras.append({"resultados": est,
                                 "estados": {k: evaluar(v, *p["params"][k]) for k, v in est.items()}})
            medias = {k: sum(m["resultados"][k] for m in muestras) / len(muestras) for k in p["params"]}
            datos = {"contenedor": contenedor_demo(),
                     "permiso": f"{f:%y} 073 EC01 {random.randint(1000, 99999):06d} {random.choice('ABCDEFHJK')}",
                     "exportador": random.choice(DEMO_EXPORTADORES), "destino": random.choice(DEMO_DESTINOS),
                     "terminal": random.choice(DEMO_TERMINALES[:4]), "inspector": random.choice(["F. Didolich", "M. Aguilera"])}
            self.registrar(p, datos, muestras, escaner.espectro(medias), cuando=f, guardar=False)


# ═══════════════════════════════════════════════════════════════════════════
#  ACTA DE INSPECCIÓN (PDF)
# ═══════════════════════════════════════════════════════════════════════════

def generar_acta(r, ruta):
    est = r["estado"]
    col = {"ok": "#15803D", "limite": "#B45309", "fuera": "#B91C1C"}[est]
    suave = {"ok": "#ECFDF3", "limite": "#FFF7E6", "fuera": "#FEF2F2"}[est]
    tit, sub = VEREDICTO[est]

    def fila(k, v, i):
        bg = "#F8FAFC" if i % 2 else "#FFFFFF"
        return (f'<tr><td bgcolor="{bg}" width="32%" style="color:#64748B">{k}</td>'
                f'<td bgcolor="{bg}"><b>{v}</b></td></tr>')

    datos = [("Producto", f'{r["producto"]} ({r["categoria"]})'), ("Contenedor", r["contenedor"]),
             ("Permiso de embarque", r["permiso"]), ("Exportador", r["exportador"]),
             ("País de destino", r["destino"]), ("Terminal / zona primaria", r["terminal"]),
             ("Inspector", r["inspector"]), ("Unidades analizadas", str(len(r["muestras"])))]
    t_datos = "".join(fila(k, v, i) for i, (k, v) in enumerate(datos))

    colores = {"ok": "#15803D", "limite": "#B45309", "fuera": "#B91C1C"}
    t_res = ""
    for i, (k, (mn, mx)) in enumerate(r["limites"].items()):
        vals = [m["resultados"][k] for m in r["muestras"]]
        e = r["estados_medias"][k]
        bg = "#F8FAFC" if i % 2 else "#FFFFFF"
        t_res += (f'<tr><td bgcolor="{bg}">{PARAMS[k][0]}</td><td bgcolor="{bg}" align="center">{PARAMS[k][1]}</td>'
                  f'<td bgcolor="{bg}" align="center">{val(k, mn)} – {val(k, mx)}</td>'
                  f'<td bgcolor="{bg}" align="center"><b>{val(k, r["medias"][k])}</b></td>'
                  f'<td bgcolor="{bg}" align="center">{val(k, min(vals))}</td>'
                  f'<td bgcolor="{bg}" align="center">{val(k, max(vals))}</td>'
                  f'<td bgcolor="{bg}" align="center" style="color:{colores[e]}"><b>{ESTADOS[e]["param"]}</b></td></tr>')

    claves = list(r["limites"])
    cab_u = "".join(f'<td bgcolor="#0F172A" align="center" style="color:white"><b>{PARAMS[k][2]} ({PARAMS[k][1]})</b></td>' for k in claves)
    t_u = ""
    for i, m in enumerate(r["muestras"], start=1):
        e = peor(m["estados"].values())
        bg = "#F8FAFC" if i % 2 == 0 else "#FFFFFF"
        celdas = "".join(f'<td bgcolor="{bg}" align="center" style="color:{colores[m["estados"][k]] if m["estados"][k] != "ok" else "#0F172A"}">{val(k, m["resultados"][k])}</td>' for k in claves)
        t_u += f'<tr><td bgcolor="{bg}" align="center">{i}</td>{celdas}<td bgcolor="{bg}" align="center" style="color:{colores[e]}"><b>{ESTADOS[e]["param"]}</b></td></tr>'

    html = f"""
<html><body style="font-family:'{FAMILIA}'; font-size:9pt; color:#0F172A;">
<table width="100%" cellspacing="0" cellpadding="0"><tr>
 <td><span style="font-size:20pt; font-weight:700; color:#0F766E;">ApexQuant</span><br>
     <span style="color:#64748B;">Scanner NIR · Control de calidad de productos de exportación</span></td>
 <td align="right"><span style="font-size:13pt; font-weight:700;">ACTA DE INSPECCIÓN</span><br>
     <span style="color:#64748B;">N° {r["id"]}<br>{fecha(r):%d/%m/%Y  %H:%M} h</span></td>
</tr></table>
<hr>
<p style="font-size:11pt; font-weight:700; color:#0F766E;">1. Datos de la carga</p>
<table width="100%" cellspacing="1" cellpadding="3" bgcolor="#E2E8F0">{t_datos}</table>
<p style="font-size:11pt; font-weight:700; color:#0F766E;">2. Resultados del análisis NIR (media del lote)</p>
<table width="100%" cellspacing="1" cellpadding="5" bgcolor="#E2E8F0">
<tr><td bgcolor="#0F172A" style="color:white"><b>Parámetro</b></td><td bgcolor="#0F172A" align="center" style="color:white"><b>Unidad</b></td>
<td bgcolor="#0F172A" align="center" style="color:white"><b>Especificación</b></td><td bgcolor="#0F172A" align="center" style="color:white"><b>Media</b></td>
<td bgcolor="#0F172A" align="center" style="color:white"><b>Mín.</b></td><td bgcolor="#0F172A" align="center" style="color:white"><b>Máx.</b></td>
<td bgcolor="#0F172A" align="center" style="color:white"><b>Estado</b></td></tr>
{t_res}</table>
<p>Unidades conformes: <b>{num(r["conformidad"], 0)} %</b> de {len(r["muestras"])} unidades analizadas.</p>
<p style="font-size:11pt; font-weight:700; color:#0F766E;">3. Detalle por unidad</p>
<table width="100%" cellspacing="1" cellpadding="2" bgcolor="#E2E8F0" style="font-size:8pt">
<tr><td bgcolor="#0F172A" align="center" style="color:white"><b>N°</b></td>{cab_u}<td bgcolor="#0F172A" align="center" style="color:white"><b>Estado</b></td></tr>
{t_u}</table>
<p style="font-size:11pt; font-weight:700; color:#0F766E;">4. Dictamen</p>
<table width="100%" cellspacing="1" cellpadding="8" bgcolor="{col}"><tr><td bgcolor="{suave}">
<span style="font-size:16pt; font-weight:700; color:{col};">{tit}</span><br>{sub}</td></tr></table>
<p>Observaciones: ____________________________________________________________________________</p>
<p>______________________________________________________________________________________</p>
<br>
<table width="100%" cellspacing="0" cellpadding="4"><tr>
<td width="45%" align="center">______________________________<br>Inspector · {r["inspector"]}</td><td width="10%"></td>
<td width="45%" align="center">______________________________<br>Responsable de la carga</td></tr></table>
<br>
<p style="font-size:7pt; color:#64748B;">Documento generado por ApexQuant Scanner (MVP de demostración, Sistemas de Representación — UCP 2026).
Los rangos de especificación son ilustrativos y no reemplazan la certificación oficial de SENASA ni los requisitos del país de destino.</p>
</body></html>"""
    doc = QTextDocument()
    doc.setDefaultFont(QFont(FAMILIA, 9))
    doc.setHtml(html)
    w = QPdfWriter(str(ruta))
    w.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    w.setPageMargins(QMarginsF(16, 14, 16, 14), QPageLayout.Unit.Millimeter)
    w.setTitle(f"Acta de inspección {r['id']}")
    w.setCreator("ApexQuant Scanner")
    doc.print_(w)


# ═══════════════════════════════════════════════════════════════════════════
#  ESTILO GLOBAL (QSS)
# ═══════════════════════════════════════════════════════════════════════════

def crear_iconos():
    """Flechas para combos dibujadas en tiempo de ejecución (sin archivos externos)."""
    d = Path(tempfile.gettempdir()) / "apexquant_ui"
    d.mkdir(exist_ok=True)
    rutas = {}
    for nombre, color in (("flecha", C["muted"]), ("flecha_off", C["faint"])):
        pm = QPixmap(24, 24)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(color), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline([QPointF(6, 9), QPointF(12, 15), QPointF(18, 9)])
        p.end()
        ruta = d / f"{nombre}.png"
        pm.save(str(ruta))
        rutas[nombre] = ruta.as_posix()
    return rutas


def hoja_estilo(ic):
    return f"""
* {{ font-family: "{FAMILIA}"; font-size: 13px; color: {C['text']}; }}
QMainWindow, QFrame#raiz {{ background: {C['bg']}; }}
QFrame#lateral {{ background: {C['side']}; border-right: 1px solid {C['border']}; }}
QFrame#card {{ background: {C['card']}; border: 1px solid {C['border']}; border-radius: 14px; }}
QFrame#card2 {{ background: {C['card2']}; border: none; border-radius: 10px; }}
QFrame#fila {{ background: transparent; border-radius: 10px; }}
QFrame#fila:hover {{ background: {C['card2']}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {C['card2']}; color: {C['text']}; border: 1px solid {C['border']}; padding: 6px; }}

QPushButton#nav {{ text-align: left; padding: 11px 16px; border: none; border-radius: 10px;
                   color: {C['muted']}; font-size: 14px; background: transparent; }}
QPushButton#nav:hover {{ background: {C['card']}; color: {C['text']}; }}
QPushButton#nav:checked {{ background: {C['card']}; color: {C['text']}; font-weight: 600; }}
QPushButton#primario {{ background: {C['accent']}; color: #04201C; border: none; border-radius: 10px;
                        padding: 10px 18px; font-weight: 700; font-size: 14px; }}
QPushButton#primario:hover {{ background: {C['accent_h']}; }}
QPushButton#primario:disabled {{ background: {mezclar(C['card'], C['accent'], 0.35)}; color: {mezclar(C['card'], C['accent'], 0.8)}; }}
QPushButton#secundario {{ background: {C['card2']}; border: 1px solid {C['border']}; border-radius: 10px;
                          padding: 9px 16px; }}
QPushButton#secundario:hover {{ background: {C['hover']}; }}
QPushButton#secundario:disabled {{ color: {C['faint']}; }}
QPushButton#peligro {{ background: {mezclar(C['card'], C['bad'], 0.14)}; color: {C['bad']}; border: none;
                       border-radius: 10px; padding: 9px 16px; }}
QPushButton#peligro:hover {{ background: {mezclar(C['card'], C['bad'], 0.26)}; }}
QPushButton#enlace {{ background: transparent; border: none; color: {C['accent']}; font-weight: 600; padding: 4px 8px; }}
QPushButton#enlace:hover {{ color: {C['accent_h']}; }}
QPushButton#icono {{ background: {C['card2']}; border: 1px solid {C['border']}; border-radius: 9px;
                     font-size: 15px; min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; padding: 0; }}
QPushButton#icono:hover {{ background: {C['hover']}; }}
QPushButton#icono:disabled {{ color: {C['faint']}; }}

QFrame#segmentado {{ background: {C['bg']}; border: 1px solid {C['border']}; border-radius: 10px; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 7px; padding: 6px 13px; color: {C['muted']}; }}
QPushButton#seg:hover {{ color: {C['text']}; }}
QPushButton#seg:checked {{ background: {C['hover']}; color: {C['text']}; font-weight: 600; }}

QAbstractSpinBox {{ background: {C['card2']}; border: 1px solid {C['border']}; border-radius: 9px; padding: 0 10px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QAbstractSpinBox:focus {{ border: 1px solid {mezclar(C['card2'], C['accent'], 0.8)}; }}
QLineEdit, QComboBox {{
    background: {C['card2']}; border: 1px solid {C['border']}; border-radius: 9px;
    padding: 0 12px; min-height: 36px; selection-background-color: {mezclar(C['card2'], C['accent'], 0.45)}; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {mezclar(C['card2'], C['accent'], 0.8)}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {C['muted']}; background: {mezclar(C['card'], C['card2'], 0.5)}; }}
QComboBox QLineEdit {{ border: none; background: transparent; padding: 0; min-height: 0; }}
QLineEdit[error="true"], QComboBox[error="true"] {{ border: 1px solid {C['bad']}; background: {mezclar(C['card2'], C['bad'], 0.08)}; }}
QListView#completer {{ background: {C['card2']}; border: 1px solid {C['border']}; border-radius: 8px; padding: 4px; outline: 0; }}
QListView#completer::item {{ min-height: 28px; padding: 0 8px; border-radius: 6px; }}
QListView#completer::item:selected {{ background: {C['hover']}; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: url("{ic['flecha']}"); width: 14px; height: 14px; }}
QComboBox::down-arrow:disabled {{ image: url("{ic['flecha_off']}"); }}
QComboBox QAbstractItemView {{ background: {C['card2']}; border: 1px solid {C['border']}; border-radius: 8px;
                              padding: 4px; outline: 0; selection-background-color: {C['hover']}; }}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 8px; border-radius: 6px; }}
QComboBox QAbstractItemView::item:selected {{ background: {C['hover']}; }}
QComboBox QAbstractItemView::item:disabled {{ color: {C['accent']}; font-size: 11px; font-weight: 700; }}

QTableWidget {{ background: {C['card']}; border: none; gridline-color: transparent; outline: 0;
               alternate-background-color: {mezclar(C['card'], C['card2'], 0.5)};
               selection-background-color: {mezclar(C['card'], C['accent'], 0.20)}; selection-color: {C['text']}; }}
QTableWidget::item {{ padding: 0 8px; border: none; }}
QHeaderView::section {{ background: {C['card2']}; color: {C['muted']}; border: none; padding: 9px 8px;
                       font-weight: 600; font-size: 12px; }}
QTableCornerButton::section {{ background: {C['card2']}; border: none; }}

QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ padding: 10px 12px; border-radius: 9px; color: {C['muted']}; margin: 1px 0; }}
QListWidget::item:hover {{ background: {C['card2']}; color: {C['text']}; }}
QListWidget::item:selected {{ background: {C['hover']}; color: {C['text']}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget#contenido {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {C['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {C['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {C['border']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QProgressBar {{ background: {C['card2']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 3px; }}
QSlider::groove:horizontal {{ height: 6px; background: {C['card2']}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {C['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {C['text']}; width: 18px; height: 18px; margin: -6px 0; border-radius: 9px; }}
QRadioButton {{ spacing: 10px; padding: 4px 0; }}
QRadioButton:disabled {{ color: {C['faint']}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 9px; border: 2px solid {C['border']}; background: {C['card2']}; }}
QRadioButton::indicator:checked {{ border: 5px solid {C['accent']}; background: {C['text']}; width: 8px; height: 8px; }}
QMessageBox {{ background: {C['card']}; }}
"""


# ═══════════════════════════════════════════════════════════════════════════
#  COMPONENTES
# ═══════════════════════════════════════════════════════════════════════════

def L(texto="", tam=13, color=None, negrita=False, wrap=False):
    lb = QLabel(texto)
    lb.setStyleSheet(f"color: {color or C['text']}; font-size: {tam}px; font-weight: {700 if negrita else 400};")
    if wrap:
        lb.setWordWrap(True)
    return lb


def estilo_label(lb, tam=13, color=None, negrita=False):
    lb.setStyleSheet(f"color: {color or C['text']}; font-size: {tam}px; font-weight: {700 if negrita else 400};")


def tarjeta(margen=20, espacio=10):
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(margen, margen - 2, margen, margen - 2)
    lay.setSpacing(espacio)
    return f, lay


def boton(texto, tipo="secundario", accion=None):
    b = QPushButton(texto)
    b.setObjectName(tipo)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if accion:
        b.clicked.connect(accion)
    return b


def encabezado(titulo, sub=None):
    h = QHBoxLayout()
    h.setSpacing(10)
    h.addWidget(L(titulo, 15, negrita=True))
    if sub:
        h.addWidget(L(sub, 12, C["faint"]))
    h.addStretch(1)
    return h


def separador():
    s = QFrame()
    s.setFixedHeight(1)
    s.setStyleSheet(f"background: {C['border']}; border: none;")
    return s


def combo(items=(), editable=False):
    cb = QComboBox()
    cb.setView(QListView())
    cb.setEditable(editable)
    cb.setCursor(Qt.CursorShape.PointingHandCursor)
    cb.setMaxVisibleItems(16)
    for it in items:
        cb.addItem(it, it)
    return cb


def llenar_productos(cb, productos, todos=False, vacio=False):
    actual = cb.currentData()
    cb.blockSignals(True)
    cb.clear()
    if todos:
        cb.addItem("Todos los productos", "__todos__")
    if vacio:
        cb.addItem("Seleccioná un producto…", None)
    for cat in CATEGORIAS:
        grupo = [p for p in productos if p["categoria"] == cat]
        if not grupo:
            continue
        cb.addItem(f"   {cat.upper()}", None)
        cb.model().item(cb.count() - 1).setEnabled(False)
        for p in grupo:
            cb.addItem(f"{p['icono']}   {p['nombre']}", p["nombre"])
    i = cb.findData(actual) if actual else -1
    if i < 0:
        i = 0 if (todos or vacio) else 1
    cb.setCurrentIndex(i)
    cb.blockSignals(False)


class Chip(QLabel):
    def __init__(self, texto="", color=None, fondo=None):
        super().__init__()
        self.set(texto, color, fondo)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set(self, texto, color=None, fondo=None):
        color = color or C["accent"]
        self.setText(texto)
        self.setStyleSheet(f"color: {color}; background: {mezclar(fondo or C['card'], color, 0.15)};"
                           f"border-radius: 8px; padding: 3px 10px; font-size: 11px; font-weight: 700;")


class Insignia(Chip):
    def estado(self, est, texto=None, fondo=None):
        if est is None:
            self.set("—", C["faint"], fondo)
            return
        self.set(f"●  {texto or ESTADOS[est]['lote']}", ESTADOS[est]["color"], fondo)


class Segmentado(QFrame):
    cambiado = Signal(str)

    def __init__(self, opciones):
        super().__init__()
        self.setObjectName("segmentado")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(3, 3, 3, 3)
        self.lay.setSpacing(2)
        self.grupo = QButtonGroup(self)
        self.grupo.setExclusive(True)
        self.grupo.buttonClicked.connect(lambda b: self.cambiado.emit(b.text()))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.opciones(opciones)

    def opciones(self, opciones, seleccion=None):
        for b in self.grupo.buttons():
            self.grupo.removeButton(b)
            b.deleteLater()
        for o in opciones:
            b = QPushButton(o)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedWidth(QFontMetrics(fuente(13, True)).horizontalAdvance(o) + 30)
            self.grupo.addButton(b)
            self.lay.addWidget(b)
        anchos = sum(QFontMetrics(fuente(13, True)).horizontalAdvance(o) + 30 for o in opciones)
        self.setFixedWidth(anchos + 2 * max(0, len(opciones) - 1) + 8)
        self.set(seleccion if seleccion in opciones else (opciones[0] if opciones else None))

    def set(self, texto):
        for b in self.grupo.buttons():
            b.setChecked(b.text() == texto)

    def valor(self):
        b = self.grupo.checkedButton()
        return b.text() if b else None


class Indicador(QFrame):
    def __init__(self, titulo, icono=""):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(L(titulo, 12, C["muted"]))
        top.addStretch(1)
        if icono:
            top.addWidget(L(icono, 14, C["faint"]))
        lay.addLayout(top)
        self.valor = L("—", 28, negrita=True)
        self.pie = L("", 12, C["faint"])
        lay.addWidget(self.valor)
        lay.addWidget(self.pie)

    def set(self, valor, pie="", color=None):
        self.valor.setText(valor)
        estilo_label(self.valor, 28, color or C["text"], True)
        self.pie.setText(pie)


class Paso(QWidget):
    """Selector numérico con botones − / +."""
    cambiado = Signal(int)

    def __init__(self, minimo=3, maximo=30, valor=10):
        super().__init__()
        self.mn, self.mx = minimo, maximo
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.b1 = boton("−", "icono", lambda: self.set(self.v - 1))
        self.lb = L("", 15, negrita=True)
        self.lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb.setMinimumWidth(44)
        self.b2 = boton("+", "icono", lambda: self.set(self.v + 1))
        h.addWidget(self.b1)
        h.addWidget(self.lb)
        h.addWidget(self.b2)
        h.addWidget(L("unidades", 12, C["faint"]))
        h.addStretch(1)
        self.v = valor
        self.set(valor)

    def set(self, v):
        self.v = max(self.mn, min(self.mx, int(v)))
        self.lb.setText(str(self.v))
        self.cambiado.emit(self.v)

    def setEnabled(self, e):
        self.b1.setEnabled(e)
        self.b2.setEnabled(e)


class Interruptor(QAbstractButton):
    """Interruptor tipo iOS dibujado a mano."""

    def __init__(self, texto=""):
        super().__init__()
        self.setCheckable(True)
        self.setText(texto)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        fm = QFontMetrics(fuente(13))
        return QSize(56 + fm.horizontalAdvance(self.text()), 30)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = 24
        y = (self.height() - h) / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(C["accent"] if self.isChecked() else C["border"]))
        p.drawRoundedRect(QRectF(0, y, 44, h), h / 2, h / 2)
        p.setBrush(qc("#FFFFFF"))
        x = 44 - h + 3 if self.isChecked() else 3
        p.drawEllipse(QRectF(x, y + 3, h - 6, h - 6))
        p.setFont(fuente(13))
        p.setPen(qc(C["text"]))
        p.drawText(QRectF(56, 0, self.width() - 56, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


# ─────────────────────────────  Lienzos (QPainter)  ───────────────────────

class Lienzo(QWidget):
    def __init__(self, alto=None):
        super().__init__()
        if alto:
            self.setMinimumHeight(alto)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.pintar(p, self.width(), self.height())
        p.end()

    def pintar(self, p, w, h):
        pass


def texto(p, x, y, s, color, tam=11, negrita=False, alin="i"):
    """Dibuja texto centrado verticalmente en y. alin: i(zquierda) c(entro) d(erecha)."""
    f = fuente(tam, negrita)
    p.setFont(f)
    p.setPen(qc(color) if isinstance(color, str) else color)
    fm = QFontMetrics(f)
    ancho = fm.horizontalAdvance(s)
    if alin == "c":
        x -= ancho / 2
    elif alin == "d":
        x -= ancho
    p.drawText(QPointF(x, y + (fm.ascent() - fm.descent()) / 2), s)
    return ancho


class Logo(Lienzo):
    def __init__(self, tam=40):
        super().__init__()
        self.setFixedSize(tam, tam)

    def pintar(self, p, w, h):
        p.setPen(QPen(qc(C["accent"], 140), 2))
        p.setBrush(qc(C["accent"], 18))
        p.drawRoundedRect(QRectF(1.5, 1.5, w - 3, h - 3), 11, 11)
        path = QPainterPath()
        for i in range(41):
            x = 7 + i * (w - 14) / 40
            y = h / 2 - math.sin(i / 40 * math.pi * 2.6) * 8 * math.exp(-((i - 20) / 14) ** 2)
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        p.setPen(QPen(qc(C["accent"]), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)


class IconoEstado(Lienzo):
    def __init__(self):
        super().__init__()
        self.setFixedSize(58, 58)
        self.estado = None
        self.fase = 0

    def set(self, estado):
        self.estado = estado
        self.update()

    def pintar(self, p, w, h):
        r = QRectF(3, 3, w - 6, h - 6)
        e = self.estado
        if e in ESTADOS:
            col = ESTADOS[e]["color"]
            p.setPen(QPen(qc(col), 2))
            p.setBrush(qc(col, 40))
            p.drawEllipse(r)
            texto(p, w / 2, h / 2, ESTADOS[e]["icono"], col, 24, True, "c")
        elif e == "abierta":
            p.setPen(QPen(qc(C["accent2"], 70), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r)
            p.setPen(QPen(qc(C["accent2"]), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(r, (90 - self.fase) * 16, -100 * 16)
            texto(p, w / 2, h / 2, "NIR", C["accent2"], 11, True, "c")
        else:
            p.setPen(QPen(qc(C["accent"], 110), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r)
            p.setPen(QPen(qc(C["accent"]), 2))
            p.drawEllipse(QRectF(w / 2 - 11, h / 2 - 11, 22, 22))
            p.setBrush(qc(C["accent"]))
            p.drawEllipse(QRectF(w / 2 - 3.5, h / 2 - 3.5, 7, 7))


class Led(Lienzo):
    def __init__(self, tam=30):
        super().__init__()
        self.setFixedSize(tam, tam)
        self.color = None

    def set(self, color):
        if color != self.color:
            self.color = color
            self.update()

    def pintar(self, p, w, h):
        c = QPointF(w / 2, h / 2)
        if self.color:
            g = QRadialGradient(c, w / 2)
            g.setColorAt(0, qc(self.color, 120))
            g.setColorAt(1, qc(self.color, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(c, w / 2, h / 2)
            p.setBrush(qc(self.color))
            p.setPen(QPen(qc(mezclar(self.color, "#FFFFFF", 0.5)), 1))
        else:
            p.setBrush(qc("#06090E"))
            p.setPen(QPen(qc(C["border"]), 1))
        p.drawEllipse(c, w * 0.24, h * 0.24)


class GraficoEspectro(Lienzo):
    ANOT = [(970, "H₂O", 0), (1200, "C–H", 1), (1450, "O–H", 0), (1510, "N–H", 1), (1690, "C–H", 0)]

    def __init__(self, alto=200, compacto=False):
        super().__init__(alto)
        self.actual, self.previas, self.revelado, self.compacto = None, [], 1.0, compacto

    def set(self, actual, previas=(), revelado=1.0):
        self.actual, self.previas, self.revelado = actual, list(previas), revelado
        self.update()

    def pintar(self, p, w, h):
        pl, pr, pt, pb = (44 if not self.compacto else 6), 10, (24 if not self.compacto else 8), 24
        x0, x1, y0, y1 = pl, w - pr, pt, h - pb
        wls = EscanerSimulado.LONGITUDES
        X = lambda wl: x0 + (wl - wls[0]) / (wls[-1] - wls[0]) * (x1 - x0)
        p.setPen(QPen(qc(C["border"], 150), 1))
        for i in range(5):
            y = y0 + (y1 - y0) * i / 4
            p.drawLine(QPointF(x0, y), QPointF(x1, y))
        curvas = [c for c in [self.actual] + self.previas if c]
        if not curvas:
            texto(p, w / 2, h / 2, "Sin datos espectrales — escaneá una unidad", C["faint"], 12, alin="c")
            return
        lo = min(min(c) for c in curvas)
        hi = max(max(c) for c in curvas)
        pad = (hi - lo) * 0.10 or 0.05
        lo, hi = lo - pad, hi + pad
        Y = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)
        if not self.compacto:
            for i in range(5):
                y = y0 + (y1 - y0) * i / 4
                texto(p, x0 - 8, y, num(hi - (hi - lo) * i / 4, 2), C["faint"], 10, alin="d")
            for wl, et, nivel in self.ANOT:
                p.setPen(QPen(qc(C["accent2"], 45), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(X(wl), y0), QPointF(X(wl), y1))
                texto(p, X(wl), y0 - 9 - nivel * 0, et, qc(C["accent2"], 170), 10, alin="c")
        for wl in (900, 1100, 1300, 1500, 1700):
            al = "i" if wl == 900 else ("d" if wl == 1700 else "c")
            texto(p, X(wl), y1 + 13, f"{wl} nm" if wl == 1700 else str(wl), C["faint"], 10, alin=al)

        for c in self.previas:
            path = QPainterPath()
            for i, (wl, v) in enumerate(zip(wls, c)):
                path.moveTo(X(wl), Y(v)) if i == 0 else path.lineTo(X(wl), Y(v))
            p.setPen(QPen(qc(C["muted"], 55), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        if self.actual:
            n = max(2, int(len(self.actual) * self.revelado))
            pts = [QPointF(X(wl), Y(v)) for wl, v in zip(wls[:n], self.actual[:n])]
            linea = QPainterPath(pts[0])
            for q in pts[1:]:
                linea.lineTo(q)
            area = QPainterPath(linea)
            area.lineTo(pts[-1].x(), y1)
            area.lineTo(pts[0].x(), y1)
            area.closeSubpath()
            g = QLinearGradient(0, y0, 0, y1)
            g.setColorAt(0, qc(C["accent"], 80))
            g.setColorAt(1, qc(C["accent"], 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawPath(area)
            p.setPen(QPen(qc(C["accent"]), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(linea)
            if self.revelado < 1:
                q = pts[-1]
                p.setPen(QPen(qc(C["accent2"], 140), 1))
                p.drawLine(QPointF(q.x(), y0), QPointF(q.x(), y1))
                g2 = QRadialGradient(q, 14)
                g2.setColorAt(0, qc(C["accent2"], 200))
                g2.setColorAt(1, qc(C["accent2"], 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(g2))
                p.drawEllipse(q, 14, 14)
                p.setBrush(qc("#FFFFFF"))
                p.drawEllipse(q, 3.5, 3.5)


class BarraRango(Lienzo):
    """Zonas fuera / límite / especificación con marcador de la media y puntos por unidad."""

    def __init__(self, alto=40):
        super().__init__(alto)
        self.setFixedHeight(alto)
        self.mn, self.mx, self.valor, self.estado, self.puntos, self.clave = 0, 1, None, None, [], None

    def set(self, mn, mx, valor=None, estado=None, puntos=(), clave=None):
        self.mn, self.mx, self.valor, self.estado, self.puntos, self.clave = mn, mx, valor, estado, list(puntos), clave
        self.update()

    def pintar(self, p, w, h):
        mn, mx = self.mn, self.mx
        ancho = mx - mn
        lo, hi = mn - ancho * 0.5, mx + ancho * 0.5
        x0, x1 = 8, w - 8
        X = lambda v: x0 + (min(max(v, lo), hi) - lo) / (hi - lo) * (x1 - x0)
        y, g = 14, 8
        wm = ancho * MARGEN
        pista = QPainterPath()
        pista.addRoundedRect(QRectF(x0, y - g / 2, x1 - x0, g), g / 2, g / 2)
        p.save()
        p.setClipPath(pista)
        for a, b, col in [(lo, mn, C["bad"]), (mn, mn + wm, C["warn"]), (mn + wm, mx - wm, C["ok"]),
                          (mx - wm, mx, C["warn"]), (mx, hi, C["bad"])]:
            p.fillRect(QRectF(X(a), y - g / 2, X(b) - X(a) + 0.5, g), qc(col, 80))
        p.restore()
        fmt = (lambda v: val(self.clave, v)) if self.clave else num
        texto(p, X(mn), y + 17, fmt(mn), C["faint"], 10, alin="c")
        texto(p, X(mx), y + 17, fmt(mx), C["faint"], 10, alin="c")
        for v, est in self.puntos:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qc(ESTADOS[est]["color"], 150))
            p.drawEllipse(QPointF(X(v), y), 2.6, 2.6)
        if self.valor is not None:
            col = ESTADOS[self.estado]["color"] if self.estado else C["text"]
            p.setPen(QPen(qc(C["card"]), 3))
            p.setBrush(qc(col))
            p.drawEllipse(QPointF(X(self.valor), y), 7.5, 7.5)


class GraficoMuestras(Lienzo):
    """Fila de unidades del lote: pendiente / escaneando / resultado."""

    def __init__(self):
        super().__init__(44)
        self.plan, self.estados, self.activa, self.fase = 0, [], None, 0

    def set(self, plan, estados, activa=None):
        self.plan, self.estados, self.activa = plan, list(estados), activa
        self.update()

    def pintar(self, p, w, h):
        if not self.plan:
            texto(p, 0, h / 2, "Iniciá una inspección para comenzar el muestreo", C["faint"], 12)
            return
        d = min(30, (w - 4) / self.plan - 7)
        paso = d + 7
        for i in range(self.plan):
            x, y = 2 + i * paso, h / 2 - d / 2
            r = QRectF(x, y, d, d)
            if i < len(self.estados):
                col = ESTADOS[self.estados[i]]["color"]
                p.setPen(QPen(qc(col), 1.6))
                p.setBrush(qc(col, 45))
                p.drawEllipse(r)
                texto(p, x + d / 2, h / 2, str(i + 1), col, 11 if d > 22 else 9, True, "c")
            elif i == self.activa:
                a = 120 + int(100 * abs(math.sin(self.fase)))
                p.setPen(QPen(qc(C["accent2"], a), 2))
                p.setBrush(qc(C["accent2"], 30))
                p.drawEllipse(r)
                texto(p, x + d / 2, h / 2, str(i + 1), C["accent2"], 11 if d > 22 else 9, True, "c")
            else:
                p.setPen(QPen(qc(C["border"]), 1.4, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(r)
                texto(p, x + d / 2, h / 2, str(i + 1), C["faint"], 11 if d > 22 else 9, alin="c")


class GraficoTendencia(Lienzo):
    def __init__(self, alto=260):
        super().__init__(alto)
        self.setMouseTracking(True)
        self.puntos, self.lim, self.clave, self._pts, self._hover = [], None, None, [], None

    def set(self, puntos, lim, clave):
        self.puntos, self.lim, self.clave, self._hover = puntos, lim, clave, None
        self.update()

    def mouseMoveEvent(self, e):
        if not self._pts:
            return
        i = min(range(len(self._pts)), key=lambda j: abs(self._pts[j][0] - e.position().x()))
        nuevo = i if abs(self._pts[i][0] - e.position().x()) < 18 else None
        if nuevo != self._hover:
            self._hover = nuevo
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def pintar(self, p, w, h):
        self._pts = []
        if not self.puntos or not self.lim:
            texto(p, w / 2, h / 2, "Todavía no hay inspecciones para este producto", C["faint"], 12, alin="c")
            return
        pl, pr, pt, pb = 52, 96, 14, 30
        x0, x1, y0, y1 = pl, w - pr, pt, h - pb
        mn, mx = self.lim
        vals = [q[1] for q in self.puntos]
        lo, hi = min(min(vals), mn), max(max(vals), mx)
        pad = (hi - lo) * 0.18 or 1
        lo, hi = lo - pad, hi + pad
        Y = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)
        n = len(self.puntos)
        X = (lambda i: (x0 + x1) / 2) if n == 1 else (lambda i: x0 + i / (n - 1) * (x1 - x0))
        wm = (mx - mn) * MARGEN
        p.fillRect(QRectF(x0, Y(mx), x1 - x0, Y(mn) - Y(mx)), qc(C["ok"], 18))
        p.fillRect(QRectF(x0, Y(mx), x1 - x0, Y(mx - wm) - Y(mx)), qc(C["warn"], 22))
        p.fillRect(QRectF(x0, Y(mn + wm), x1 - x0, Y(mn) - Y(mn + wm)), qc(C["warn"], 22))
        for i in range(6):
            v = lo + (hi - lo) * i / 5
            p.setPen(QPen(qc(C["border"], 120), 1))
            p.drawLine(QPointF(x0, Y(v)), QPointF(x1, Y(v)))
            texto(p, x0 - 8, Y(v), val(self.clave, v), C["faint"], 10, alin="d")
        for v, et in ((mx, "Máx"), (mn, "Mín")):
            p.setPen(QPen(qc(C["bad"], 170), 1.2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x0, Y(v)), QPointF(x1, Y(v)))
            texto(p, x1 + 8, Y(v), f"{et} {val(self.clave, v)}", qc(C["bad"], 220), 10, True)
        texto(p, x1 + 8, Y((mn + mx) / 2), "especificación", qc(C["ok"], 190), 10)
        paso, previa = max(1, n // 8), None
        for i in range(0, n, paso):
            et = self.puntos[i][0].strftime("%d/%m")
            if et != previa:
                texto(p, X(i), y1 + 16, et, C["faint"], 10, alin="c")
            previa = et
        if n > 1:
            path = QPainterPath(QPointF(X(0), Y(vals[0])))
            for i in range(1, n):
                path.lineTo(X(i), Y(vals[i]))
            p.setPen(QPen(qc(C["accent"], 200), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        for i, (f, v, est, ident, extra) in enumerate(self.puntos):
            x, y = X(i), Y(v)
            col = ESTADOS[est]["color"]
            p.setPen(QPen(qc(C["card"]), 2))
            p.setBrush(qc(col))
            p.drawEllipse(QPointF(x, y), 4.5, 4.5)
            self._pts.append((x, y, i))
        if self._hover is not None:
            x, y, i = self._pts[self._hover]
            f, v, est, ident, extra = self.puntos[i]
            col = ESTADOS[est]["color"]
            p.setPen(QPen(qc(col), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(x, y), 9, 9)
            lineas = [f"{ident}  ·  {val(self.clave, v)} {PARAMS[self.clave][1]}", f"{f:%d/%m/%Y %H:%M}  ·  {extra}"]
            fm = QFontMetrics(fuente(12))
            tw = max(fm.horizontalAdvance(s) for s in lineas) + 24
            th = 50
            tx = x + 14 if x + 14 + tw < w else x - 14 - tw
            ty = max(4, min(h - th - 4, y - th - 12))
            p.setPen(QPen(qc(C["border"]), 1))
            p.setBrush(qc(C["card2"]))
            p.drawRoundedRect(QRectF(tx, ty, tw, th), 8, 8)
            texto(p, tx + 12, ty + 16, lineas[0], C["text"], 12, True)
            texto(p, tx + 12, ty + 34, lineas[1], C["muted"], 11)


class GraficoDona(Lienzo):
    def __init__(self, alto=170):
        super().__init__(alto)
        self.conteos = {}

    def set(self, conteos):
        self.conteos = conteos
        self.update()

    def pintar(self, p, w, h):
        total = sum(self.conteos.values())
        d = min(w, h) - 20
        r = QRectF(w / 2 - d / 2, h / 2 - d / 2, d, d)
        p.setPen(QPen(qc(C["card2"]), 16))
        p.drawEllipse(r)
        inicio = 90.0
        if total:
            for k in ("ok", "limite", "fuera"):
                c = self.conteos.get(k, 0)
                if not c:
                    continue
                ext = 360.0 * c / total
                p.setPen(QPen(qc(ESTADOS[k]["color"]), 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                p.drawArc(r, int(inicio * 16), int(-ext * 16))
                inicio -= ext
        texto(p, w / 2, h / 2 - 8, str(total), C["text"], 26, True, "c")
        texto(p, w / 2, h / 2 + 16, "lotes", C["faint"], 11, alin="c")


class BarrasProductos(Lienzo):
    """Barras apiladas por producto (aptos / observados / rechazados)."""

    def __init__(self):
        super().__init__(200)
        self.filas = []

    def set(self, filas):
        self.filas = filas
        self.setMinimumHeight(max(120, 30 * len(filas) + 10))
        self.update()

    def pintar(self, p, w, h):
        if not self.filas:
            texto(p, w / 2, h / 2, "Sin inspecciones en el período", C["faint"], 12, alin="c")
            return
        maximo = max(sum(c.values()) for _, _, c in self.filas)
        x_bar, x_fin = 210, w - 44
        for i, (icono, nombre, c) in enumerate(self.filas):
            y = 16 + i * 30
            texto(p, 2, y, icono, C["text"], 15)
            texto(p, 30, y, nombre if len(nombre) < 24 else nombre[:22] + "…", C["text"], 12)
            total = sum(c.values())
            x = x_bar
            ancho_total = (x_fin - x_bar) * total / maximo
            pista = QPainterPath()
            pista.addRoundedRect(QRectF(x_bar, y - 6, max(ancho_total, 12), 12), 6, 6)
            p.save()
            p.setClipPath(pista)
            for k in ("ok", "limite", "fuera"):
                a = ancho_total * c.get(k, 0) / total
                p.fillRect(QRectF(x, y - 6, a + 0.5, 12), qc(ESTADOS[k]["color"], 210))
                x += a
            p.restore()
            texto(p, x_bar + ancho_total + 10, y, str(total), C["muted"], 12, True)


# ═══════════════════════════════════════════════════════════════════════════
#  PÁGINAS
# ═══════════════════════════════════════════════════════════════════════════

class Pagina(QWidget):
    titulo = ""
    subtitulo = ""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.construir()

    def construir(self):
        pass

    def refrescar(self, **kw):
        pass


def scroll(widget):
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    widget.setObjectName("contenido")
    sa.setWidget(widget)
    sa.viewport().setAutoFillBackground(False)
    widget.setAutoFillBackground(False)
    return sa


# ─────────────────────────────  Panel  ─────────────────────────────────────

class FilaInspeccion(QFrame):
    def __init__(self, r, al_click):
        super().__init__()
        self.setObjectName("fila")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.al_click = al_click
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 7, 10, 7)
        h.setSpacing(12)
        h.addWidget(L(r["icono"], 18))
        a = L(r["id"], 13, negrita=True)
        a.setFixedWidth(108)
        h.addWidget(a)
        b = L(r["producto"], 13)
        b.setFixedWidth(150)
        h.addWidget(b)
        c = L(r["contenedor"], 12, C["muted"])
        c.setFixedWidth(120)
        h.addWidget(c)
        h.addWidget(L(f"{r['exportador']}  →  {r['destino']}", 12, C["faint"]), 1)
        h.addWidget(L(hace(fecha(r)), 12, C["faint"]))
        ins = Insignia()
        ins.estado(r["estado"])
        ins.setFixedWidth(108)
        ins.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(ins)

    def mousePressEvent(self, e):
        self.al_click()


class PaginaPanel(Pagina):
    titulo = "Panel de control"
    subtitulo = "Actividad de inspección NIR de cargas de exportación"

    def construir(self):
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(16)
        self.k = [Indicador("Inspecciones de hoy", "◎"), Indicador("Lotes aptos · 7 días", "✓"),
                  Indicador("Rechazos · 7 días", "✕"), Indicador("Unidades escaneadas · 7 días", "▦")]
        for i, k in enumerate(self.k):
            g.addWidget(k, 0, i)
            g.setColumnStretch(i, 1)

        cr, lr = tarjeta(14, 4)
        hr = encabezado("Últimas inspecciones", "clic para ver el detalle")
        hr.addWidget(boton("Ver historial  →", "enlace", lambda: self.app.mostrar("historial")))
        lr.addLayout(hr)
        self.lista = QVBoxLayout()
        self.lista.setSpacing(2)
        lr.addLayout(self.lista)
        lr.addStretch(1)
        g.addWidget(cr, 1, 0, 1, 3)

        cd, ld = tarjeta()
        ld.addLayout(encabezado("Dictámenes", "últimos 7 días"))
        self.dona = GraficoDona(160)
        ld.addWidget(self.dona)
        self.leyenda = QVBoxLayout()
        self.leyenda.setSpacing(4)
        ld.addLayout(self.leyenda)
        ld.addStretch(1)
        g.addWidget(cd, 1, 3)

        cp, lp = tarjeta()
        lp.addLayout(encabezado("Productos inspeccionados", "últimos 7 días · aptos / observados / rechazados"))
        self.barras = BarrasProductos()
        lp.addWidget(scroll(self.barras), 1)
        g.addWidget(cp, 2, 0, 1, 2)

        cx, lx = tarjeta()
        lx.addLayout(encabezado("Destinos principales", "lotes · últimos 7 días"))
        self.destinos = QVBoxLayout()
        self.destinos.setSpacing(8)
        lx.addLayout(self.destinos)
        lx.addStretch(1)
        g.addWidget(cx, 2, 2, 1, 2)
        g.setRowStretch(1, 5)
        g.setRowStretch(2, 4)

    @staticmethod
    def _limpiar(lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                PaginaPanel._limpiar(it.layout())

    def refrescar(self, **kw):
        ins = self.app.datos.inspecciones
        ahora = dt.datetime.now()
        hoy = [r for r in ins if fecha(r).date() == ahora.date()]
        sem = [r for r in ins if (ahora - fecha(r)).days < 7]
        cuenta = {e: sum(1 for r in sem if r["estado"] == e) for e in ESTADOS}
        self.k[0].set(str(len(hoy)), f"{len(sem)} en los últimos 7 días")
        if sem:
            pct = 100 * cuenta["ok"] / len(sem)
            self.k[1].set(f"{pct:.0f} %", f"{cuenta['ok']} de {len(sem)} lotes",
                          C["ok"] if pct >= 80 else (C["warn"] if pct >= 60 else C["bad"]))
        else:
            self.k[1].set("—", "sin inspecciones")
        self.k[2].set(str(cuenta["fuera"]), f"+ {cuenta['limite']} lotes observados", C["bad"] if cuenta["fuera"] else C["ok"])
        unidades = sum(len(r["muestras"]) for r in sem)
        self.k[3].set(str(unidades), f"en {len({r['contenedor'] for r in sem})} contenedores")

        self._limpiar(self.lista)
        if not ins:
            self.lista.addWidget(L("Todavía no hay inspecciones registradas.", 12, C["faint"]))
        for r in reversed(ins[-7:]):
            self.lista.addWidget(FilaInspeccion(r, lambda rid=r["id"]: self.app.mostrar("historial", seleccionar=rid)))

        self.dona.set(cuenta)
        self._limpiar(self.leyenda)
        tot = max(1, len(sem))
        for e, d in ESTADOS.items():
            f = QHBoxLayout()
            f.addWidget(L("●", 12, d["color"]))
            f.addWidget(L(d["lote"], 12, C["muted"]))
            f.addStretch(1)
            f.addWidget(L(f"{cuenta[e]}  ·  {100 * cuenta[e] / tot:.0f} %", 12, negrita=True))
            self.leyenda.addLayout(f)

        por = {}
        for r in sem:
            d = por.setdefault(r["producto"], {"icono": r["icono"], "c": {}})
            d["c"][r["estado"]] = d["c"].get(r["estado"], 0) + 1
        filas = sorted(((v["icono"], k, v["c"]) for k, v in por.items()), key=lambda t: -sum(t[2].values()))
        self.barras.set(filas)

        self._limpiar(self.destinos)
        dest = {}
        for r in sem:
            dest[r["destino"]] = dest.get(r["destino"], 0) + 1
        top = sorted(dest.items(), key=lambda t: -t[1])[:7]
        if not top:
            self.destinos.addWidget(L("Sin inspecciones en el período", 12, C["faint"]))
        mx = max([c for _, c in top] or [1])
        for nombre, c in top:
            fila = QHBoxLayout()
            fila.setSpacing(10)
            lb = L(nombre, 12)
            lb.setFixedWidth(170)
            fila.addWidget(lb)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setRange(0, mx)
            bar.setValue(c)
            bar.setStyleSheet(f"QProgressBar::chunk {{ background: {C['accent2']}; border-radius: 3px; }}")
            fila.addWidget(bar, 1)
            n = L(str(c), 12, C["muted"], True)
            n.setFixedWidth(24)
            n.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            fila.addWidget(n)
            self.destinos.addLayout(fila)


# ─────────────────────────────  Nueva inspección  ──────────────────────────

class TarjetaParametro(QFrame):
    def __init__(self, clave):
        super().__init__()
        self.setObjectName("card")
        self.clave = clave
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 10)
        v.setSpacing(2)
        top = QHBoxLayout()
        nombre, unidad, _, _ = PARAMS[clave]
        top.addWidget(L(nombre.upper(), 11, C["muted"], True))
        top.addStretch(1)
        self.ins = Insignia()
        top.addWidget(self.ins)
        v.addLayout(top)
        fila = QHBoxLayout()
        fila.setSpacing(6)
        self.valor = L("—", 30, C["faint"], True)
        fila.addWidget(self.valor)
        u = L(unidad, 14, C["muted"])
        fila.addWidget(u, 0, Qt.AlignmentFlag.AlignBottom)
        fila.addStretch(1)
        v.addLayout(fila)
        self.sub = L("", 11, C["faint"])
        v.addWidget(self.sub)
        self.barra = BarraRango()
        v.addWidget(self.barra)

    def limpiar(self, mn, mx):
        self.valor.setText("—")
        estilo_label(self.valor, 30, C["faint"], True)
        self.ins.estado(None)
        self.sub.setText(f"Especificación  {val(self.clave, mn)} – {val(self.clave, mx)} {PARAMS[self.clave][1]}")
        self.barra.set(mn, mx, clave=self.clave)

    def mostrar(self, muestras, mn, mx):
        vals = [m["resultados"][self.clave] for m in muestras]
        media = sum(vals) / len(vals)
        est = evaluar(media, mn, mx)
        sd = (sum((x - media) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5 if len(vals) > 1 else 0
        self.valor.setText(val(self.clave, media))
        estilo_label(self.valor, 30, C["text"], True)
        self.ins.estado(est, ESTADOS[est]["param"])
        self.sub.setText(f"Media de {len(vals)} unidades  ·  σ {val(self.clave, sd)}")
        self.barra.set(mn, mx, media, est, [(m["resultados"][self.clave], m["estados"][self.clave]) for m in muestras], self.clave)


class PaginaInspeccion(Pagina):
    titulo = "Nueva inspección"
    subtitulo = "Registrá la carga, escaneá el lote y obtené el dictamen"

    def construir(self):
        self.estado = "libre"          # libre · abierta · cerrada
        self.muestras, self.espectros = [], []
        self.registro = None
        self.escaneando = False
        self.automatico = False
        self._ver = -1
        self.tarjetas = {}
        self.errores = {}

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(16)

        # ── formulario (todo se ingresa a mano)
        izq = QFrame()
        izq.setObjectName("card")
        izq.setFixedWidth(350)
        vi = QVBoxLayout(izq)
        vi.setContentsMargins(0, 18, 0, 18)
        vi.setSpacing(0)
        cab = QHBoxLayout()
        cab.setContentsMargins(22, 0, 22, 8)
        cab.addWidget(L("Datos de la carga", 15, negrita=True))
        cab.addStretch(1)
        self.chip_estado = Chip("BORRADOR", C["muted"])
        cab.addWidget(self.chip_estado)
        vi.addLayout(cab)

        form = QWidget()
        vf = QVBoxLayout(form)
        vf.setContentsMargins(22, 4, 22, 4)
        vf.setSpacing(4)

        def campo(titulo, w):
            vf.addSpacing(8)
            vf.addWidget(L(titulo, 12, C["muted"]))
            vf.addWidget(w)
            err = L("", 11, C["bad"], wrap=True)
            err.hide()
            vf.addWidget(err)
            self.errores[w] = err
            if isinstance(w, QLineEdit):
                w.textChanged.connect(lambda _=None, ww=w: self._quitar_error(ww))
            elif w.isEditable():
                w.editTextChanged.connect(lambda _=None, ww=w: self._quitar_error(ww))
            else:
                w.currentIndexChanged.connect(lambda _=None, ww=w: self._quitar_error(ww))

        self.c_prod = combo()
        llenar_productos(self.c_prod, self.app.datos.productos, vacio=True)
        self.c_prod.currentIndexChanged.connect(self._cambio_producto)
        campo("Producto", self.c_prod)
        info = QHBoxLayout()
        info.setContentsMargins(0, 6, 0, 0)
        self.chip_cat = Chip("")
        info.addWidget(self.chip_cat)
        self.lb_params = L("", 11, C["faint"])
        info.addWidget(self.lb_params, 1)
        vf.addLayout(info)

        ejemplo = "MSCU 482913-" + str(digito_iso6346("MSCU482913"))
        self.e_cont = QLineEdit()
        self.e_cont.setPlaceholderText(f"Ej: {ejemplo}")
        self.e_cont.textEdited.connect(self._mayusculas)
        self.e_cont.editingFinished.connect(self._chequear_contenedor)
        campo("Contenedor", self.e_cont)
        self.e_perm = QLineEdit()
        self.e_perm.setPlaceholderText("Ej: 26 001 EC01 000123 A")
        campo("Permiso de embarque", self.e_perm)
        self.e_exp = combo(editable=True)
        self.e_exp.lineEdit().setPlaceholderText("Razón social del exportador")
        campo("Exportador", self.e_exp)
        self.e_dest = combo(editable=True)
        self.e_dest.lineEdit().setPlaceholderText("Ej: Países Bajos")
        campo("País de destino", self.e_dest)
        self.e_term = combo(editable=True)
        self.e_term.lineEdit().setPlaceholderText("Ej: Puerto de Rosario")
        campo("Terminal / zona primaria", self.e_term)
        self.e_insp = QLineEdit()
        self.e_insp.setPlaceholderText("Nombre y apellido")
        campo("Inspector", self.e_insp)
        vf.addSpacing(8)
        vf.addWidget(L("Unidades a muestrear", 12, C["muted"]))
        self.paso = Paso(3, 30, 10)
        vf.addWidget(self.paso)
        vf.addStretch(1)
        vi.addWidget(scroll(form), 1)

        botones = QVBoxLayout()
        botones.setContentsMargins(22, 12, 22, 0)
        botones.setSpacing(8)
        self.b_prin = boton("", "primario", self._accion_principal)
        self.b_prin.setMinimumHeight(46)
        botones.addWidget(self.b_prin)
        fila = QHBoxLayout()
        fila.setSpacing(8)
        self.b_auto = boton("⏵⏵  Automático", "secundario", self._automatico)
        self.b_sec = boton("", "secundario", self._accion_secundaria)
        fila.addWidget(self.b_auto, 1)
        fila.addWidget(self.b_sec, 1)
        botones.addLayout(fila)
        self.b_cancel = boton("Cancelar inspección", "enlace", self._cancelar)
        self.b_cancel.setStyleSheet(f"color: {C['faint']};")
        botones.addWidget(self.b_cancel, 0, Qt.AlignmentFlag.AlignHCenter)
        vi.addLayout(botones)
        h.addWidget(izq)

        # ── resultados
        der = QVBoxLayout()
        der.setSpacing(14)
        self.banner = QFrame()
        self.banner.setObjectName("card")
        hb = QHBoxLayout(self.banner)
        hb.setContentsMargins(18, 14, 22, 14)
        hb.setSpacing(14)
        self.icono = IconoEstado()
        hb.addWidget(self.icono)
        tb = QVBoxLayout()
        tb.setSpacing(2)
        self.b_tit = L("", 22, negrita=True)
        self.b_sub = L("", 13, C["muted"])
        tb.addWidget(self.b_tit)
        tb.addWidget(self.b_sub)
        hb.addLayout(tb, 1)
        self.b_meta = L("", 12, C["faint"])
        self.b_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hb.addWidget(self.b_meta)
        der.addWidget(self.banner)

        ce, le = tarjeta(18, 6)
        he = encabezado("Respuesta espectral", "absorbancia log(1/R) · unidades previas en gris")
        he.addWidget(L("900 – 1700 nm  ·  Pynect NIR-M-R2", 12, C["faint"]))
        le.addLayout(he)
        self.grafico = GraficoEspectro(170)
        le.addWidget(self.grafico, 1)
        pie = QHBoxLayout()
        self.lb_estado = L("", 12, C["muted"])
        pie.addWidget(self.lb_estado, 1)
        self.progreso = QProgressBar()
        self.progreso.setTextVisible(False)
        self.progreso.setFixedWidth(240)
        self.progreso.setRange(0, 1000)
        pie.addWidget(self.progreso)
        le.addLayout(pie)
        der.addWidget(ce, 1)

        cm, lm = tarjeta(18, 6)
        hm = encabezado("Unidades del lote")
        self.lb_conf = L("", 12, C["muted"])
        hm.addWidget(self.lb_conf)
        lm.addLayout(hm)
        self.g_muestras = GraficoMuestras()
        lm.addWidget(self.g_muestras)
        der.addWidget(cm)

        self.cont_params = QHBoxLayout()
        self.cont_params.setSpacing(14)
        der.addLayout(self.cont_params)
        h.addLayout(der, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.pulso = QTimer(self)
        self.pulso.timeout.connect(self._pulso)
        self.app.escaner.conexion.connect(self._cambio_conexion)
        self._completar()
        self._cambio_producto()

    # ── helpers de formulario
    def producto(self):
        return self.app.datos.producto(self.c_prod.currentData()) if self.c_prod.currentData() else None

    def _campos(self):
        return (self.c_prod, self.e_cont, self.e_perm, self.e_exp, self.e_dest, self.e_term, self.e_insp)

    @staticmethod
    def _txt(w):
        return (w.currentText() if isinstance(w, QComboBox) else w.text()).strip()

    def _mayusculas(self, texto):
        pos = self.e_cont.cursorPosition()
        self.e_cont.setText(texto.upper())
        self.e_cont.setCursorPosition(pos)

    def _chequear_contenedor(self):
        if self.e_cont.text().strip():
            normal, err = validar_contenedor(self.e_cont.text())
            if err:
                self._error(self.e_cont, err)
            else:
                self.e_cont.setText(normal)

    def _error(self, w, mensaje):
        w.setProperty("error", True)
        w.style().unpolish(w)
        w.style().polish(w)
        self.errores[w].setText(mensaje)
        self.errores[w].show()

    def _quitar_error(self, w):
        if w.property("error"):
            w.setProperty("error", False)
            w.style().unpolish(w)
            w.style().polish(w)
        self.errores[w].hide()

    def _completar(self):
        """Sugiere solo valores que el usuario ya ingresó antes (no hay listas fijas)."""
        for w, campo in ((self.e_exp, "exportador"), (self.e_dest, "destino"), (self.e_term, "terminal")):
            texto = w.currentText()
            w.blockSignals(True)
            w.clear()
            w.addItems(self.app.datos.valores(campo))
            w.setCurrentIndex(-1)
            w.setEditText(texto)
            w.blockSignals(False)
        for w, campo in ((self.e_insp, "inspector"),):
            comp = QCompleter(self.app.datos.valores(campo), w)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.popup().setObjectName("completer")
            w.setCompleter(comp)

    def _validar(self):
        ok = True
        if not self.c_prod.currentData():
            self._error(self.c_prod, "Seleccioná el producto a inspeccionar")
            ok = False
        normal, err = validar_contenedor(self.e_cont.text())
        if err:
            self._error(self.e_cont, err)
            ok = False
        else:
            self.e_cont.setText(normal)
        for w, msg, minimo in ((self.e_perm, "Ingresá el permiso de embarque", 6),
                               (self.e_exp, "Ingresá el exportador", 2),
                               (self.e_dest, "Ingresá el país de destino", 2),
                               (self.e_term, "Ingresá la terminal o zona primaria", 2),
                               (self.e_insp, "Ingresá el nombre del inspector", 2)):
            if len(self._txt(w)) < minimo:
                self._error(w, msg)
                ok = False
        if not ok:
            self.app.notificar("Faltan datos de la carga", "limite", "Revisá los campos marcados en rojo")
        return ok

    def _bloquear(self, b):
        for w in self._campos():
            w.setEnabled(not b)
        self.paso.setEnabled(not b)

    def _banner(self, estado, titulo, sub, meta=""):
        self.icono.set(estado)
        col = ESTADOS[estado]["color"] if estado in ESTADOS else C["text"]
        self.b_tit.setText(titulo)
        estilo_label(self.b_tit, 22, col, True)
        self.b_sub.setText(sub)
        self.b_meta.setText(meta)
        borde = mezclar(C["card"], col, 0.55) if estado in ESTADOS else C["border"]
        self.banner.setStyleSheet(f"QFrame#card {{ border: 1px solid {borde}; }}")

    def _botones(self):
        n, plan = len(self.muestras), self.paso.v
        conectado = self.app.escaner.conectado
        if self.estado == "libre":
            self.chip_estado.set("BORRADOR", C["muted"])
            self.b_prin.setText("▶   Iniciar inspección")
            self.b_prin.setEnabled(True)
            self.b_auto.hide()
            self.b_sec.hide()
            self.b_cancel.hide()
        elif self.estado == "abierta":
            self.chip_estado.set("EN CURSO", C["accent2"])
            if conectado:
                self.b_prin.setText(f"◎   Escanear unidad {min(n + 1, plan)} de {plan}")
            else:
                self.b_prin.setText("Conectá el escáner para escanear")
            self.b_auto.show()
            self.b_sec.show()
            self.b_sec.setText("Cerrar lote")
            self.b_cancel.show()
            self.b_prin.setEnabled(conectado and not self.escaneando)
            self.b_auto.setEnabled(conectado and not self.escaneando)
            self.b_sec.setEnabled(not self.escaneando and n >= 3)
            self.b_sec.setToolTip("Se necesitan al menos 3 unidades" if n < 3 else "Cerrar con las unidades escaneadas")
            self.b_cancel.setEnabled(not self.escaneando)
        else:
            e = self.registro["estado"]
            self.chip_estado.set(ESTADOS[e]["lote"].upper(), ESTADOS[e]["color"])
            self.b_prin.setText("＋   Nueva inspección")
            self.b_prin.setEnabled(True)
            self.b_auto.hide()
            self.b_sec.show()
            self.b_sec.setEnabled(True)
            self.b_sec.setText("⤓  Acta PDF")
            self.b_cancel.hide()

    def _cambio_producto(self, *_):
        p = self.producto()
        if p:
            self.chip_cat.set(p["categoria"])
            self.chip_cat.show()
            self.lb_params.setText("  " + " · ".join(PARAMS[k][2] for k in p["params"]) + f"  ·  muestreo sugerido {p['muestras']}")
            self.paso.set(p["muestras"])
        else:
            self.chip_cat.hide()
            self.lb_params.setText("")
        self._ver = self.app.datos.version
        self._reiniciar_vista()

    def _reiniciar_vista(self):
        p = self.producto()
        while self.cont_params.count():
            it = self.cont_params.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.tarjetas = {}
        if p:
            for k, (mn, mx) in p["params"].items():
                t = TarjetaParametro(k)
                t.limpiar(mn, mx)
                self.cont_params.addWidget(t, 1)
                self.tarjetas[k] = t
            sub = f"{p['icono']}  {p['nombre']}  ·  {len(p['params'])} parámetros calibrados  ·  completá los datos de la carga"
        else:
            vacio, lv = tarjeta(18, 4)
            lv.addWidget(L("Parámetros de calidad", 13, C["muted"], True))
            lv.addWidget(L("Seleccioná un producto para ver qué parámetros mide el escáner y sus rangos de aceptación.", 12, C["faint"], wrap=True))
            self.cont_params.addWidget(vacio, 1)
            sub = "Seleccioná el producto e ingresá los datos del contenedor para comenzar"
        self.muestras, self.espectros = [], []
        self.grafico.set(None)
        self.progreso.setValue(0)
        self.g_muestras.set(0, [])
        self.lb_conf.setText("")
        self._banner(None, "Listo para inspeccionar", sub)
        self._texto_estado()
        self._botones()

    def _texto_estado(self):
        if not self.app.escaner.conectado:
            self.lb_estado.setText("Escáner desconectado · conectalo por USB-C para poder escanear")
        elif self.estado == "libre":
            self.lb_estado.setText("Escáner listo · esperando el inicio de la inspección")
        elif self.estado == "abierta" and not self.escaneando:
            self.lb_estado.setText("Escáner listo · apoyá el cabezal sobre la unidad y presioná «Escanear»")

    def refrescar(self, **kw):
        if self.estado == "libre":
            llenar_productos(self.c_prod, self.app.datos.productos, vacio=True)
            self._completar()
            if self._ver != self.app.datos.version:
                self._cambio_producto()

    def _cambio_conexion(self, conectado):
        if not conectado and self.escaneando:
            self.timer.stop()
            self.pulso.stop()
            self.escaneando = False
            self.app.escaneando = False
            self.automatico = False
            self.icono.fase = 0
            self.grafico.set(self.espectros[-1] if self.espectros else None, self.espectros[-7:-1])
            self.progreso.setValue(0)
            self.g_muestras.set(self.paso.v, [peor(m["estados"].values()) for m in self.muestras])
            self._botones()
            self.lb_estado.setText(f"Escaneo de la unidad {len(self.muestras) + 1} interrumpido: se perdió la conexión con el escáner")
            return
        if not conectado:
            self.automatico = False
        self._botones()
        self._texto_estado()

    # ── flujo
    def _accion_principal(self):
        if self.estado == "libre":
            self._iniciar()
        elif self.estado == "abierta":
            self.automatico = False
            self._escanear()
        else:
            # nueva inspección: se limpian producto y datos de la carga
            # (terminal e inspector se mantienen durante la sesión)
            self.estado = "libre"
            self.registro = None
            self._bloquear(False)
            for w in (self.e_cont, self.e_perm):
                w.clear()
            for w in (self.e_exp, self.e_dest):
                w.setEditText("")
            self.c_prod.setCurrentIndex(0)
            self._completar()
            self._cambio_producto()

    def _accion_secundaria(self):
        if self.estado == "abierta":
            self._cerrar()
        elif self.estado == "cerrada" and self.registro:
            self.app.exportar_acta(self.registro)

    def _iniciar(self):
        if not self._validar():
            return
        p = self.producto()
        self.app.escaner.nuevo_lote(p)
        self.estado = "abierta"
        self._bloquear(True)
        self.muestras, self.espectros = [], []
        self.g_muestras.set(self.paso.v, [], 0 if self.app.escaner.conectado else None)
        self._banner("abierta", "Inspección en curso",
                     f"{p['icono']}  {p['nombre']}  ·  {self._txt(self.e_exp)}  →  {self._txt(self.e_dest)}",
                     f"Contenedor {self.e_cont.text()}\n{self._txt(self.e_term)}")
        self._texto_estado()
        self._botones()
        if not self.app.escaner.conectado:
            self.app.notificar("Inspección abierta sin escáner", "info", "Conectá el escáner para empezar a escanear las unidades")

    def _automatico(self):
        self.automatico = True
        self._escanear()

    def _escanear(self):
        if self.escaneando or self.estado != "abierta":
            return
        if not self.app.escaner.conectado:
            self.automatico = False
            self.app.notificar("No se puede escanear", "fuera", "El escáner no está conectado")
            return
        p = self.producto()
        self.escaneando = True
        self.app.escaneando = True
        self._pend = self.app.escaner.medir(p)
        self._t = 0
        self.g_muestras.set(self.paso.v, [peor(m["estados"].values()) for m in self.muestras], len(self.muestras))
        self.lb_estado.setText(f"Unidad {len(self.muestras) + 1}: emitiendo radiación NIR y registrando la respuesta espectral…")
        self.pulso.start(40)
        self.timer.start(30)
        self._botones()

    def _tick(self):
        self._t += 1
        N, M = 32, 12
        if self._t <= N:
            self.progreso.setValue(int(850 * self._t / N))
            self.grafico.set(self._pend[0], self.espectros[-6:], self._t / N)
        elif self._t == N + 1:
            self.lb_estado.setText("Aplicando modelo de calibración PLS y comparando con la especificación…")
        elif self._t <= N + M:
            self.progreso.setValue(850 + int(150 * (self._t - N) / M))
        else:
            self.timer.stop()
            self._registrar_unidad()

    def _pulso(self):
        self.g_muestras.fase += 0.18
        self.icono.fase = (self.icono.fase + 9) % 360
        self.g_muestras.update()
        self.icono.update()

    def _registrar_unidad(self):
        p = self.producto()
        esp, est = self._pend
        estados = {k: evaluar(v, *p["params"][k]) for k, v in est.items()}
        self.muestras.append({"resultados": est, "estados": estados})
        self.espectros.append(esp)
        self.grafico.set(esp, self.espectros[-7:-1], 1.0)
        for k, (mn, mx) in p["params"].items():
            self.tarjetas[k].mostrar(self.muestras, mn, mx)
        _, _, conf, _ = evaluar_lote(self.muestras, p["params"])
        n = len(self.muestras)
        self.lb_conf.setText(f"{n} de {self.paso.v} escaneadas  ·  conformes {conf:.0f} %")
        e = peor(estados.values())
        fuera = [PARAMS[k][0] for k, s in estados.items() if s != "ok"]
        self.lb_estado.setText(f"Unidad {n}: " + (ESTADOS[e]["texto"].lower() if e == "ok" else "atención en " + ", ".join(fuera)))
        self.escaneando = False
        self.app.escaneando = False
        self.app.fin_escaneo()
        self.pulso.stop()
        self.icono.fase = 0
        if n >= self.paso.v:
            self.g_muestras.set(self.paso.v, [peor(m["estados"].values()) for m in self.muestras])
            QTimer.singleShot(350, self._cerrar)
            return
        self.g_muestras.set(self.paso.v, [peor(m["estados"].values()) for m in self.muestras],
                            n if self.automatico else None)
        self._botones()
        if self.automatico:
            QTimer.singleShot(260, self._escanear)

    def _cerrar(self):
        if self.estado != "abierta" or len(self.muestras) < 3 or self.escaneando:
            return
        self.automatico = False
        p = self.producto()
        n = len(self.espectros)
        promedio = [sum(e[i] for e in self.espectros) / n for i in range(len(self.espectros[0]))]
        datos = {"contenedor": self.e_cont.text().strip(), "permiso": self.e_perm.text().strip(),
                 "exportador": self._txt(self.e_exp), "destino": self._txt(self.e_dest),
                 "terminal": self._txt(self.e_term), "inspector": self._txt(self.e_insp)}
        self.registro = r = self.app.datos.registrar(p, datos, self.muestras, [round(x, 4) for x in promedio])
        self.estado = "cerrada"
        e = r["estado"]
        tit, sub = VEREDICTO[e]
        self._banner(e, tit, sub, f"{r['id']}\nContenedor {r['contenedor']}")
        self.g_muestras.set(len(self.muestras), [peor(m["estados"].values()) for m in self.muestras])
        self.lb_estado.setText(f"Inspección cerrada  ·  {len(self.muestras)} unidades  ·  conformes {num(r['conformidad'], 0)} %")
        self.progreso.setValue(1000)
        self._botones()
        self.app.notificar(f"Inspección {r['id']} registrada", e, f"{p['nombre']} · {r['contenedor']} · {ESTADOS[e]['lote']}")

    def _cancelar(self):
        if self.estado != "abierta" or self.escaneando:
            return
        if self.muestras and QMessageBox.question(
                self, "Cancelar inspección",
                f"Se descartarán {len(self.muestras)} unidades escaneadas. ¿Continuar?") != QMessageBox.StandardButton.Yes:
            return
        self.estado = "libre"
        self.automatico = False
        self._bloquear(False)
        self._reiniciar_vista()


# ─────────────────────────────  Historial  ─────────────────────────────────

class Celda(QTableWidgetItem):
    def __init__(self, texto, clave=None, color=None, negrita=False):
        super().__init__(texto)
        self.clave = clave if clave is not None else texto
        if color:
            self.setForeground(qc(color))
        if negrita:
            self.setFont(fuente(13, True))

    def __lt__(self, otro):
        try:
            return self.clave < otro.clave
        except TypeError:
            return str(self.clave) < str(otro.clave)


class PaginaHistorial(Pagina):
    titulo = "Historial de inspecciones"
    subtitulo = "Registro de lotes inspeccionados por contenedor, exportador y destino"
    COLS = ["ID", "Dictamen", "Fecha", "Producto", "Contenedor", "Exportador"]
    FILTRO = {"Todos": None, "Aptos": "ok", "Observados": "limite", "Rechazados": "fuera"}

    def construir(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)
        self.visibles = []

        barra = QFrame()
        barra.setObjectName("card")
        hb = QHBoxLayout(barra)
        hb.setContentsMargins(14, 12, 14, 12)
        hb.setSpacing(10)
        self.e_buscar = QLineEdit()
        self.e_buscar.setPlaceholderText("Buscar ID, contenedor, permiso o exportador")
        self.e_buscar.setMinimumWidth(240)
        self.e_buscar.setClearButtonEnabled(True)
        self.e_buscar.textChanged.connect(lambda _: self.filtrar())
        hb.addWidget(self.e_buscar)
        self.c_prod = combo()
        self.c_prod.setMinimumWidth(210)
        llenar_productos(self.c_prod, self.app.datos.productos, todos=True)
        self.c_prod.currentIndexChanged.connect(lambda _: self.filtrar())
        hb.addWidget(self.c_prod)
        self.s_estado = Segmentado(list(self.FILTRO))
        self.s_estado.cambiado.connect(lambda _: self.filtrar())
        hb.addWidget(self.s_estado)
        hb.addStretch(1)
        hb.addWidget(boton("⤓  Exportar CSV", "secundario", self.exportar))
        v.addWidget(barra)

        cuerpo = QHBoxLayout()
        cuerpo.setSpacing(16)
        ct, lt = tarjeta(10, 6)
        self.lb_n = L("", 12, C["faint"])
        self.lb_n.setContentsMargins(6, 0, 0, 0)
        lt.addWidget(self.lb_n)
        self.tabla = QTableWidget(0, len(self.COLS))
        self.tabla.setHorizontalHeaderLabels(self.COLS)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.verticalHeader().setDefaultSectionSize(40)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.setShowGrid(False)
        self.tabla.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabla.setWordWrap(False)
        hh = self.tabla.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setHighlightSections(False)
        for i, w in enumerate([130, 116, 136, 168, 136, 200]):
            self.tabla.setColumnWidth(i, w)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.tabla.itemSelectionChanged.connect(self._sel)
        lt.addWidget(self.tabla)
        cuerpo.addWidget(ct, 1)

        self.det = QFrame()
        self.det.setObjectName("card")
        self.det.setFixedWidth(340)
        vd = QVBoxLayout(self.det)
        vd.setContentsMargins(0, 0, 0, 0)
        cont = QWidget()
        self.vd = QVBoxLayout(cont)
        self.vd.setContentsMargins(20, 18, 20, 18)
        self.vd.setSpacing(6)
        sa = scroll(cont)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        vd.addWidget(sa)
        cuerpo.addWidget(self.det)
        v.addLayout(cuerpo, 1)

    def refrescar(self, seleccionar=None, **kw):
        llenar_productos(self.c_prod, self.app.datos.productos, todos=True)
        if seleccionar:
            self.e_buscar.blockSignals(True)
            self.e_buscar.clear()
            self.e_buscar.blockSignals(False)
            self.c_prod.setCurrentIndex(0)
            self.s_estado.set("Todos")
        self.filtrar(seleccionar)

    def filtrar(self, seleccionar=None):
        q = self.e_buscar.text().strip().lower()
        prod = self.c_prod.currentData()
        est = self.FILTRO.get(self.s_estado.valor())
        items = [r for r in reversed(self.app.datos.inspecciones)
                 if (not q or any(q in r[c].lower() for c in ("id", "contenedor", "permiso", "exportador")))
                 and (prod in (None, "__todos__") or r["producto"] == prod)
                 and (est is None or r["estado"] == est)]
        previa = seleccionar or self._id_sel()
        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(items))
        for i, r in enumerate(items):
            e = ESTADOS[r["estado"]]
            celdas = [Celda(r["id"], negrita=True),
                      Celda(f"●  {e['lote']}", ORDEN[r["estado"]], e["color"]),
                      Celda(f"{fecha(r):%d/%m/%y  %H:%M}", r["fecha"]),
                      Celda(f"{r['icono']}  {r['producto']}", r["producto"]),
                      Celda(r["contenedor"]), Celda(f"{r['exportador']}  →  {r['destino']}", color=C["muted"])]
            celdas[0].setData(Qt.ItemDataRole.UserRole, r["id"])
            for j, c in enumerate(celdas):
                self.tabla.setItem(i, j, c)
        self.tabla.setSortingEnabled(True)
        self.visibles = {r["id"]: r for r in items}
        self.lb_n.setText(f"{len(items)} inspecciones  ·  clic en un encabezado para ordenar")
        objetivo = previa if previa in self.visibles else (items[0]["id"] if items else None)
        if objetivo:
            for i in range(self.tabla.rowCount()):
                if self.tabla.item(i, 0).data(Qt.ItemDataRole.UserRole) == objetivo:
                    self.tabla.selectRow(i)
                    self.tabla.scrollToItem(self.tabla.item(i, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
                    break
        else:
            self._detalle(None)

    def _id_sel(self):
        filas = self.tabla.selectionModel().selectedRows() if self.tabla.selectionModel() else []
        if filas:
            it = self.tabla.item(filas[0].row(), 0)
            return it.data(Qt.ItemDataRole.UserRole) if it else None
        return None

    def _sel(self):
        self._detalle(self.visibles.get(self._id_sel()))

    def _detalle(self, r):
        PaginaPanel._limpiar(self.vd)
        if not r:
            self.vd.addWidget(L("Seleccioná una inspección", 13, C["faint"]))
            self.vd.addStretch(1)
            return
        top = QHBoxLayout()
        top.addWidget(L(r["id"], 19, negrita=True))
        top.addStretch(1)
        ins = Insignia()
        ins.estado(r["estado"])
        top.addWidget(ins)
        self.vd.addLayout(top)
        self.vd.addWidget(L(f"{r['icono']}  {r['producto']}", 15, C["text"], True))
        self.vd.addSpacing(4)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(5)
        for i, (k, v) in enumerate([("Fecha", f"{fecha(r):%d/%m/%Y  %H:%M}"), ("Contenedor", r["contenedor"]),
                                    ("Permiso", r["permiso"]), ("Exportador", r["exportador"]),
                                    ("Destino", r["destino"]), ("Terminal", r["terminal"]),
                                    ("Inspector", r["inspector"])]):
            g.addWidget(L(k, 12, C["faint"]), i, 0, Qt.AlignmentFlag.AlignTop)
            lb = L(v, 12, wrap=True)
            g.addWidget(lb, i, 1)
        g.setColumnStretch(1, 1)
        self.vd.addLayout(g)
        self.vd.addSpacing(6)
        self.vd.addWidget(separador())
        self.vd.addSpacing(4)
        self.vd.addWidget(L(f"Resultados · media de {len(r['muestras'])} unidades · {num(r['conformidad'], 0)} % conformes", 12, C["muted"], wrap=True))
        for k, (mn, mx) in r["limites"].items():
            e = r["estados_medias"][k]
            f = QFrame()
            f.setObjectName("card2")
            hf = QHBoxLayout(f)
            hf.setContentsMargins(12, 8, 12, 8)
            hf.addWidget(L("●", 12, ESTADOS[e]["color"]))
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(L(PARAMS[k][0], 13))
            col.addWidget(L(f"esp. {val(k, mn)} – {val(k, mx)} {PARAMS[k][1]}", 11, C["faint"]))
            hf.addLayout(col, 1)
            hf.addWidget(L(f"{val(k, r['medias'][k])} {PARAMS[k][1]}", 14, negrita=True))
            self.vd.addWidget(f)
        self.vd.addSpacing(4)
        self.vd.addWidget(L("Espectro promedio del lote", 12, C["muted"]))
        ge = GraficoEspectro(120, compacto=True)
        ge.set(r.get("espectro"))
        self.vd.addWidget(ge)
        self.vd.addSpacing(6)
        self.vd.addWidget(boton("⤓   Acta de inspección PDF", "primario", lambda: self.app.exportar_acta(r)))
        self.vd.addStretch(1)

    def exportar(self):
        items = list(self.visibles.values())
        if not items:
            self.app.notificar("No hay inspecciones para exportar", "limite")
            return
        ruta, _ = QFileDialog.getSaveFileName(self, "Exportar historial",
                                              f"ApexQuant_inspecciones_{dt.date.today():%Y%m%d}.csv", "CSV (*.csv)")
        if not ruta:
            return
        claves = list(PARAMS)
        try:
            with open(ruta, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(["ID", "Fecha", "Producto", "Categoría", "Contenedor", "Permiso de embarque", "Exportador",
                            "Destino", "Terminal", "Inspector", "Unidades", "Conformes (%)", "Dictamen"]
                           + [f"{PARAMS[k][0]} ({PARAMS[k][1]})" for k in claves])
                for r in items:
                    w.writerow([r["id"], f"{fecha(r):%d/%m/%Y %H:%M}", r["producto"], r["categoria"], r["contenedor"],
                                r["permiso"], r["exportador"], r["destino"], r["terminal"], r["inspector"],
                                len(r["muestras"]), num(r["conformidad"], 0), ESTADOS[r["estado"]]["lote"]]
                               + [val(k, r["medias"][k]) if k in r["medias"] else "" for k in claves])
        except OSError as e:
            QMessageBox.critical(self, "ApexQuant", f"No se pudo exportar:\n{e}")
            return
        self.app.notificar(f"{len(items)} inspecciones exportadas a CSV")


# ─────────────────────────────  Tendencias  ────────────────────────────────

class PaginaTendencias(Pagina):
    titulo = "Tendencias y capacidad"
    subtitulo = "Evolución de la calidad por producto y exportador"
    CANT = {"Últimos 20": 20, "Últimos 50": 50, "Todos": None}

    def construir(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)
        barra = QFrame()
        barra.setObjectName("card")
        hb = QHBoxLayout(barra)
        hb.setContentsMargins(16, 12, 16, 12)
        hb.setSpacing(10)
        self.c_prod = combo()
        self.c_prod.setMinimumWidth(220)
        llenar_productos(self.c_prod, self.app.datos.productos)
        self.c_prod.currentIndexChanged.connect(lambda _: self._cambio_producto())
        hb.addWidget(self.c_prod)
        self.s_param = Segmentado(["—"])
        self.s_param.cambiado.connect(lambda _: self.actualizar())
        hb.addWidget(self.s_param)
        self.c_exp = combo(["Todos los exportadores"])
        self.c_exp.setMinimumWidth(220)
        self.c_exp.currentIndexChanged.connect(lambda _: self.actualizar())
        hb.addWidget(self.c_exp)
        hb.addStretch(1)
        v.addWidget(barra)

        cg, lg = tarjeta(18, 6)
        hg = QHBoxLayout()
        self.g_tit = L("", 15, negrita=True)
        hg.addWidget(self.g_tit)
        hg.addStretch(1)
        for t, col in (("especificación", C["ok"]), ("próximo al límite", C["warn"]), ("fuera", C["bad"])):
            hg.addWidget(L("■", 12, col))
            hg.addWidget(L(t, 12, C["faint"]))
            hg.addSpacing(8)
        self.s_cant = Segmentado(list(self.CANT))
        self.s_cant.set("Últimos 50")
        self.s_cant.cambiado.connect(lambda _: self.actualizar())
        hg.addSpacing(8)
        hg.addWidget(self.s_cant)
        lg.addLayout(hg)
        self.grafico = GraficoTendencia(260)
        lg.addWidget(self.grafico, 1)
        v.addWidget(cg, 1)

        fila = QHBoxLayout()
        fila.setSpacing(14)
        self.kpi = {}
        for k, t in (("n", "Lotes"), ("media", "Media"), ("sd", "Desvío estándar"), ("rango", "Mínimo – Máximo"),
                     ("rech", "Lotes rechazados"), ("cpk", "Índice Cpk")):
            self.kpi[k] = Indicador(t)
            fila.addWidget(self.kpi[k], 1)
        v.addLayout(fila)
        self._etq = {}
        self._cambio_producto()

    def _cambio_producto(self, clave=None):
        p = self.app.datos.producto(self.c_prod.currentData())
        self._etq = {PARAMS[k][0]: k for k in p["params"]}
        inicial = PARAMS[clave][0] if clave in p["params"] else list(self._etq)[0]
        self.s_param.opciones(list(self._etq), inicial)
        self.actualizar()

    def refrescar(self, producto=None, clave=None, **kw):
        llenar_productos(self.c_prod, self.app.datos.productos)
        actual = self.c_exp.currentText()
        self.c_exp.blockSignals(True)
        self.c_exp.clear()
        for x in ["Todos los exportadores"] + self.app.datos.valores("exportador"):
            self.c_exp.addItem(x, x)
        i = self.c_exp.findText(actual)
        self.c_exp.setCurrentIndex(max(0, i))
        self.c_exp.blockSignals(False)
        if producto:
            i = self.c_prod.findData(producto)
            if i >= 0:
                self.c_prod.blockSignals(True)
                self.c_prod.setCurrentIndex(i)
                self.c_prod.blockSignals(False)
        self._cambio_producto(clave or self._etq.get(self.s_param.valor()))

    def actualizar(self):
        p = self.app.datos.producto(self.c_prod.currentData())
        k = self._etq.get(self.s_param.valor())
        if not k:
            return
        mn, mx = p["params"][k]
        exp = self.c_exp.currentText()
        rs = [r for r in self.app.datos.inspecciones if r["producto"] == p["nombre"] and k in r["medias"]
              and (exp == "Todos los exportadores" or r["exportador"] == exp)]
        n = self.CANT[self.s_cant.valor()]
        if n:
            rs = rs[-n:]
        pts = [(fecha(r), r["medias"][k], evaluar(r["medias"][k], mn, mx), r["id"],
                f"{ESTADOS[r['estado']]['lote']} · {r['destino']}") for r in rs]
        self.g_tit.setText(f"{p['icono']}  {PARAMS[k][0]} en {p['nombre']}  ·  esp. {val(k, mn)} – {val(k, mx)} {PARAMS[k][1]}")
        self.grafico.set(pts, (mn, mx), k)
        vals = [q[1] for q in pts]
        u = PARAMS[k][1]
        if not vals:
            for x in self.kpi.values():
                x.set("—", "sin datos")
            return
        media = sum(vals) / len(vals)
        sd = (sum((x - media) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5 if len(vals) > 1 else 0.0
        rech = sum(1 for r in rs if r["estado"] == "fuera")
        self.kpi["n"].set(str(len(vals)), "inspecciones del producto")
        self.kpi["media"].set(f"{val(k, media)} {u}", f"nominal {val(k, (mn + mx) / 2)} {u}", ESTADOS[evaluar(media, mn, mx)]["color"])
        self.kpi["sd"].set(val(k, sd), "variación entre lotes")
        self.kpi["rango"].set(f"{val(k, min(vals))} – {val(k, max(vals))}", u)
        self.kpi["rech"].set(f"{100 * rech / len(rs):.0f} %", f"{rech} de {len(rs)} lotes", C["bad"] if rech else C["ok"])
        if sd > 0:
            cpk = min(mx - media, media - mn) / (3 * sd)
            t, col = (("proceso capaz", C["ok"]) if cpk >= 1.33 else
                      (("capacidad marginal", C["warn"]) if cpk >= 1 else ("proceso no capaz", C["bad"])))
            self.kpi["cpk"].set(num(cpk, 2), t, col)
        else:
            self.kpi["cpk"].set("—", "se necesitan 2+ lotes")


# ─────────────────────────────  Productos  ─────────────────────────────────

class PaginaProductos(Pagina):
    titulo = "Productos y especificaciones"
    subtitulo = "Modelos de calibración NIR y rangos de aceptación para exportación"

    def construir(self):
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(16)
        self.actual = self.app.datos.productos[0]["nombre"]

        ci, li = tarjeta(14, 8)
        ci.setFixedWidth(310)
        li.addLayout(encabezado("Catálogo", f"{len(self.app.datos.productos)} productos"))
        self.s_cat = Segmentado(["Todos", "Frutas", "Hortalizas", "Granos"])
        self.s_cat.cambiado.connect(lambda _: self._lista())
        li.addWidget(self.s_cat)
        self.lista = QListWidget()
        self.lista.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lista.currentItemChanged.connect(self._elegir)
        li.addWidget(self.lista, 1)
        h.addWidget(ci)

        ce, le = tarjeta(24, 12)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.lb_icono = L("", 34)
        top.addWidget(self.lb_icono)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.lb_nombre = L("", 22, negrita=True)
        col.addWidget(self.lb_nombre, 0, Qt.AlignmentFlag.AlignLeft)
        self.chip = Chip("")
        col.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignLeft)
        top.addLayout(col, 1)
        le.addLayout(top)
        aviso = QFrame()
        aviso.setStyleSheet(f"background: {mezclar(C['card'], C['warn'], 0.10)}; border-radius: 10px;")
        ha = QHBoxLayout(aviso)
        ha.setContentsMargins(14, 10, 14, 10)
        ha.addWidget(L("!", 15, C["warn"], True))
        ha.addWidget(L("Rangos de referencia ilustrativos para la demostración. Deben ajustarse a los protocolos "
                       "de SENASA y a los requisitos del mercado de destino.", 12, mezclar(C["text"], C["warn"], 0.35), wrap=True), 1)
        le.addWidget(aviso)
        fila = QHBoxLayout()
        fila.addWidget(L("Muestreo por lote", 13, C["muted"]))
        fila.addSpacing(12)
        self.paso = Paso(3, 30, 10)
        fila.addWidget(self.paso)
        fila.addStretch(1)
        le.addLayout(fila)
        le.addWidget(separador())
        self.tabla = QGridLayout()
        self.tabla.setHorizontalSpacing(16)
        self.tabla.setVerticalSpacing(10)
        le.addLayout(self.tabla)
        le.addStretch(1)
        pie = QHBoxLayout()
        self.msg = L("", 12, C["bad"])
        pie.addWidget(self.msg, 1)
        pie.addWidget(boton("Descartar", "secundario", self._mostrar))
        pie.addWidget(boton("Guardar cambios", "primario", self._guardar))
        le.addLayout(pie)
        h.addWidget(ce, 1)
        self._lista()

    def refrescar(self, **kw):
        self._lista()

    def _lista(self):
        cat = self.s_cat.valor()
        mapa = {"Granos": "Granos y oleaginosas"}
        self.lista.blockSignals(True)
        self.lista.clear()
        sel = None
        for p in self.app.datos.productos:
            if cat not in (None, "Todos") and p["categoria"] != mapa.get(cat, cat):
                continue
            it = QListWidgetItem(f"{p['icono']}    {p['nombre']}")
            it.setData(Qt.ItemDataRole.UserRole, p["nombre"])
            it.setToolTip(f"{p['categoria']} · {', '.join(PARAMS[k][0] for k in p['params'])}")
            self.lista.addItem(it)
            if p["nombre"] == self.actual:
                sel = it
        self.lista.blockSignals(False)
        if sel is None and self.lista.count():
            sel = self.lista.item(0)
        if sel:
            self.lista.setCurrentItem(sel)
            self.actual = sel.data(Qt.ItemDataRole.UserRole)
        self._mostrar()

    def _elegir(self, it, _prev=None):
        if it:
            self.actual = it.data(Qt.ItemDataRole.UserRole)
            self._mostrar()

    def _mostrar(self):
        p = self.app.datos.producto(self.actual)
        self.msg.setText("")
        self.lb_icono.setText(p["icono"])
        self.lb_nombre.setText(p["nombre"])
        self.chip.set(p["categoria"])
        self.paso.set(p["muestras"])
        PaginaPanel._limpiar(self.tabla)
        for j, t in enumerate(("Parámetro", "Mínimo", "Máximo", "Vista del rango de aceptación")):
            self.tabla.addWidget(L(t, 12, C["faint"]), 0, j)
        self.filas = {}
        for i, (k, (mn, mx)) in enumerate(p["params"].items(), start=1):
            nombre, unidad, _, dec = PARAMS[k]
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(L(nombre, 14, negrita=True))
            col.addWidget(L(unidad, 11, C["faint"]))
            self.tabla.addLayout(col, i, 0)
            cajas = []
            for j, v in ((1, mn), (2, mx)):
                sb = QDoubleSpinBox()
                sb.setDecimals(dec if dec > 1 else 2)
                sb.setRange(0, 100)
                sb.setSingleStep(0.1)
                sb.setValue(v)
                sb.setFixedSize(120, 38)
                sb.lineEdit().setTextMargins(10, 0, 0, 0)
                sb.setSuffix(f"  {unidad}")
                self.tabla.addWidget(sb, i, j)
                cajas.append(sb)
            barra = BarraRango()
            barra.set(mn, mx, (mn + mx) / 2, "ok", clave=k)
            self.tabla.addWidget(barra, i, 3)
            for sb in cajas:
                sb.valueChanged.connect(lambda _, kk=k: self._previa(kk))
            self.filas[k] = (cajas[0], cajas[1], barra)
        self.tabla.setColumnStretch(3, 1)

    def _previa(self, k):
        a, b, barra = self.filas[k]
        if a.value() < b.value():
            barra.set(a.value(), b.value(), (a.value() + b.value()) / 2, "ok", clave=k)

    def _guardar(self):
        p = self.app.datos.producto(self.actual)
        nuevos = {}
        for k, (a, b, _) in self.filas.items():
            if a.value() >= b.value():
                self.msg.setText(f"{PARAMS[k][0]}: el mínimo debe ser menor que el máximo")
                return
            nuevos[k] = [round(a.value(), 3), round(b.value(), 3)]
        p["params"] = nuevos
        p["muestras"] = self.paso.v
        self.app.datos.version += 1
        self.app.datos.guardar()
        self.app.notificar(f"Especificación de {p['nombre']} actualizada")


# ─────────────────────────────  Dispositivo  ───────────────────────────────

class PaginaDispositivo(Pagina):
    titulo = "Dispositivo"
    subtitulo = "Estado del escáner ApexQuant, conexión, simulación y datos"
    LEDS = [("bateria", "LED de batería", "Verde: cargado  ·  Rojo: batería baja  ·  Amarillo intermitente: cargando"),
            ("escaneo", "LED de escaneo", "Encendido: mantener el escáner quieto  ·  Parpadea 3 s al finalizar"),
            ("hdmi", "LED HDMI", "Se enciende al detectar una pantalla externa conectada")]
    HARDWARE = [("Módulo espectral NIR", "Pynect NIR-M-R2 · emite en la banda NIR y captura la respuesta reflejada"),
                ("Microcontrolador", "ESP32-S3 DevKitC-1 · doble núcleo, aceleración IA, Wi-Fi y BLE 5"),
                ("Gestión de batería", "TP4056 + DW01A + FS8205A · carga por USB-C con protección"),
                ("Regulación de tensión", "LM2596 · convertidor DC-DC reductor"),
                ("Pulsadores", "3 × Metzler IP67 / IK10 · antivandálicos y sellados contra polvo y humedad"),
                ("Puertos", "HDMI (resultados en pantalla externa) · USB-C (datos y carga)")]

    def construir(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        cont = QWidget()
        g = QGridLayout(cont)
        g.setContentsMargins(0, 0, 8, 0)
        g.setSpacing(16)
        v.addWidget(scroll(cont))
        esc = self.app.escaner

        ce, le = tarjeta()
        le.addLayout(encabezado("Escáner ApexQuant", f"AQ-1  ·  firmware {Escaner.FIRMWARE}"))
        fb = QHBoxLayout()
        self.lb_bat = L("", 34, negrita=True)
        fb.addWidget(self.lb_bat)
        self.lb_bat2 = L("", 12, C["muted"])
        fb.addWidget(self.lb_bat2, 0, Qt.AlignmentFlag.AlignBottom)
        fb.addStretch(1)
        le.addLayout(fb)
        self.bar_bat = QProgressBar()
        self.bar_bat.setTextVisible(False)
        self.bar_bat.setRange(0, 100)
        le.addWidget(self.bar_bat)
        le.addSpacing(6)
        self.leds = {}
        for k, t, d in self.LEDS:
            f = QFrame()
            f.setObjectName("card2")
            hf = QHBoxLayout(f)
            hf.setContentsMargins(12, 9, 12, 9)
            hf.setSpacing(12)
            self.leds[k] = Led()
            hf.addWidget(self.leds[k])
            col = QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(L(t, 13, negrita=True))
            col.addWidget(L(d, 11, C["faint"]))
            hf.addLayout(col, 1)
            le.addWidget(f)
        g.addWidget(ce, 0, 0)

        cc, lc = tarjeta()
        lc.addLayout(encabezado("Conexión"))
        r1 = QRadioButton("Simulador integrado")
        r1.setChecked(True)
        lc.addWidget(r1)
        r2 = QRadioButton("USB-C serie  ·  ESP32-S3")
        r2.setEnabled(False)
        lc.addWidget(r2)
        r3 = QRadioButton("Bluetooth LE")
        r3.setEnabled(False)
        lc.addWidget(r3)
        lc.addWidget(Chip("Próximamente: lectura del hardware real", C["accent2"]))
        lc.addSpacing(6)
        lc.addWidget(separador())
        lc.addSpacing(6)
        self.sw_con = Interruptor("Escáner conectado (USB-C)")
        self.sw_con.clicked.connect(lambda _=False: esc.alternar_conexion())
        lc.addWidget(self.sw_con)
        self.sw_hdmi = Interruptor("Pantalla externa conectada (HDMI)")
        self.sw_hdmi.clicked.connect(lambda _=False: esc.alternar_hdmi())
        lc.addWidget(self.sw_hdmi)
        self.sw_carga = Interruptor("Cargador USB-C conectado")
        self.sw_carga.clicked.connect(lambda _=False: esc.alternar_cargador())
        lc.addWidget(self.sw_carga)
        lc.addStretch(1)
        g.addWidget(cc, 0, 1)

        cs, ls = tarjeta()
        ls.addLayout(encabezado("Simulación de lotes"))
        ls.addWidget(L("Controla cuánto se aleja la calidad de cada lote de la especificación. "
                       "Subilo para ver más lotes observados o rechazados.", 12, C["faint"], wrap=True))
        fs = QHBoxLayout()
        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, 100)
        self.sl.setValue(int(esc.motor.variabilidad * 100))
        self.sl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sl.valueChanged.connect(self._var)
        fs.addWidget(self.sl, 1)
        self.lb_var = L("", 13, negrita=True)
        self.lb_var.setFixedWidth(130)
        fs.addWidget(self.lb_var)
        ls.addLayout(fs)
        ls.addStretch(1)
        self._var(self.sl.value())
        g.addWidget(cs, 1, 0)

        cp, lp = tarjeta()
        lp.addLayout(encabezado("Datos y privacidad"))
        lp.addWidget(L("Las inspecciones, contenedores y datos de exportadores se guardan solo en este equipo. "
                       "Nada se envía a servidores externos.", 12, C["faint"], wrap=True))
        ruta = str(RUTA_DATOS)
        lr = L(("…" + ruta[-58:]) if len(ruta) > 60 else ruta, 11, C["muted"])
        lr.setStyleSheet(lr.styleSheet() + f"background: {C['card2']}; border-radius: 8px; padding: 6px 10px;")
        lp.addWidget(lr)
        fb2 = QHBoxLayout()
        fb2.addWidget(boton("Cargar datos demo", "secundario", self.app.cargar_demo))
        fb2.addWidget(boton("Borrar historial", "peligro", self._borrar))
        fb2.addStretch(1)
        lp.addLayout(fb2)
        lp.addStretch(1)
        g.addWidget(cp, 1, 1)

        chw, lh = tarjeta()
        lh.addLayout(encabezado("Hardware del cabezal", "según el modelo CAD (Fusion 360)"))
        gh = QGridLayout()
        gh.setSpacing(10)
        for i, (t, d) in enumerate(self.HARDWARE):
            f = QFrame()
            f.setObjectName("card2")
            vf = QVBoxLayout(f)
            vf.setContentsMargins(14, 10, 14, 10)
            vf.setSpacing(2)
            vf.addWidget(L(t, 13, negrita=True))
            vf.addWidget(L(d, 11, C["faint"], wrap=True))
            gh.addWidget(f, i // 3, i % 3)
        lh.addLayout(gh)
        g.addWidget(chw, 2, 0, 1, 2)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        g.setRowStretch(3, 1)

    def _var(self, v):
        self.app.escaner.motor.variabilidad = v / 100
        self.lb_var.setText(f"{v} %  ·  " + ("baja" if v < 30 else "media" if v < 65 else "alta"))

    def _borrar(self):
        if QMessageBox.question(self, "ApexQuant", "¿Borrar todas las inspecciones?\nEsta acción no se puede deshacer.") == QMessageBox.StandardButton.Yes:
            self.app.datos.inspecciones = []
            self.app.datos.guardar()
            self.app.notificar("Historial borrado", "limite")

    def refrescar(self, **kw):
        self.sl.blockSignals(True)
        self.sl.setValue(int(self.app.escaner.motor.variabilidad * 100))
        self.sl.blockSignals(False)
        self._var(self.sl.value())
        self.actualizar(self.app.leds())

    def actualizar(self, leds):
        e = self.app.escaner
        for sw, valor in ((self.sw_con, e.conectado), (self.sw_hdmi, e.pantalla), (self.sw_carga, e.cargando)):
            if sw.isChecked() != valor:
                sw.setChecked(valor)
                sw.update()
        if e.conectado:
            b = e.nivel
            col = C["bad"] if b < 20 else (C["warn"] if b < 40 else C["ok"])
            self.lb_bat.setText(f"{b:.0f} %")
            estilo_label(self.lb_bat, 34, C["text"], True)
            self.lb_bat2.setText("  ⚡ enchufado · cargando por USB-C" if e.cargando else "  desenchufado · batería, uso autónomo")
            self.bar_bat.setValue(int(b))
            self.bar_bat.setStyleSheet(f"QProgressBar::chunk {{ background: {col}; border-radius: 3px; }}")
        else:
            self.lb_bat.setText("— %")
            estilo_label(self.lb_bat, 34, C["faint"], True)
            self.lb_bat2.setText("  escáner desconectado · sin datos de batería")
            self.bar_bat.setValue(0)
        for k, led in self.leds.items():
            led.set(leds.get(k))


# ═══════════════════════════════════════════════════════════════════════════
#  NOTIFICACIONES Y CONTROLES OCULTOS
# ═══════════════════════════════════════════════════════════════════════════

class LogoSecreto(Logo):
    """El logo de la barra lateral: doble clic abre los controles ocultos."""
    doble = Signal()

    def mouseDoubleClickEvent(self, e):
        self.doble.emit()


class ChipSecreto(Chip):
    """Chip de la barra superior; el doble clic es un control oculto."""
    doble = Signal()

    def mouseDoubleClickEvent(self, e):
        self.doble.emit()


COLOR_AVISO = {"ok": C["ok"], "limite": C["warn"], "warn": C["warn"], "fuera": C["bad"], "bad": C["bad"],
               "info": C["accent2"]}


class Aviso(QFrame):
    def __init__(self, padre):
        super().__init__(padre)
        self.setStyleSheet(f"QFrame {{ background: {C['card2']}; border: 1px solid {C['border']}; border-radius: 12px; }}"
                           f"QLabel {{ border: none; }}")
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 11, 18, 11)
        h.setSpacing(10)
        self.punto = L("●", 13)
        self.txt = L("", 13)
        h.addWidget(self.punto)
        h.addWidget(self.txt)
        self.ef = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.ef)
        self.anim = QPropertyAnimation(self.ef, b"opacity", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.reloj = QTimer(self)
        self.reloj.setSingleShot(True)
        self.reloj.timeout.connect(self._ocultar)
        self.hide()

    def mostrar(self, texto, color):
        self.txt.setText(texto)
        estilo_label(self.punto, 13, color)
        self.adjustSize()
        p = self.parentWidget()
        self.move(p.width() - self.width() - 28, p.height() - self.height() - 28)
        self.show()
        self.raise_()
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        self.reloj.start(3600)

    def _ocultar(self):
        self.anim.stop()
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.start()
        QTimer.singleShot(240, self.hide)


class PanelOculto(QFrame):
    """Controles de demostración: no aparecen en la interfaz normal.

    Se abren con Ctrl+Shift+D o con doble clic en el logo. También funcionan los
    atajos F2–F7 aunque el panel esté cerrado.
    """

    def __init__(self, ventana):
        super().__init__(ventana)
        self.v = ventana
        esc = ventana.escaner
        self.setObjectName("oculto")
        self.setStyleSheet(f"QFrame#oculto {{ background: {C['card']}; border: 1px solid {mezclar(C['card'], C['accent2'], 0.6)};"
                           f" border-radius: 14px; }}")
        self.setFixedWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(L("Controles ocultos · demo", 14, C["accent2"], True))
        top.addStretch(1)
        x = boton("×", "enlace", self.hide)
        x.setStyleSheet(f"color: {C['muted']}; font-size: 18px;")
        top.addWidget(x)
        lay.addLayout(top)
        lay.addWidget(L("Simulan los eventos que en el equipo real llegan por USB-C. Ctrl+Shift+D o doble clic en el logo "
                        "para mostrar u ocultar este panel.", 11, C["faint"], wrap=True))
        g = QGridLayout()
        g.setSpacing(6)
        acciones = [("⇄  Conexión", "F2", esc.alternar_conexion),
                    ("⚡  Cargador", "F3", esc.alternar_cargador),
                    ("Batería −10 %", "F4", lambda: esc.ajustar(-10)),
                    ("Batería +10 %", "F5", lambda: esc.ajustar(10)),
                    ("Batería baja", "F6", lambda: esc.fijar(15)),
                    ("▭  HDMI", "F7", esc.alternar_hdmi)]
        for i, (t, tecla, f) in enumerate(acciones):
            b = boton(f"{t}   {tecla}", "secundario", f)
            b.setStyleSheet("padding: 7px 8px; font-size: 12px;")
            g.addWidget(b, i // 2, i % 2)
        lay.addLayout(g)
        lay.addSpacing(4)
        lay.addWidget(L("Variabilidad de los lotes simulados", 12, C["muted"]))
        fila = QHBoxLayout()
        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, 100)
        self.sl.setValue(int(esc.motor.variabilidad * 100))
        self.sl.valueChanged.connect(self._var)
        fila.addWidget(self.sl, 1)
        self.lb_var = L("", 12, negrita=True)
        self.lb_var.setFixedWidth(96)
        fila.addWidget(self.lb_var)
        lay.addLayout(fila)
        self._var(self.sl.value())
        lay.addWidget(boton("Cargar datos de demostración", "secundario", ventana.cargar_demo))
        self.hide()

    def _var(self, v):
        self.v.escaner.motor.variabilidad = v / 100
        self.lb_var.setText(f"{v} % · " + ("baja" if v < 30 else "media" if v < 65 else "alta"))

    def alternar(self):
        if self.isVisible():
            self.hide()
            return
        self.adjustSize()
        self.move(256, self.v.height() - self.height() - 24)
        self.show()
        self.raise_()


# ═══════════════════════════════════════════════════════════════════════════
#  VENTANA PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════

class Ventana(QMainWindow):
    NAV = [("panel", "▦", "Panel"), ("inspeccion", "◎", "Nueva inspección"), ("historial", "☰", "Historial"),
           ("tendencias", "↗", "Tendencias"), ("productos", "◈", "Productos"), ("dispositivo", "⚙", "Dispositivo")]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ApexQuant Scanner · Inspección NIR de exportaciones")
        self.setMinimumSize(1280, 760)
        pantalla = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1480, int(pantalla.width() * 0.96)), min(920, int(pantalla.height() * 0.94)))
        self.escaner = Escaner()
        self.datos = Almacen(RUTA_DATOS)
        self.escaneando = False
        self._fin_escaneo = 0.0
        self._fase = False
        self.actual = None

        raiz = QFrame()
        raiz.setObjectName("raiz")
        self.setCentralWidget(raiz)
        h = QHBoxLayout(raiz)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._lateral())
        principal = QWidget()
        vp = QVBoxLayout(principal)
        vp.setContentsMargins(28, 20, 28, 24)
        vp.setSpacing(18)
        vp.addLayout(self._superior())
        self.pila = QStackedWidget()
        vp.addWidget(self.pila, 1)
        h.addWidget(principal, 1)

        clases = {"panel": PaginaPanel, "inspeccion": PaginaInspeccion, "historial": PaginaHistorial,
                  "tendencias": PaginaTendencias, "productos": PaginaProductos, "dispositivo": PaginaDispositivo}
        self.paginas = {}
        for k, c in clases.items():
            self.paginas[k] = c(self)
            self.pila.addWidget(self.paginas[k])

        self.aviso = Aviso(self)
        self.oculto = PanelOculto(self)

        # señales del escáner → interfaz
        self.escaner.aviso.connect(lambda t, d, tipo, ic: self.notificar(t, tipo, d, ic))
        self.escaner.conexion.connect(lambda _: self._estado_escaner())
        self.escaner.cargador.connect(lambda _: self._estado_escaner())
        self.escaner.bateria.connect(lambda _: self._estado_escaner())
        self.escaner.hdmi.connect(lambda _: self._estado_escaner())

        # controles ocultos: atajos de teclado y dobles clics
        for tecla, f in (("Ctrl+Shift+D", self.oculto.alternar), ("F2", self.escaner.alternar_conexion),
                         ("F3", self.escaner.alternar_cargador), ("F4", lambda: self.escaner.ajustar(-10)),
                         ("F5", lambda: self.escaner.ajustar(10)), ("F6", lambda: self.escaner.fijar(15)),
                         ("F7", self.escaner.alternar_hdmi)):
            atajo = QShortcut(QKeySequence(tecla), self)
            atajo.setContext(Qt.ShortcutContext.ApplicationShortcut)
            atajo.activated.connect(f)
        self.logo.doble.connect(self.oculto.alternar)
        self.chip_con.doble.connect(self.escaner.alternar_conexion)
        self.chip_bat.doble.connect(self.escaner.alternar_cargador)

        self.mostrar("panel")
        self._estado_escaner()

        self.t_reloj = QTimer(self)
        self.t_reloj.timeout.connect(self._reloj)
        self.t_reloj.start(1000)
        self._reloj()
        self.t_led = QTimer(self)
        self.t_led.timeout.connect(self._parpadeo)
        self.t_led.start(450)
        QTimer.singleShot(700, lambda: self.notificar(
            "Esperando conexión del escáner", "info", "Conectá el ApexQuant AQ-1 por USB-C para comenzar a escanear", "⇄"))

    def _lateral(self):
        f = QFrame()
        f.setObjectName("lateral")
        f.setFixedWidth(244)
        v = QVBoxLayout(f)
        v.setContentsMargins(16, 24, 16, 18)
        v.setSpacing(4)
        logo = QHBoxLayout()
        logo.setContentsMargins(6, 0, 0, 0)
        logo.setSpacing(12)
        self.logo = LogoSecreto()
        logo.addWidget(self.logo)
        t = QVBoxLayout()
        t.setSpacing(0)
        t.addWidget(L("ApexQuant", 18, negrita=True))
        t.addWidget(L("NIR · EXPORTACIONES", 10, C["accent"], True))
        logo.addLayout(t, 1)
        v.addLayout(logo)
        v.addSpacing(26)
        m = L("MENÚ", 10, C["faint"], True)
        m.setContentsMargins(10, 0, 0, 4)
        v.addWidget(m)
        self.grupo = QButtonGroup(self)
        self.nav = {}
        for clave, icono, texto in self.NAV:
            b = QPushButton(f"{icono}     {texto}")
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=clave: self.mostrar(c))
            self.grupo.addButton(b)
            self.nav[clave] = b
            v.addWidget(b)
        v.addStretch(1)

        mini = QFrame()
        mini.setObjectName("card")
        vm = QVBoxLayout(mini)
        vm.setContentsMargins(14, 12, 14, 12)
        vm.setSpacing(4)
        fm = QHBoxLayout()
        self.mini_dot = L("●", 12, C["warn"])
        fm.addWidget(self.mini_dot)
        fm.addWidget(L("Escáner AQ-1", 13, negrita=True))
        fm.addStretch(1)
        vm.addLayout(fm)
        self.mini_txt = L("", 11, C["faint"])
        vm.addWidget(self.mini_txt)
        fb = QHBoxLayout()
        self.mini_bar = QProgressBar()
        self.mini_bar.setTextVisible(False)
        self.mini_bar.setRange(0, 100)
        fb.addWidget(self.mini_bar, 1)
        self.mini_pct = L("", 11, C["muted"])
        fb.addWidget(self.mini_pct)
        vm.addLayout(fb)
        v.addWidget(mini)
        v.addSpacing(8)
        pie = L("UCP · Sistemas de Representación 2026", 10, C["faint"])
        pie.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(pie)
        return f

    def _superior(self):
        h = QHBoxLayout()
        t = QVBoxLayout()
        t.setSpacing(2)
        self.h_tit = L("", 24, negrita=True)
        self.h_sub = L("", 13, C["muted"], wrap=True)
        t.addWidget(self.h_tit)
        t.addWidget(self.h_sub)
        h.addLayout(t, 1)
        h.addWidget(Chip("⚓  ZONA PRIMARIA ADUANERA", C["accent2"], C["bg"]))
        h.addSpacing(6)
        self.chip_con = ChipSecreto("", C["warn"], C["bg"])
        h.addWidget(self.chip_con)
        h.addSpacing(6)
        self.chip_bat = ChipSecreto("", C["faint"], C["bg"])
        h.addWidget(self.chip_bat)
        h.addSpacing(14)
        self.lb_reloj = L("", 13, C["muted"])
        h.addWidget(self.lb_reloj)
        return h

    def mostrar(self, pagina, **kw):
        pag = self.paginas[pagina]
        self.pila.setCurrentWidget(pag)
        self.nav[pagina].setChecked(True)
        self.h_tit.setText(pag.titulo)
        self.h_sub.setText(pag.subtitulo)
        self.actual = pagina
        pag.refrescar(**kw)

    def notificar(self, titulo, tipo="ok", detalle="", icono=None):
        texto = f"{titulo}  ·  {detalle}" if detalle else titulo
        self.aviso.mostrar(texto, COLOR_AVISO.get(tipo, C["accent2"]))

    def cargar_demo(self):
        if self.datos.inspecciones and QMessageBox.question(
                self, "ApexQuant", "Esto reemplaza el historial actual por inspecciones de demostración. ¿Continuar?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.datos.sembrar(self.escaner.motor)
        self.datos.guardar()
        self.paginas[self.actual].refrescar()
        self.notificar("Datos de demostración cargados", "info", f"{len(self.datos.inspecciones)} inspecciones de ejemplo")

    def exportar_acta(self, r):
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar acta de inspección", f"Acta_{r['id']}.pdf", "PDF (*.pdf)")
        if not ruta:
            return
        try:
            generar_acta(r, ruta)
        except Exception as e:
            QMessageBox.critical(self, "ApexQuant", f"No se pudo generar el acta:\n{e}")
            return
        self.notificar(f"Acta {r['id']} guardada en PDF")
        QDesktopServices.openUrl(QUrl.fromLocalFile(ruta))

    def fin_escaneo(self):
        self._fin_escaneo = dt.datetime.now().timestamp() + 3

    def leds(self):
        e = self.escaner
        if not e.conectado:
            return {"bateria": None, "escaneo": None, "hdmi": None}
        if e.cargando:
            bat = C["warn"] if self._fase else None
        else:
            bat = C["bad"] if e.nivel < 20 else C["ok"]
        if self.escaneando:
            esc = C["accent2"]
        elif dt.datetime.now().timestamp() < self._fin_escaneo:
            esc = C["accent2"] if self._fase else None
        else:
            esc = None
        return {"bateria": bat, "escaneo": esc, "hdmi": C["accent"] if e.pantalla else None}

    def _estado_escaner(self):
        e = self.escaner
        if e.conectado:
            self.chip_con.set("●  ESCÁNER CONECTADO", C["ok"], C["bg"])
            b = e.nivel
            col = C["bad"] if b < 20 else (C["warn"] if b < 40 else C["ok"])
            self.chip_bat.set(f"▮  {b:.0f} %  ·  " + ("⚡ ENCHUFADO" if e.cargando else "DESENCHUFADO"), col, C["bg"])
        else:
            self.chip_con.set("○  ESCÁNER DESCONECTADO", C["warn"], C["bg"])
            self.chip_bat.set("▮  — %  ·  SIN DATOS", C["faint"], C["bg"])
        if e.conectado:
            b = e.nivel
            self.mini_bar.setValue(int(b))
            self.mini_bar.setStyleSheet(f"QProgressBar::chunk {{ background: {C['bad'] if b < 20 else C['warn'] if b < 40 else C['accent']}; border-radius: 3px; }}")
            self.mini_pct.setText(f"{b:.0f} %" + (" ⚡" if e.cargando else ""))
        else:
            self.mini_bar.setValue(0)
            self.mini_pct.setText("—")
        if self.actual == "dispositivo":
            self.paginas["dispositivo"].actualizar(self.leds())

    def _parpadeo(self):
        self._fase = not self._fase
        e = self.escaner
        if not e.conectado:
            estilo_label(self.mini_dot, 12, C["warn"] if self._fase else C["card2"])
            self.mini_txt.setText("Sin conexión")
            self.chip_con.set(("○" if self._fase else "●") + "  ESCÁNER DESCONECTADO", C["warn"], C["bg"])
        elif self.escaneando:
            estilo_label(self.mini_dot, 12, C["accent2"] if self._fase else C["card2"])
            self.mini_txt.setText("Escaneando unidad…")
        else:
            estilo_label(self.mini_dot, 12, C["ok"])
            self.mini_txt.setText(f"Conectado · {e.INTERFAZ}")
        if self.actual == "dispositivo":
            self.paginas["dispositivo"].actualizar(self.leds())

    def _reloj(self):
        ahora = dt.datetime.now()
        self.lb_reloj.setText(f"{DIAS[ahora.weekday()][:3].lower()} {ahora:%d/%m}   {ahora:%H:%M}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "aviso") and self.aviso.isVisible():
            self.aviso.move(self.width() - self.aviso.width() - 28, self.height() - self.aviso.height() - 28)
        if hasattr(self, "oculto") and self.oculto.isVisible():
            self.oculto.move(256, self.height() - self.oculto.height() - 24)

    def closeEvent(self, e):
        self.datos.guardar()
        super().closeEvent(e)


def main():
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QLocale.setDefault(QLocale(QLocale.Language.Spanish, QLocale.Country.Argentina))
    app = QApplication(sys.argv)
    app.setApplicationName("ApexQuant Scanner")
    app.setStyle("Fusion")
    pal = QPalette()
    for rol, col in ((QPalette.ColorRole.Window, C["bg"]), (QPalette.ColorRole.Base, C["card2"]),
                     (QPalette.ColorRole.AlternateBase, C["card"]), (QPalette.ColorRole.Text, C["text"]),
                     (QPalette.ColorRole.WindowText, C["text"]), (QPalette.ColorRole.Button, C["card2"]),
                     (QPalette.ColorRole.ButtonText, C["text"]), (QPalette.ColorRole.Highlight, C["accent"]),
                     (QPalette.ColorRole.HighlightedText, "#04201C"), (QPalette.ColorRole.ToolTipBase, C["card2"]),
                     (QPalette.ColorRole.ToolTipText, C["text"]), (QPalette.ColorRole.PlaceholderText, C["faint"])):
        pal.setColor(rol, QColor(col))
    app.setPalette(pal)
    app.setFont(QFont(FAMILIA, 10))
    app.setStyleSheet(hoja_estilo(crear_iconos()))
    v = Ventana()
    v.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
