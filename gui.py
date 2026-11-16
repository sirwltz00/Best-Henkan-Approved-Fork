#!/usr/bin/env python3
"""Henkan 変換 GUI: pick an UTAU bank, convert it, build the singer DB with the DBTool, test it in the
VOCALOID4 Editor for Developer, pack and install it, or backport a V3/V4 bank to VOCALOID2. Every step
runs the command line tools, so the log shows exactly what the CLI would print.

    python gui.py
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from system_requirements import collect_system_info, evaluate_system, overall_status
from types import SimpleNamespace

import customtkinter as ctk

FROZEN = bool(getattr(sys, 'frozen', False))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else os.path.dirname(os.path.abspath(__file__))
# PyInstaller keeps bundled modules and resources in _MEIPASS. Keep that runtime
# directory separate from the folder containing Henkan.exe and HenkanWorker.exe.
ROOT = getattr(sys, '_MEIPASS', APP_DIR) if FROZEN else APP_DIR


def _default_devkit_dir():
    if not FROZEN:
        return os.path.dirname(ROOT)
    candidates = (APP_DIR, os.path.dirname(APP_DIR))
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, 'VocaloidDBTool3.exe')):
            return candidate
    return APP_DIR


DEVKIT = _default_devkit_dir()
CLI = os.path.join(ROOT, 'henkan.py')   # script identity; frozen builds dispatch through HenkanWorker.exe
_APPDATA = os.environ.get('APPDATA', os.path.expanduser('~'))
SETTINGS = os.path.join(_APPDATA, 'Henkan', 'gui.json')
OLD_SETTINGS = os.path.join(_APPDATA, 'utau2vocaloid', 'gui.json')     # read once if SETTINGS isn't there yet
sys.path.insert(0, ROOT)


SAVED = ('bank', 'out', 'dict', 'lang', 'colors', 'keep', 'splice', 'fill', 'normalize', 'derive', 'extend_dict',
         'extras', 'evec_map', 'evec_phonemes', 'custom_extras', 'evec_vowels', 'v_mode', 'triphones',
         'merge_groups', 'brighten', 'avoid', 'db_name', 'db_path', 'db_lang', 'dbtool', 'dbtool_hidden',
         'pkg_db', 'pkg_name', 'pkg_compid', 'pkg_out', 'pkg_editor', 'pkg_dest', 'pkg_devini', 'growl_wav',
         'pkg_breathiness', 'pkg_brightness', 'pkg_clearness', 'pkg_gender_factor',
          'v3v2_name', 'v3v2_lang', 'pitch_assignments', 'sections', 'geometry', 'tab')

V3V2_BROWSE_FOLDER = 'Browse for a voicebank folder…'
V3V2_BROWSE_TREE = 'Browse for a developer-kit .tree…'
V3V2_PACK_DB = "Use the Pack & Install page's built DB (.tree)…"

# ヴ choices: the label's first word is the --v-mode value
V_MODE_LABELS = ('auto — v when the bank has ヴ', "v — own phonemes v, v'", "b — sing it as b, b'")

UI_FONT, UI_FONT_BOLD = 'Segoe UI', 'Segoe UI Semibold'
INK, INK_SOFT = '#1d2230', '#a7adbd'          # header band
ACCENT = '#d6336c'
HINT, INFO = '#9aa4b2', '#d4dbe5'

# CustomTkinter theme. Keep the familiar Henkan pink as the single accent;
# the application remains named simply "Henkan" and is not presented as a product.
APP_BG = '#11151d'
PANEL_BG = '#191f2a'
CARD_BG = '#202938'
FIELD_BG = '#111925'
BORDER = '#303c4d'
TEXT = '#edf1f7'
TEXT_MUTED = '#9aa7b8'
ACCENT_HOVER = '#b92359'
GOOD = '#42b983'
WARN = '#e6b35a'
BAD = '#ef7272'


def _char_width(value, minimum=32):
    """Translate ttk's character-based widths into CustomTkinter pixels."""
    if value is None:
        return None
    try:
        return max(minimum, int(value * 9 + 18))
    except (TypeError, ValueError):
        return value


def _padding_sides(value):
    """Normalize ttk's scalar/2-side/4-side padding into left, top, right, bottom."""
    if value is None:
        return 0, 0, 0, 0
    if isinstance(value, (tuple, list)):
        if len(value) == 4:
            return tuple(value)
        if len(value) == 2:
            return value[0], value[1], value[0], value[1]
        if len(value) == 1:
            return value[0], value[0], value[0], value[0]
    return value, value, value, value


def _add_padding(existing, before, after):
    """Add legacy widget padding to any caller-supplied geometry padding."""
    if isinstance(existing, (tuple, list)) and len(existing) == 2:
        old_before, old_after = existing
    else:
        old_before = old_after = existing or 0
    return old_before + before, old_after + after


class _StyleCompat:
    """No-op ttk.Style facade for the small number of legacy style calls."""
    def __init__(self, *_a, **_kw):
        pass

    @staticmethod
    def theme_names():
        return ()

    @staticmethod
    def theme_use(*_a, **_kw):
        return None

    @staticmethod
    def configure(*_a, **_kw):
        return None

    @staticmethod
    def map(*_a, **_kw):
        return None

    @staticmethod
    def lookup(*_a, **_kw):
        return APP_BG


class _CTkFrame(ctk.CTkFrame):
    def __init__(self, master=None, **kwargs):
        padding = kwargs.pop('padding', None)
        kwargs.pop('cursor', None)
        kwargs.pop('style', None)
        kwargs.pop('relief', None)
        borderwidth = kwargs.pop('borderwidth', None)
        if borderwidth is not None:
            kwargs.setdefault('border_width', borderwidth)
        background = kwargs.pop('background', None)
        if background:
            kwargs.setdefault('fg_color', background)
        self._legacy_padding = padding
        kwargs.setdefault('fg_color', 'transparent')
        kwargs.setdefault('corner_radius', 0)
        super().__init__(master, **kwargs)

    def _apply_padding(self, method, kwargs):
        left, top, right, bottom = _padding_sides(self._legacy_padding)
        if left or right:
            kwargs['padx'] = _add_padding(kwargs.get('padx', 0), left, right)
        if top or bottom:
            kwargs['pady'] = _add_padding(kwargs.get('pady', 0), top, bottom)
        return method(**kwargs)

    def pack(self, *args, **kwargs):
        return self._apply_padding(super().pack, kwargs)

    def grid(self, *args, **kwargs):
        return self._apply_padding(super().grid, kwargs)

    def configure(self, **kwargs):
        kwargs.pop('style', None)
        kwargs.pop('padding', None)
        if 'background' in kwargs:
            kwargs['fg_color'] = kwargs.pop('background')
        if 'borderwidth' in kwargs:
            kwargs['border_width'] = kwargs.pop('borderwidth')
        kwargs.pop('relief', None)
        return super().configure(**kwargs)

    config = configure


class _CTkLabel(ctk.CTkLabel):
    def __init__(self, master=None, **kwargs):
        style = kwargs.pop('style', '')
        variable = kwargs.pop('textvariable', None)
        width = kwargs.get('width')
        if width is not None:
            kwargs['width'] = _char_width(width)
        kwargs.pop('padding', None)
        if 'foreground' in kwargs:
            kwargs['text_color'] = kwargs.pop('foreground')
        if 'background' in kwargs:
            kwargs['fg_color'] = kwargs.pop('background')
        if style in ('Head.TLabel', 'Sec.TLabel', 'Step.TLabel'):
            kwargs.setdefault('font', (UI_FONT_BOLD, 12 if style == 'Step.TLabel' else 10))
            kwargs.setdefault('text_color', TEXT)
        elif style == 'Num.TLabel':
            kwargs.setdefault('font', (UI_FONT_BOLD, 15))
            kwargs.setdefault('text_color', ACCENT)
        elif style in ('Hint.TLabel', 'Wait.TLabel'):
            kwargs.setdefault('text_color', TEXT_MUTED)
        elif style == 'Info.TLabel':
            kwargs.setdefault('text_color', '#b9c7da')
        elif style == 'Rec.TLabel' or style == 'Good.TLabel':
            kwargs.setdefault('text_color', GOOD)
        elif style == 'Splice.TLabel':
            kwargs.setdefault('text_color', WARN)
        elif style == 'Bad.TLabel':
            kwargs.setdefault('text_color', BAD)
        kwargs.setdefault('font', (UI_FONT, 10))
        kwargs.setdefault('text_color', TEXT)
        kwargs.setdefault('fg_color', 'transparent')
        super().__init__(master, **kwargs)
        self._textvar = variable
        self._textvar_trace = None
        if variable is not None:
            self.configure(text=variable.get())
            self._textvar_trace = variable.trace_add('write', self._sync_textvar)

    def _sync_textvar(self, *_a):
        try:
            self.configure(text=self._textvar.get())
        except (tk.TclError, RuntimeError):
            pass

    def configure(self, **kwargs):
        if 'foreground' in kwargs:
            kwargs['text_color'] = kwargs.pop('foreground')
        if 'background' in kwargs:
            kwargs['fg_color'] = kwargs.pop('background')
        style = kwargs.pop('style', None)
        if style in ('Head.TLabel', 'Sec.TLabel', 'Step.TLabel'):
            kwargs.setdefault('text_color', TEXT)
            kwargs.setdefault('font', (UI_FONT_BOLD, 12 if style == 'Step.TLabel' else 10))
        elif style == 'Num.TLabel':
            kwargs.setdefault('text_color', ACCENT)
            kwargs.setdefault('font', (UI_FONT_BOLD, 15))
        elif style in ('Hint.TLabel', 'Wait.TLabel'):
            kwargs.setdefault('text_color', TEXT_MUTED)
        elif style == 'Info.TLabel':
            kwargs.setdefault('text_color', '#b9c7da')
        elif style in ('Rec.TLabel', 'Good.TLabel'):
            kwargs.setdefault('text_color', GOOD)
        elif style == 'Splice.TLabel':
            kwargs.setdefault('text_color', WARN)
        elif style == 'Bad.TLabel':
            kwargs.setdefault('text_color', BAD)
        kwargs.pop('padding', None)
        if 'width' in kwargs and isinstance(kwargs['width'], (int, float)):
            kwargs['width'] = _char_width(kwargs['width'])
        return super().configure(**kwargs)

    config = configure


class _CTkEntry(ctk.CTkEntry):
    def __init__(self, master=None, **kwargs):
        # CTkEntry supports tkinter.StringVar through its native textvariable option.
        if 'width' in kwargs:
            kwargs['width'] = _char_width(kwargs['width'], minimum=55)
        kwargs.pop('style', None)
        kwargs.setdefault('fg_color', FIELD_BG)
        kwargs.setdefault('border_color', BORDER)
        kwargs.setdefault('text_color', TEXT)
        kwargs.setdefault('placeholder_text_color', TEXT_MUTED)
        kwargs.setdefault('height', 32)
        super().__init__(master, **kwargs)


class _CTkButton(ctk.CTkButton):
    def __init__(self, master=None, **kwargs):
        style = kwargs.pop('style', '')
        width = kwargs.get('width')
        if width is not None:
            kwargs['width'] = _char_width(width)
        if style in ('Accent.TButton', 'Go.TButton'):
            kwargs.setdefault('fg_color', ACCENT)
            kwargs.setdefault('hover_color', ACCENT_HOVER)
            kwargs.setdefault('text_color', '#ffffff')
            kwargs.setdefault('font', (UI_FONT_BOLD, 12 if style == 'Go.TButton' else 10))
            kwargs.setdefault('height', 42 if style == 'Go.TButton' else 36)
        elif style == 'Big.TButton':
            kwargs.setdefault('height', 38)
            kwargs.setdefault('font', (UI_FONT_BOLD, 10))
        else:
            kwargs.setdefault('fg_color', '#303c4d')
            kwargs.setdefault('hover_color', '#3b4b60')
            kwargs.setdefault('text_color', TEXT)
            kwargs.setdefault('height', 32)
        kwargs.setdefault('corner_radius', 8)
        super().__init__(master, **kwargs)


class _CTkCheckbutton(ctk.CTkCheckBox):
    def __init__(self, master=None, **kwargs):
        kwargs.pop('style', None)
        if 'width' in kwargs:
            kwargs['width'] = _char_width(kwargs['width'], minimum=22)
        kwargs.setdefault('text_color', TEXT)
        kwargs.setdefault('hover_color', ACCENT_HOVER)
        kwargs.setdefault('fg_color', ACCENT)
        kwargs.setdefault('border_color', '#6c788a')
        kwargs.setdefault('font', (UI_FONT, 10))
        kwargs.setdefault('checkbox_width', 18)
        kwargs.setdefault('checkbox_height', 18)
        super().__init__(master, **kwargs)


class _CTkComboBox(ctk.CTkComboBox):
    def __init__(self, master=None, **kwargs):
        self._virtual_bindings = []
        self._legacy_command = kwargs.pop('command', None)
        if 'textvariable' in kwargs:
            kwargs['variable'] = kwargs.pop('textvariable')
        if 'width' in kwargs:
            kwargs['width'] = _char_width(kwargs['width'], minimum=90)
        if 'values' in kwargs and not isinstance(kwargs['values'], list):
            kwargs['values'] = list(kwargs['values'])
        kwargs.pop('style', None)
        kwargs.setdefault('fg_color', FIELD_BG)
        kwargs.setdefault('border_color', BORDER)
        kwargs.setdefault('button_color', '#344258')
        kwargs.setdefault('button_hover_color', ACCENT_HOVER)
        kwargs.setdefault('dropdown_fg_color', CARD_BG)
        kwargs.setdefault('dropdown_text_color', TEXT)
        kwargs.setdefault('text_color', TEXT)
        kwargs.setdefault('font', (UI_FONT, 10))
        kwargs.setdefault('dropdown_font', (UI_FONT, 10))
        kwargs.setdefault('height', 32)
        kwargs['command'] = self._selected
        super().__init__(master, **kwargs)

    def _selected(self, value):
        if self._legacy_command:
            self._legacy_command(value)
        event = SimpleNamespace(widget=self)
        for callback in tuple(self._virtual_bindings):
            callback(event)

    def bind(self, sequence=None, func=None, add=None):
        if sequence == '<<ComboboxSelected>>' and func is not None:
            self._virtual_bindings.append(func)
            return None
        return super().bind(sequence, func, add)

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def __getitem__(self, key):
        if key == 'values':
            return self.cget('values')
        return self.cget(key)


class _CTkSpinbox(ctk.CTkFrame):
    """Small CustomTkinter numeric stepper replacing ttk.Spinbox."""
    def __init__(self, master=None, **kwargs):
        self.variable = kwargs.pop('textvariable', None)
        self.minimum = kwargs.pop('from_', 0)
        self.maximum = kwargs.pop('to', 100)
        self.increment = kwargs.pop('increment', 1)
        width = _char_width(kwargs.pop('width', 6), minimum=48)
        kwargs.pop('style', None)
        super().__init__(master, width=width + 52, height=32, fg_color='transparent', corner_radius=0)
        self.grid_columnconfigure(0, weight=1)
        self.entry = _CTkEntry(self, textvariable=self.variable, width=width, height=32)
        self.entry.grid(row=0, column=0, sticky='ew')
        arrows = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0)
        arrows.grid(row=0, column=1, sticky='ns', padx=(3, 0))
        self.up = ctk.CTkButton(arrows, text='＋', width=23, height=14, corner_radius=4,
                                fg_color='#303c4d', hover_color='#3b4b60', command=lambda: self._step(1))
        self.up.pack(side='top')
        self.down = ctk.CTkButton(arrows, text='－', width=23, height=14, corner_radius=4,
                                  fg_color='#303c4d', hover_color='#3b4b60', command=lambda: self._step(-1))
        self.down.pack(side='bottom')

    def _step(self, direction):
        try:
            current = float(self.variable.get()) if self.variable is not None else float(self.entry.get())
        except (TypeError, ValueError, tk.TclError):
            current = self.minimum
        value = max(self.minimum, min(self.maximum, current + direction * self.increment))
        if self.variable is not None:
            self.variable.set(int(value) if value.is_integer() else value)
        else:
            self.entry.delete(0, 'end')
            self.entry.insert(0, str(value))

    def configure(self, **kwargs):
        state = kwargs.pop('state', None)
        if state is not None:
            self.entry.configure(state=state)
            self.up.configure(state=state)
            self.down.configure(state=state)
        if 'textvariable' in kwargs:
            self.variable = kwargs.pop('textvariable')
            self.entry.configure(textvariable=self.variable)
        return super().configure(**kwargs)

    config = configure

    def get(self):
        return self.entry.get()

    def set(self, value):
        if self.variable is not None:
            self.variable.set(value)
        else:
            self.entry.delete(0, 'end')
            self.entry.insert(0, str(value))


class _CTkScrollbar(ctk.CTkScrollbar):
    def __init__(self, master=None, **kwargs):
        if 'orient' in kwargs:
            kwargs['orientation'] = kwargs.pop('orient')
        kwargs.setdefault('button_color', '#45536a')
        kwargs.setdefault('button_hover_color', ACCENT)
        kwargs.setdefault('fg_color', PANEL_BG)
        super().__init__(master, **kwargs)


class _CTkProgressBar(ctk.CTkProgressBar):
    def __init__(self, master=None, **kwargs):
        self._legacy_mode = kwargs.get('mode', 'determinate')
        length = kwargs.pop('length', None)
        if length is not None:
            kwargs.setdefault('width', length)
        kwargs.setdefault('progress_color', ACCENT)
        kwargs.setdefault('fg_color', '#303a4b')
        kwargs.setdefault('height', 8)
        super().__init__(master, **kwargs)

    def start(self, *_a):
        super().start()

    def configure(self, **kwargs):
        mode = kwargs.get('mode')
        value = kwargs.pop('value', None)
        if mode:
            self._legacy_mode = mode
        result = super().configure(**kwargs)
        if value is not None:
            self.set(float(value) / 100 if float(value) > 1 else float(value))
        return result

    config = configure


class _CTkSeparator(ctk.CTkFrame):
    def __init__(self, master=None, **kwargs):
        orientation = kwargs.pop('orient', 'horizontal')
        kwargs.pop('style', None)
        if orientation == 'vertical':
            kwargs.setdefault('width', 1)
            kwargs.setdefault('height', 80)
        else:
            kwargs.setdefault('height', 1)
            kwargs.setdefault('width', 80)
        kwargs.setdefault('fg_color', BORDER)
        kwargs.setdefault('corner_radius', 0)
        super().__init__(master, **kwargs)


class _CTkOptionMenu(ctk.CTkOptionMenu):
    def __init__(self, master, variable, default, *values, **kwargs):
        kwargs.pop('style', None)
        if 'width' in kwargs:
            kwargs['width'] = _char_width(kwargs['width'], minimum=90)
        kwargs.setdefault('fg_color', '#303c4d')
        kwargs.setdefault('button_color', '#45536a')
        kwargs.setdefault('button_hover_color', ACCENT_HOVER)
        kwargs.setdefault('dropdown_fg_color', CARD_BG)
        kwargs.setdefault('text_color', TEXT)
        kwargs.setdefault('font', (UI_FONT, 10))
        super().__init__(master, variable=variable, values=list(values), **kwargs)
        if default is not None:
            self.set(default)


class _CTkLabelFrame(_CTkFrame):
    """Card with a heading row; children are shifted down when the card is laid out."""
    def __init__(self, master=None, **kwargs):
        labelwidget = kwargs.pop('labelwidget', None)
        text = ''
        if labelwidget is not None:
            try:
                text = labelwidget.cget('text').strip()
            except tk.TclError:
                pass
            try:
                labelwidget.destroy()
            except tk.TclError:
                pass
        padding = kwargs.pop('padding', (10, 6))
        kwargs.setdefault('fg_color', CARD_BG)
        kwargs.setdefault('border_width', 1)
        kwargs.setdefault('border_color', BORDER)
        kwargs.setdefault('corner_radius', 12)
        super().__init__(master, padding=padding, **kwargs)
        self._heading = _CTkLabel(self, text=text, font=(UI_FONT_BOLD, 10), text_color=TEXT,
                                  fg_color=CARD_BG)
        # The calling code grids the group before adding its row-0 controls. Float the
        # title on the border, then reserve top space for those controls after construction.
        self._heading.place(x=12, y=2)
        self._heading_reserved = False

    def _reserve_heading_space(self):
        if self._heading_reserved:
            return
        for child in self.winfo_children():
            if child is self._heading:
                continue
            try:
                info = child.grid_info()
                if info and int(info.get('row', 0)) == 0:
                    old_pady = info.get('pady', 0)
                    bottom = 3
                    try:
                        if isinstance(old_pady, (tuple, list)):
                            bottom = old_pady[1]
                        elif isinstance(old_pady, str) and ' ' in old_pady:
                            bottom = int(float(old_pady.split()[-1]))
                        else:
                            bottom = int(float(old_pady))
                    except (TypeError, ValueError):
                        pass
                    child.grid_configure(pady=(24, bottom))
            except (tk.TclError, ValueError):
                pass
        self._heading_reserved = True

    def grid(self, *args, **kwargs):
        result = super().grid(*args, **kwargs)
        self.after_idle(self._reserve_heading_space)
        return result


class _TTKCompat:
    Style = _StyleCompat
    Frame = _CTkFrame
    Label = _CTkLabel
    Entry = _CTkEntry
    Button = _CTkButton
    Checkbutton = _CTkCheckbutton
    Combobox = _CTkComboBox
    Spinbox = _CTkSpinbox
    Scrollbar = _CTkScrollbar
    Progressbar = _CTkProgressBar
    Separator = _CTkSeparator
    OptionMenu = _CTkOptionMenu
    LabelFrame = _CTkLabelFrame


ttk = _TTKCompat()


class NavigationPages(ctk.CTkFrame):
    """Sidebar navigation with a page stack; keeps Notebook's small public API."""
    def __init__(self, master):
        super().__init__(master, fg_color='transparent', corner_radius=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = ctk.CTkFrame(self, width=202, fg_color=PANEL_BG, corner_radius=14,
                                    border_width=1, border_color=BORDER)
        self.sidebar.grid(row=0, column=0, sticky='nsew', padx=(0, 10), pady=2)
        self.sidebar.grid_propagate(False)
        ctk.CTkLabel(self.sidebar, text='WORKSPACE', text_color=TEXT_MUTED,
                     font=(UI_FONT_BOLD, 9)).pack(anchor='w', padx=18, pady=(18, 10))
        self.nav_buttons = ctk.CTkFrame(self.sidebar, fg_color='transparent')
        self.nav_buttons.pack(fill='x', padx=9)
        self.page_area = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0)
        self.page_area.grid(row=0, column=1, sticky='nsew')
        self.page_area.grid_columnconfigure(0, weight=1)
        self.page_area.grid_rowconfigure(0, weight=1)
        self.pages = []
        self.buttons = []
        self.current = 0
        self._tab_changed = None

    def add(self, page, text=''):
        index = len(self.pages)
        self.pages.append(page)
        page.grid(row=0, column=0, sticky='nsew')
        page.grid_remove()
        button = ctk.CTkButton(self.nav_buttons, text=text, anchor='w', height=42,
                               fg_color='transparent', hover_color='#2b3545',
                               text_color=TEXT_MUTED, font=(UI_FONT, 10), corner_radius=8,
                               command=lambda i=index: self.select(i))
        button.pack(fill='x', pady=2)
        self.buttons.append(button)
        if index == 0:
            self.select(0)

    def select(self, index):
        if isinstance(index, str):
            try:
                index = self.pages.index(self.nametowidget(index))
            except (ValueError, tk.TclError):
                return
        try:
            index = int(index)
        except (TypeError, ValueError):
            return
        if index < 0 or index >= len(self.pages):
            return
        for i, page in enumerate(self.pages):
            if i == index:
                page.grid()
            else:
                page.grid_remove()
        for i, button in enumerate(self.buttons):
            active = i == index
            button.configure(fg_color=ACCENT if active else 'transparent',
                             text_color='#ffffff' if active else TEXT_MUTED,
                             hover_color=ACCENT_HOVER if active else '#2b3545')
        changed = index != self.current
        self.current = index
        if changed and self._tab_changed:
            self.after_idle(lambda: self._tab_changed(SimpleNamespace(widget=self)))

    def index(self, what):
        if what == 'current':
            return self.current
        if what in self.pages:
            return self.pages.index(what)
        return int(what)

    def bind(self, sequence=None, func=None, add=None):
        if sequence == '<<NotebookTabChanged>>':
            self._tab_changed = func
            return None
        return super().bind(sequence, func, add)


class ScrollFrame(ttk.Frame):
    """A frame whose .body scrolls vertically (wheel included) once it is taller than the space it gets."""

    def __init__(self, parent):
        super().__init__(parent)
        bg = APP_BG
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=bg)
        self.vsb = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = tk.Frame(self.canvas, background=APP_BG, borderwidth=0, highlightthickness=0)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind('<Configure>', self._fit)
        self.canvas.bind('<Configure>', self._fit)
        self.bind('<Enter>', lambda _e: self.bind_all('<MouseWheel>', self._wheel))
        self.bind('<Leave>', lambda _e: self.unbind_all('<MouseWheel>'))

    def _fit(self, _e=None):
        self.canvas.itemconfigure(self._win, width=self.canvas.winfo_width())
        self.canvas.configure(scrollregion=(0, 0, self.body.winfo_reqwidth(), self.body.winfo_reqheight()))
        if self.body.winfo_reqheight() > self.canvas.winfo_height() + 1:
            if not self.vsb.winfo_ismapped():
                self.vsb.pack(side='right', fill='y', before=self.canvas)
        elif self.vsb.winfo_ismapped():
            self.vsb.pack_forget()
            self.canvas.yview_moveto(0)

    def _wheel(self, ev):
        if self.vsb.winfo_ismapped() and not isinstance(ev.widget, (ttk.Combobox, ttk.Spinbox)):
            self.canvas.yview_scroll(-1 if ev.delta > 0 else 1, 'units')


class Section(ttk.Frame):
    """A titled block that folds open and shut. summary (a StringVar) shows next to the title."""

    def __init__(self, parent, title, opened=False, on_toggle=None):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.opened, self.on_toggle = opened, on_toggle
        self.summary = tk.StringVar()
        head = ttk.Frame(self, cursor='hand2')
        head.grid(row=0, column=0, sticky='ew')
        self.arrow = ttk.Label(head, style='Sec.TLabel', width=2)
        self.arrow.pack(side='left')
        title_lb = ttk.Label(head, text=title, style='Sec.TLabel')
        title_lb.pack(side='left')
        sum_lb = ttk.Label(head, textvariable=self.summary, style='Hint.TLabel')
        sum_lb.pack(side='left', padx=12)
        for w in (head, self.arrow, title_lb, sum_lb):
            w.bind('<Button-1>', lambda _e: self.toggle())
        ttk.Separator(self).grid(row=1, column=0, sticky='ew', pady=(3, 0))
        self.body = ttk.Frame(self, padding=(20, 6, 0, 2))
        self._show()

    def toggle(self):
        self.opened = not self.opened
        self._show()
        if self.on_toggle:
            self.on_toggle()

    def _show(self):
        self.arrow.configure(text='▾' if self.opened else '▸')
        if self.opened:
            self.body.grid(row=2, column=0, sticky='ew')
        else:
            self.body.grid_remove()


class Runner:
    """Runs one command at a time in the background and streams its output into the log."""

    def __init__(self, app):
        self.app, self.proc, self.q = app, None, queue.Queue()

    @property
    def busy(self):
        return self.proc is not None

    def start(self, cmd, title, done=None):
        if not cmd:
            return
        if self.busy:
            messagebox.showinfo('Busy', 'Wait for the current step to finish (or press Stop).')
            return
        self.app.log('\n$ %s\n' % ' '.join('"%s"' % c if ' ' in c else c for c in cmd[1:]), 'cmd')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            self.proc = subprocess.Popen(cmd, cwd=APP_DIR if FROZEN else ROOT,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, text=True, encoding='utf-8',
                                         errors='replace', bufsize=1, creationflags=flags)
        except OSError as ex:
            messagebox.showerror('Could not start task',
                                 'Henkan could not start its command worker.\n\n%s\n\n'
                                 'Make sure HenkanWorker.exe is next to Henkan.exe.' % ex)
            return
        self.done, self.title = done, title
        self.app.set_busy(title)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        self.app.after(100, self._poll)

    def _read(self, proc):
        for line in proc.stdout:
            if 'warnings.warn(' in line or '32-bit application should be automated' in line:
                continue                                    # pywinauto noise
            self.q.put(line)
        proc.wait()
        self.q.put(None)

    def _poll(self):
        try:
            while True:
                line = self.q.get_nowait()
                if line is None:
                    code = self.proc.returncode
                    self.proc = None
                    self.app.set_busy(None)
                    self.app.log('%s %s\n' % (self.title, 'finished' if code == 0 else 'failed (exit %s)' % code),
                                 'ok' if code == 0 else 'err')
                    if self.done:
                        self.done(code)
                    self.app.step_ended(code)
                    return
                tag = 'err' if re.search(r'Traceback|Error|failed|problems', line) else None
                self.app.log(line, tag)
        except queue.Empty:
            pass
        self.app.after(100, self._poll)

    def stop(self):
        if not self.proc:
            return
        # the build drives the DBTool and the render the dev editor: end the whole process tree
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(self.proc.pid)], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.app.log('stopped\n', 'err')


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        from u2v import __version__
        self.title('Henkan — UTAU → VOCALOID ' + __version__)
        self.geometry('960x900')
        self.minsize(820, 640)
        self.runner = Runner(self)
        self.v = {}
        self.groups = []          # [(group, entries)]
        self.detected_lang = None
        self._vars()
        self._load()
        self._ui()
        if re.fullmatch(r'\d+x\d+([+-]-?\d+){2}', self.v['geometry'].get()):
            self.geometry(self.v['geometry'].get())
        self.protocol('WM_DELETE_WINDOW', self._close)
        if self.v['bank'].get() and os.path.isdir(self.v['bank'].get()):
            self.after(200, lambda: (self.scan(), self._easy_check()))

    # ------------------------------------------------------------------ state
    def _vars(self):
        S, B, I = tk.StringVar, tk.BooleanVar, tk.IntVar
        self.v.update(bank=S(), out=S(), dict=S(), lang=S(value='auto'), colors=S(value='main'), keep=I(value=1),
                      splice=B(value=True), fill=B(value=True), normalize=B(value=True), derive=B(value=True),
                      extend_dict=B(value=True), merge_groups=B(value=False), extras=B(value=True),
                      brighten=S(), avoid=S(), evec_map=S(), evec_phonemes=S(),
                      custom_extras=S(), evec_vowels=B(value=False), v_mode=S(value=V_MODE_LABELS[0]),
                      triphones=B(value=False), pitch_assignments=S(value='{}'),
                      sections=S(value='extras'), geometry=S(), tab=S(value='0'),
                      db_name=S(), db_path=S(), db_lang=S(value='Japanese'),
                      pkg_db=S(), pkg_name=S(), pkg_compid=S(), pkg_out=S(), pkg_editor=S(),
                      pkg_dest=S(), pkg_devini=B(value=False), growl_wav=S(),
                      pkg_breathiness=S(value='0'), pkg_brightness=S(value='0'),
                      pkg_clearness=S(value='0'), pkg_gender_factor=S(value='0'),
                      v3v2_src=S(), v3v2_name=S(), v3v2_lang=S(value='Auto'),
                      dbtool=S(value=os.path.join(DEVKIT, 'VocaloidDBTool3.exe')), dbtool_hidden=B(value=True))
        self.status = S(value='Ready')
        self.info = S(value='Choose an UTAU voicebank folder.')
        self.evec_info = S(value='')
        self.evec_pick, self.evec_pick_order, self.color_rows = {}, [], []

    def _load(self):
        for path in (SETTINGS, OLD_SETTINGS):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            for k in SAVED:
                if k in data:
                    try:
                        self.v[k].set(data[k])
                    except tk.TclError:
                        pass
            break
        if self.v['v_mode'].get().split()[0] not in ('auto', 'v', 'b'):
            self.v['v_mode'].set(V_MODE_LABELS[0])

    def _save(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
            self.v['geometry'].set(self.geometry())
            if hasattr(self, 'nb'):
                self.v['tab'].set(str(self.nb.index('current')))
            with open(SETTINGS, 'w', encoding='utf-8') as f:
                json.dump({k: self.v[k].get() for k in SAVED}, f, indent=1)
        except OSError:
            pass

    def _close(self):
        if (self.runner.busy or getattr(self, '_v3v2_busy', False)) \
                and not messagebox.askyesno('Quit', 'A step is still running. Stop it and quit?'):
            return
        self.runner.stop()
        self._save()
        self.destroy()

    # --------------------------------------------------------------------- UI
    def _ui(self):
        """Build the CustomTkinter workspace around the existing, tested workflows."""
        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('dark-blue')
        self.configure(fg_color=APP_BG)

        self._header()

        # Source context strip. It is hidden on Quick Start and shown on the advanced pages.
        self.src = src = ttk.Frame(self, padding=(16, 10, 16, 8), fg_color=PANEL_BG,
                                   corner_radius=12, border_width=1, border_color=BORDER)
        src.pack(fill='x', padx=16, pady=(10, 4))
        src.columnconfigure(1, weight=1)
        ttk.Label(src, text='UTAU voicebank', style='Head.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Entry(src, textvariable=self.v['bank']).grid(row=0, column=1, sticky='ew', padx=8)
        ttk.Button(src, text='Browse…', command=self.pick_bank).grid(row=0, column=2)
        ttk.Button(src, text='Rescan', command=self.scan).grid(row=0, column=3, padx=(6, 0))
        ttk.Button(src, text='OTO Inspector', command=self.show_oto_report).grid(row=0, column=4, padx=(6, 0))
        ttk.Label(src, textvariable=self.info, style='Info.TLabel').grid(
            row=1, column=1, columnspan=4, sticky='w', padx=8, pady=(3, 0))
        ttk.Label(src, text='Phonetic dictionary', style='Head.TLabel').grid(
            row=2, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(src, textvariable=self.v['dict']).grid(row=2, column=1, sticky='ew', padx=8, pady=(8, 0))
        ttk.Button(src, text='Browse…', command=self.pick_dict).grid(row=2, column=2, pady=(8, 0))
        ttk.Label(src, text='Japanese_Dictionary.txt / english_phonetic_dictionary_20061220.txt',
                  style='Hint.TLabel').grid(row=3, column=1, columnspan=4, sticky='w', padx=8, pady=(2, 0))

        # Main workspace: a persistent workflow sidebar instead of crowded tabs.
        self.pw = work = ttk.Frame(self, fg_color='transparent')
        work.pack(fill='both', expand=True, padx=16, pady=(8, 4))
        work.grid_columnconfigure(0, weight=1)
        work.grid_rowconfigure(0, weight=1)
        self.nb = NavigationPages(work)
        self.nb.grid(row=0, column=0, sticky='nsew')
        self.nb.add(self._tab_easy(self.nb.page_area), '✦   Quick Start')
        self.nb.add(self._tab_convert(self.nb.page_area), '01   Convert')
        self.nb.add(self._tab_build(self.nb.page_area), '02   Build singer DB')
        self.nb.add(self._tab_pack(self.nb.page_area), '03   Pack & Install')
        self.nb.add(self._tab_v3v2(self.nb.page_area), '04   V3 → V2')
        self.nb.add(self._tab_system(self.nb.page_area), '05   PC Requirements')
        try:
            self.nb.select(int(self.v['tab'].get()))
        except (TypeError, ValueError):
            self.nb.select(0)

        # Collapsible activity console; output is still the exact CLI stream.
        self.log_panel = ttk.Frame(work, fg_color=PANEL_BG, corner_radius=12,
                                   border_width=1, border_color=BORDER)
        self.log_panel.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        self.log_header = ttk.Frame(self.log_panel, padding=(12, 5, 10, 5), fg_color='transparent')
        self.log_header.pack(fill='x')
        ttk.Label(self.log_header, text='ACTIVITY', style='Head.TLabel').pack(side='left')
        ttk.Label(self.log_header, text='Live command output', style='Hint.TLabel').pack(side='left', padx=10)
        self.log_toggle = ttk.Button(self.log_header, text='Hide', width=7, command=self._toggle_log)
        self.log_toggle.pack(side='right')
        self.log_body = ttk.Frame(self.log_panel, fg_color='transparent')
        self.log_body.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        self.text = tk.Text(self.log_body, wrap='word', font=('Consolas', 9), height=6, relief='flat',
                            padx=10, pady=8, background='#0d131c', foreground='#d8e1ed',
                            insertbackground='#d8e1ed', selectbackground='#5d3150',
                            highlightthickness=1, highlightbackground=BORDER, borderwidth=0)
        sb = ttk.Scrollbar(self.log_body, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.text.pack(fill='both', expand=True)
        self.text.tag_configure('cmd', foreground='#8ecbff')
        self.text.tag_configure('ok', foreground='#68d6a4')
        self.text.tag_configure('warn', foreground='#efc56e')
        self.text.tag_configure('err', foreground='#ff8b8b')

        # Sticky status / progress footer.
        bar = ttk.Frame(self, padding=(16, 6, 16, 10), fg_color='transparent')
        bar.pack(side='bottom', fill='x')
        self.prog = ttk.Progressbar(bar, mode='determinate', length=160)
        self.prog.pack(side='left')
        ttk.Label(bar, textvariable=self.status, style='Hint.TLabel').pack(side='left', padx=10)
        self.stop_btn = ttk.Button(bar, text='Stop process', command=self.runner.stop, state='disabled')
        self.stop_btn.pack(side='right')
        ttk.Button(bar, text='Clear log', command=self.clear_log).pack(side='right', padx=6)

        self.nb.bind('<<NotebookTabChanged>>', lambda _e: self._tab_changed())
        self.bind_all('<Control-l>', lambda _e: self.clear_log())
        self.bind_all('<F5>', lambda _e: self.scan())
        self._tab_changed()

    def _toggle_log(self):
        if self.log_body.winfo_manager():
            self.log_body.pack_forget()
            self.log_toggle.configure(text='Show')
            self.log_panel.grid_configure(sticky='ew')
        else:
            self.log_body.pack(fill='both', expand=True, padx=8, pady=(0, 8))
            self.log_toggle.configure(text='Hide')

    def _tab_changed(self):
        """Quick Start asks for the bank itself: the bank/dictionary rows on top are for the other tabs."""
        easy = self.nb.index('current') == 0
        if easy and getattr(self, '_src_shown', True):
            self.src.pack_forget()
        elif not easy and not getattr(self, '_src_shown', True):
            self.src.pack(fill='x', before=self.pw)
        self._src_shown = not easy
        if easy:
            self._easy_check()
            self.after(300, self._small_log)          # once the window has its real size
        elif self.nb.index('current') == 5:
            self.refresh_system_report()

    def _small_log(self):
        """Keep the Quick Start console compact without a draggable legacy sash."""
        if getattr(self, 'text', None) is not None:
            self.text.configure(height=4)

    # ------------------------------------------------------------------ easy
    EASY_STEPS = (('convert', 'Convert the UTAU sounds'),
                  ('build', 'Build the singer DB (a few minutes; the DBTool works by itself)'),
                  ('pack', 'Pack it into a voicebank'))

    def _tab_easy(self, nb):
        """Three steps and one button: the whole bank → voicebank road with every default taken."""
        sf = ScrollFrame(nb)
        f = ttk.Frame(sf.body, padding=(18, 14, 18, 10))
        f.pack(fill='both', expand=True)
        f.columnconfigure(0, weight=1)

        def step(row, num, title):
            head = ttk.Frame(f)
            head.grid(row=row, column=0, sticky='ew', pady=(14 if row else 0, 4))
            ttk.Label(head, text=num, style='Num.TLabel').pack(side='left', padx=(0, 10))
            ttk.Label(head, text=title, style='Step.TLabel').pack(side='left')
            body = ttk.Frame(f, padding=(34, 0, 0, 0))
            body.grid(row=row + 1, column=0, sticky='ew')
            body.columnconfigure(1, weight=1)
            return body

        b = step(0, '1', 'Pick your UTAU voicebank')
        ttk.Button(b, text='Choose folder…', style='Big.TButton', command=self.pick_bank).grid(
            row=0, column=0, sticky='w')
        self.easy_bank = tk.StringVar(value='No voicebank picked yet: choose the folder with its oto.ini.')
        self.easy_bank_lb = ttk.Label(b, textvariable=self.easy_bank, style='Wait.TLabel', wraplength=640,
                                      justify='left')
        self.easy_bank_lb.grid(row=0, column=1, sticky='w', padx=12)

        b = step(2, '2', 'Check your setup')
        b.columnconfigure(1, weight=0)
        b.columnconfigure(3, weight=1)
        self.easy_checks = {}
        for r, (key, name) in enumerate((('dbtool', 'DBTool'), ('dict', 'Dictionary'), ('pkgs', 'Packages'),
                                         ('editor', 'Editor'))):
            ttk.Label(b, text=name, style='Easy.TLabel', width=11).grid(row=r, column=0, sticky='w', pady=2)
            lb = ttk.Label(b, style='Wait.TLabel', wraplength=560, justify='left')
            lb.grid(row=r, column=1, sticky='w')
            btn = ttk.Button(b, text='Find it…')
            btn.grid(row=r, column=2, sticky='e', padx=(8, 0))
            self.easy_checks[key] = (lb, btn)

        b = step(4, '3', 'Name your singer and go')
        ttk.Label(b, text='Singer name', style='Easy.TLabel', width=11).grid(row=0, column=0, sticky='w')
        ttk.Entry(b, textvariable=self.v['db_name'], width=24, font=(UI_FONT, 10)).grid(row=0, column=1,
                                                                                        sticky='w')
        self.easy_name = ttk.Label(b, style='Hint.TLabel')
        self.easy_name.grid(row=1, column=1, sticky='w', pady=(2, 0))
        ttk.Checkbutton(b, text='Show the DBTool while it works (only if it keeps getting stuck)',
                        variable=self.v['dbtool_hidden'], onvalue=False, offvalue=True).grid(
            row=2, column=1, sticky='w', pady=(6, 0))
        self.easy_go = ttk.Button(b, text='▶  Make my voicebank', style='Go.TButton', command=self.easy_make)
        self.easy_go.grid(row=3, column=0, columnspan=2, sticky='w', pady=(12, 6))
        prog = ttk.Frame(b)
        prog.grid(row=4, column=0, columnspan=3, sticky='w')
        self.easy_marks = {}
        for r, (key, text) in enumerate(self.EASY_STEPS):
            mark = ttk.Label(prog, text='○', style='Wait.TLabel', width=2)
            mark.grid(row=r, column=0, sticky='w')
            lb = ttk.Label(prog, text=text, style='Wait.TLabel')
            lb.grid(row=r, column=1, sticky='w')
            self.easy_marks[key] = (mark, lb)

        b = step(6, '4', 'Install it')
        ttk.Label(b, text='Into', style='Easy.TLabel', width=11).grid(row=0, column=0, sticky='w')
        self.easy_editor = ttk.Combobox(b, textvariable=self.v['pkg_editor'], width=60, state='readonly')
        self.easy_editor.grid(row=0, column=1, sticky='w')
        self.easy_editor.bind('<<ComboboxSelected>>', lambda _e: self._easy_check())
        self.easy_install = ttk.Button(b, text='Install', style='Accent.TButton', command=self.easy_do_install,
                                       state='disabled')
        self.easy_install.grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 0))
        self.easy_done = tk.StringVar(value='Make the voicebank first (step 3). Windows asks for permission '
                                            'when you install.')
        ttk.Label(b, textvariable=self.easy_done, style='Hint.TLabel', wraplength=640, justify='left').grid(
            row=2, column=0, columnspan=3, sticky='w', pady=(6, 0))

        ttk.Label(f, text='Want more control (folders, voice colours, extras, CompIDs, V3→V2)? '
                          'Use the workflow sidebar for conversion options, database building, packaging and V3 → V2 tools.', style='Hint.TLabel').grid(
            row=8, column=0, sticky='w', pady=(20, 0))
        ctk.CTkButton(f, text='PC requirements & compatibility check', height=34,
                      fg_color=CARD_BG, hover_color='#2b3545', text_color=TEXT,
                      border_width=1, border_color=BORDER,
                      command=lambda: self.nb.select(5)).grid(row=9, column=0, sticky='w', pady=(12, 4))
        self._easy_run = None
        self._step_log = []
        self.v['db_name'].trace_add('write', lambda *_a: self._easy_check())
        return sf

    def _tab_system(self, nb):
        """Show practical PC requirements and a live, read-only compatibility report."""
        sf = ScrollFrame(nb)
        body = sf.body
        body.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(body, text='PC requirements & compatibility', font=(UI_FONT_BOLD, 22),
                     text_color=TEXT).grid(row=0, column=0, columnspan=2, sticky='w', padx=18, pady=(18, 4))
        ctk.CTkLabel(body, text='Henkan is a Windows desktop application. This check reads your PC specs and '
                     'points out common reasons a task may fail. It does not change your system.',
                     text_color=TEXT_MUTED, wraplength=760, justify='left').grid(
            row=1, column=0, columnspan=2, sticky='w', padx=18, pady=(0, 12))

        summary = ctk.CTkFrame(body, fg_color=CARD_BG, border_width=1, border_color=BORDER, corner_radius=12)
        summary.grid(row=2, column=0, columnspan=2, sticky='ew', padx=18, pady=(0, 12))
        summary.grid_columnconfigure(0, weight=1)
        self.system_summary = ctk.CTkLabel(summary, text='Run the compatibility check to see your PC status.',
                                           text_color=TEXT, font=(UI_FONT_BOLD, 13), anchor='w',
                                           justify='left', wraplength=720)
        self.system_summary.grid(row=0, column=0, sticky='ew', padx=14, pady=12)
        ctk.CTkButton(summary, text='↻  Refresh check', width=142, height=34,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self.refresh_system_report).grid(row=0, column=1, padx=12, pady=8)

        spec_card = ctk.CTkFrame(body, fg_color=PANEL_BG, border_width=1, border_color=BORDER, corner_radius=12)
        spec_card.grid(row=3, column=0, columnspan=2, sticky='ew', padx=18, pady=(0, 12))
        ctk.CTkLabel(spec_card, text='DETECTED ON THIS PC', font=(UI_FONT_BOLD, 10),
                     text_color=TEXT_MUTED).pack(anchor='w', padx=14, pady=(12, 8))
        self.system_rows_frame = ctk.CTkFrame(spec_card, fg_color='transparent')
        self.system_rows_frame.pack(fill='x', padx=12, pady=(0, 12))

        req_card = ctk.CTkFrame(body, fg_color=PANEL_BG, border_width=1, border_color=BORDER, corner_radius=12)
        req_card.grid(row=4, column=0, columnspan=2, sticky='ew', padx=18, pady=(0, 12))
        ctk.CTkLabel(req_card, text='MINIMUM VS RECOMMENDED', font=(UI_FONT_BOLD, 10),
                     text_color=TEXT_MUTED).pack(anchor='w', padx=14, pady=(12, 8))
        requirement_text = (
            'OPERATING SYSTEM\nMinimum: Windows 10 64-bit\nRecommended: Windows 11 64-bit\n\n'
            'PROCESSOR\nMinimum: modern Intel/AMD 64-bit dual-core\nRecommended: modern 4-core CPU or better\n\n'
            'MEMORY\nMinimum: 4 GB RAM\nRecommended: 8 GB; 16 GB for large voicebanks\n\n'
            'STORAGE\nMinimum: 2 GB free working space\nRecommended: 10 GB+ free on an SSD\n\n'
            'DISPLAY\nMinimum: 820 × 640 to fit the app window\nRecommended: 1280 × 800 or higher\n\n'
            'GRAPHICS\nNo dedicated GPU required; Henkan analysis is CPU-based.'
        )
        ctk.CTkLabel(req_card, text=requirement_text, text_color=TEXT, font=(UI_FONT, 11),
                     justify='left', anchor='w').pack(fill='x', padx=14, pady=(0, 14))

        prereq = ctk.CTkFrame(body, fg_color=PANEL_BG, border_width=1, border_color=BORDER, corner_radius=12)
        prereq.grid(row=5, column=0, columnspan=2, sticky='ew', padx=18, pady=(0, 12))
        prereq.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(prereq, text='EXTERNAL COMPONENTS & WHAT THEY ARE NEEDED FOR',
                     font=(UI_FONT_BOLD, 10), text_color=TEXT_MUTED).grid(
            row=0, column=0, columnspan=2, sticky='w', padx=14, pady=(12, 8))
        self.system_external = {}
        for r, (key, title, description) in enumerate((
                ('dbtool', 'VocaloidDBTool3.exe', 'Required only for Build singer DB. Supplied/configured separately.'),
                ('dictionary', 'Phonetic dictionary', 'Required for conversion and DB creation. Choose the language-matched dictionary.'),
                ('editor', 'VOCALOID editor', 'Optional for conversion/building; needed to install or audition the result.'),
                ('runtime', 'VC++ 2008 runtime (x86)', 'Only relevant if the legacy DBTool reports side-by-side error 14001.'),
                ('admin', 'Administrator permission', 'Needed for voicebank install/uninstall because Windows registry and Program Files may be changed.'),
        )):
            ctk.CTkLabel(prereq, text=title, text_color=TEXT, font=(UI_FONT_BOLD, 11)).grid(
                row=r + 1, column=0, sticky='w', padx=14, pady=5)
            lb = ctk.CTkLabel(prereq, text=description, text_color=TEXT_MUTED, font=(UI_FONT, 10),
                              anchor='w', justify='left', wraplength=650)
            lb.grid(row=r + 1, column=1, sticky='ew', padx=12, pady=5)
            self.system_external[key] = lb

        note = ('These are practical project targets, not official Yamaha hardware specifications or a guarantee '
                'that every legacy DBTool/VOCALOID installation will work. The packaged Henkan build is 64-bit. '
                'The DBTool is a separate legacy Windows program and is not included. Its own runtime, dictionary, '
                'permissions and compatibility can fail even when the PC meets Henkan’s requirements.')
        ctk.CTkLabel(body, text=note, text_color=TEXT_MUTED, font=(UI_FONT, 10),
                     wraplength=780, justify='left').grid(row=6, column=0, columnspan=2,
                                                          sticky='w', padx=18, pady=(0, 18))
        return sf

    def refresh_system_report(self):
        """Refresh the PC report while keeping the application usable on lower-spec machines."""
        if not hasattr(self, 'system_rows_frame'):
            return
        info = collect_system_info(
            app_dir=APP_DIR,
            screen_size=(self.winfo_screenwidth(), self.winfo_screenheight()),
            frozen=FROZEN,
        )
        rows = evaluate_system(info)
        status, summary = overall_status(rows)
        colors = {'good': GOOD, 'info': '#78a9d6', 'warn': WARN, 'bad': BAD}
        symbols = {'good': '✓', 'info': '•', 'warn': '!', 'bad': '✕'}
        color = colors.get(status, TEXT)
        self.system_summary.configure(text=summary, text_color=color)

        for widget in self.system_rows_frame.winfo_children():
            widget.destroy()
        for index, row in enumerate(rows):
            cell = ctk.CTkFrame(self.system_rows_frame, fg_color=CARD_BG, corner_radius=8)
            cell.pack(fill='x', pady=3)
            top = ctk.CTkFrame(cell, fg_color='transparent')
            top.pack(fill='x', padx=9, pady=(7, 1))
            ctk.CTkLabel(top, text='%s  %s' % (symbols.get(row['status'], '•'), row['name']),
                         text_color=colors.get(row['status'], TEXT), font=(UI_FONT_BOLD, 10),
                         anchor='w').pack(side='left', fill='x', expand=True)
            ctk.CTkLabel(top, text=row['detected'], text_color=TEXT, font=(UI_FONT, 10),
                         anchor='e').pack(side='right')
            ctk.CTkLabel(cell, text='Minimum: %s  ·  Recommended: %s' %
                         (row['minimum'], row['recommended']), text_color=TEXT_MUTED,
                         font=(UI_FONT, 9), anchor='w', wraplength=760, justify='left').pack(
                fill='x', padx=9, pady=(0, 2))
            ctk.CTkLabel(cell, text=row['explanation'], text_color=TEXT_MUTED,
                         font=(UI_FONT, 9), anchor='w', wraplength=760,
                         justify='left').pack(fill='x', padx=9, pady=(0, 7))

        dbtool = self.v['dbtool'].get() if hasattr(self, 'v') else ''
        dictionary = self.v['dict'].get() if hasattr(self, 'v') else ''
        dbtool_found = bool(dbtool and os.path.isfile(dbtool))
        dictionary_found = bool(dictionary and os.path.isfile(dictionary))
        self.system_external['dbtool'].configure(
            text=('Found: %s · needed for DB builds only' % os.path.basename(dbtool)
                  if dbtool_found else
                  'Missing / not configured · needed for Build singer DB only'),
            text_color=GOOD if dbtool_found else WARN)
        self.system_external['dictionary'].configure(
            text=('Found: %s · language must match the selected bank' % os.path.basename(dictionary)
                  if dictionary_found else
                  'Missing / not configured · choose Japanese or English dictionary for conversion'),
            text_color=GOOD if dictionary_found else BAD)
        self.system_external['editor'].configure(
            text='Detected separately when the install page scans Windows. Needed for installation or rendering, '
                 'not for voicebank conversion.')
        self.system_external['runtime'].configure(
            text='Not checked automatically. Install the x86 redistributable only if DBTool shows error 14001.')
        self.system_external['admin'].configure(
            text='No permanent admin mode required. Windows should request elevation only for Install/Uninstall.')

    def _easy_set(self, key, ok, text, fix=None, fix_text='Find it…'):
        lb, btn = self.easy_checks[key]
        lb.configure(text=('✔  ' if ok else '✘  ') + text, style='Good.TLabel' if ok else 'Bad.TLabel')
        if ok or fix is None:
            btn.grid_remove()
        else:
            btn.configure(text=fix_text, command=fix)
            btn.grid()
        return ok

    def _easy_check(self):
        """Re-read the setup and say, in words, what is ready and what is missing. True when it can go."""
        if not hasattr(self, 'easy_checks'):
            return False
        bank = self.v['bank'].get()
        has_bank = bool(bank) and os.path.isdir(bank) and bool(self.groups)
        if has_bank:
            lang = {'ja': 'Japanese', 'en': 'English'}.get(self.detected_lang, 'unknown language')
            n = sum(c for _g, c in self.groups)
            self.easy_bank.set('✔  %s  (%s, %d sounds)' % (os.path.basename(bank), lang, n))
            self.easy_bank_lb.configure(style='Good.TLabel')
        elif bank:
            self.easy_bank.set('✘  %s has no oto.ini sounds. Pick the folder the voicebank\'s oto.ini is in.'
                               % os.path.basename(bank))
            self.easy_bank_lb.configure(style='Bad.TLabel')
        else:
            self.easy_bank.set('No voicebank picked yet: choose the folder with its oto.ini.')
            self.easy_bank_lb.configure(style='Wait.TLabel')

        ready = has_bank
        exe = self.v['dbtool'].get()
        ready &= self._easy_set('dbtool', os.path.isfile(exe),
                                'VocaloidDBTool3.exe found' if os.path.isfile(exe) else
                                'VocaloidDBTool3.exe not found. It comes with the VOCALOID3 devkit '
                                '(easiest: put the Henkan folder inside the devkit folder).',
                                self._easy_find_dbtool)
        d = self.v['dict'].get()
        if d and os.path.isfile(d):
            ready &= self._easy_set('dict', True, os.path.basename(d))
        elif has_bank:
            ready &= self._easy_set('dict', False, 'The devkit\'s phonetic dictionary was not found '
                                    '(Japanese Dictionary\\Japanese_Dictionary.txt).', self.pick_dict)
        else:
            self._easy_set('dict', False, 'found after you pick a voicebank')
            self.easy_checks['dict'][0].configure(style='Wait.TLabel', text='…  found after you pick a voicebank')
        missing = self._missing_packages()
        ready &= self._easy_set('pkgs', not missing,
                                'all installed' if not missing else 'missing: %s' % ', '.join(missing),
                                self._easy_install_packages, 'Install')
        if not getattr(self, '_easy_eds', None):                 # the registry scan: once, not per keystroke
            self._easy_eds = self._editors()
        eds = self._easy_eds
        self.easy_editor['values'] = eds
        if not self.v['pkg_editor'].get() and getattr(self, '_editor_list', []):
            self.v['pkg_editor'].set(eds[0])
        has_ed = bool(getattr(self, '_editor_list', []))
        self._easy_set('editor', has_ed, ('%d VOCALOID editor(s) found' % len(eds)) if has_ed else
                       'No VOCALOID editor found. You can still make the voicebank and install it later.')
        name = self.v['db_name'].get()
        name_ok = bool(re.fullmatch(r'[A-Za-z0-9]+', name))
        if name_ok or not name:
            self.easy_name.configure(text='Letters and digits only (A-Z, 0-9). This is the name you pick in '
                                          'the editor.', style='Hint.TLabel')
        else:
            fixed = re.sub(r'[^A-Za-z0-9]+', '', name)
            self.easy_name.configure(style='Bad.TLabel', text='✘  Only letters and digits (A-Z, 0-9)%s.' % (
                ': try "%s"' % fixed if fixed else ''))
        if not self.runner.busy:
            self.easy_go.configure(state='normal' if (ready and name_ok) else 'disabled')
        return ready and name_ok

    @staticmethod
    def _missing_packages():
        if FROZEN:
            # Dependencies are bundled in Henkan.exe / HenkanWorker.exe; pip is not part of the app.
            return []
        import importlib.util
        need = ['numpy', 'customtkinter'] + (['pywinauto', 'win32gui'] if sys.platform == 'win32' else [])
        return [m for m in need if importlib.util.find_spec(m) is None]

    def _easy_install_packages(self):
        self.runner.start([sys.executable, '-m', 'pip', 'install', '-r', os.path.join(ROOT, 'requirements.txt')],
                          'Installing packages', done=lambda code: messagebox.showinfo(
                              'Packages', 'Installed. Close Henkan and open it again to use them.')
                          if code == 0 else None)

    def _easy_find_dbtool(self):
        self.pick_dbtool()
        if self.detected_lang in ('ja', 'en'):
            self._find_dict(self.detected_lang)
        self._easy_check()

    def _easy_free_db(self):
        """Point the DB at <devkit>\\Name\\Name, or Name2, Name3 … when that one was built before."""
        name = self.v['db_name'].get()
        base = os.path.dirname(self.v['dbtool'].get()) or DEVKIT
        for k in range(1, 100):
            n = name if k == 1 else '%s%d' % (name, k)
            db = os.path.join(base, n, n)
            if not os.path.exists(db + '.tree') and not os.path.exists(os.path.dirname(db)):
                return db
        return os.path.join(base, name, name)

    def easy_make(self):
        if self.runner.busy or not self._easy_check():
            return
        bank = self.v['bank'].get()
        safe = re.sub(r'[^\w\- ]+', '_', os.path.basename(os.path.normpath(bank))).strip() or 'bank'
        self.v['out'].set(os.path.join(os.path.dirname(self.v['dbtool'].get()) or DEVKIT, safe + '_seg'))
        self.v['db_path'].set(self._easy_free_db())
        for k in ('pkg_db', 'pkg_out', 'pkg_compid', 'pkg_dest'):
            self.v[k].set('')
        self.v['pkg_name'].set(self.v['db_name'].get())
        self.glist.selection_clear(0, 'end')                     # every folder
        for key in self.easy_marks:
            self._easy_mark(key, 'todo')
        self.easy_install.configure(state='disabled')
        self.easy_done.set('Working… you can keep using the PC.')
        self._easy_next('convert')

    def _easy_mark(self, key, state):
        mark, lb = self.easy_marks[key]
        glyph, style = {'todo': ('○', 'Wait.TLabel'), 'run': ('●', 'Num.TLabel'),
                        'ok': ('✔', 'Good.TLabel'), 'bad': ('✘', 'Bad.TLabel')}[state]
        mark.configure(text=glyph, style=style if state != 'run' else 'Bad.TLabel')
        lb.configure(style=style if state != 'run' else 'Easy.TLabel')

    def _easy_next(self, key):
        self._easy_run = key
        self._easy_mark(key, 'run')
        self.easy_go.configure(state='disabled')
        nxt = {'convert': lambda: self._easy_after('convert', 'build'),
               'build': lambda: self._easy_after('build', 'pack'),
               'pack': lambda: self._easy_after('pack', None)}[key]
        {'convert': lambda: self.convert(then=nxt), 'build': lambda: self.build(then=nxt),
         'pack': lambda: self.pack(then=nxt)}[key]()
        if not self.runner.busy:                     # the step said no (a warning box) before it started
            self._easy_mark(key, 'bad')
            self._easy_run = None
            self._easy_check()

    def _easy_after(self, done, nxt):
        self._easy_mark(done, 'ok')
        if nxt:
            self.after(10, lambda: self._easy_next(nxt))
            return
        self._easy_run = None
        self._easy_check()
        self.easy_install.configure(state='normal')
        self.easy_done.set('Your voicebank "%s" is ready. Press Install, then press Yes on the Windows '
                           'prompt.' % self.v['pkg_name'].get())
        self.log('Voicebank ready: %s\n' % self.package_dir(), 'ok')

    def step_ended(self, code):
        """Every finished step comes here; a failed Easy step explains itself in plain words."""
        if code == 0 or not self._easy_run:
            return
        from u2v import explain
        key, self._easy_run = self._easy_run, None
        self._easy_mark(key, 'bad')
        self._easy_check()
        self.easy_done.set('Stopped at: %s.' % dict(self.EASY_STEPS)[key].split(' (')[0])
        what, fix = explain.explain(''.join(self._step_log))
        messagebox.showwarning('Henkan: something went wrong', '%s\n\n%s' % (what, fix))

    def easy_do_install(self):
        self._fill_pkg_dest(self._editor_pick() or {})
        self.install()
        self.easy_done.set('After the Windows prompt and its window finish, open your VOCALOID editor: '
                           'the singer is called "%s".' % self.v['pkg_name'].get())

    def _header(self):
        from u2v import __version__
        h = ctk.CTkFrame(self, height=76, fg_color='#0d1118', corner_radius=0)
        h.pack(fill='x')
        h.pack_propagate(False)
        mark = ctk.CTkFrame(h, width=48, height=48, fg_color=ACCENT, corner_radius=13)
        mark.pack(side='left', padx=(18, 12), pady=14)
        mark.pack_propagate(False)
        ctk.CTkLabel(mark, text='変', text_color='#ffffff',
                     font=('Yu Gothic UI', 21, 'bold')).place(relx=0.5, rely=0.48, anchor='center')
        title_box = ctk.CTkFrame(h, fg_color='transparent')
        title_box.pack(side='left', pady=12)
        ctk.CTkLabel(title_box, text='Henkan', text_color='#ffffff',
                     font=(UI_FONT_BOLD, 17)).pack(anchor='w')
        ctk.CTkLabel(title_box, text='UTAU → VOCALOID  ·  recording-based conversion',
                     text_color=TEXT_MUTED, font=(UI_FONT, 9)).pack(anchor='w', pady=(1, 0))
        ctk.CTkLabel(h, text='v' + __version__, text_color=TEXT_MUTED,
                     font=(UI_FONT, 9)).pack(side='right', padx=20)

    def _row(self, parent, r, label, var, browse=None, width=None, browse_text='Browse…'):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky='w', pady=3)
        e = ttk.Entry(parent, textvariable=var, width=width or 60)
        e.grid(row=r, column=1, sticky='ew', padx=6, pady=3)
        if browse:
            ttk.Button(parent, text=browse_text, command=browse).grid(row=r, column=2, pady=3)
        return e

    @staticmethod
    def _hint(parent, r, text, column=1, columnspan=1, **kw):
        lb = ttk.Label(parent, text=text, style='Hint.TLabel', justify='left', **kw)
        lb.grid(row=r, column=column, columnspan=columnspan, sticky='w', padx=6)
        return lb

    def _section(self, parent, row, key, title):
        opened = key in self.v['sections'].get().split(',')
        s = Section(parent, title, opened, on_toggle=self._sections_changed)
        s.key = key
        s.grid(row=row, column=0, sticky='ew', pady=(10, 0))
        self.sections[key] = s
        return s

    def _sections_changed(self):
        self.v['sections'].set(','.join(k for k, s in self.sections.items() if s.opened))

    def _tab_convert(self, nb):
        f = ttk.Frame(nb, padding=10)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)

        left = ttk.Frame(f)
        left.grid(row=0, column=0, sticky='nsw')
        ttk.Label(left, text='Folders / suffixes', style='Sec.TLabel').pack(anchor='w')
        ttk.Label(left, text='none selected = all', style='Hint.TLabel').pack(anchor='w')
        box = ttk.Frame(left)
        box.pack(fill='both', expand=True, pady=4)
        self.glist = tk.Listbox(box, selectmode='extended', height=9, width=30, exportselection=False,
                                activestyle='none', relief='flat', borderwidth=0, highlightthickness=1,
                                highlightbackground=BORDER, highlightcolor=ACCENT, background=FIELD_BG,
                                foreground=TEXT, selectbackground=ACCENT, selectforeground='#ffffff',
                                font=(UI_FONT, 10))
        gsb = ttk.Scrollbar(box, command=self.glist.yview)
        self.glist.configure(yscrollcommand=gsb.set)
        self.glist.pack(side='left', fill='both', expand=True)
        gsb.pack(side='right', fill='y')
        pick = ttk.Frame(left)
        pick.pack(fill='x')
        ttk.Button(pick, text='All', width=6, command=lambda: self.glist.selection_set(0, 'end')).pack(side='left')
        ttk.Button(pick, text='None', width=6,
                   command=lambda: self.glist.selection_clear(0, 'end')).pack(side='left', padx=6)
        ttk.Button(left, text='Pitch & Append…', command=self._edit_pitch_assignments).pack(
            fill='x', pady=(8, 0))

        sf = ScrollFrame(f)
        sf.grid(row=0, column=1, sticky='nsew', padx=(16, 0))
        r = sf.body
        r.columnconfigure(0, weight=1)

        out = ttk.Frame(r)
        out.grid(row=0, column=0, sticky='ew')
        out.columnconfigure(1, weight=1)
        self._row(out, 0, 'Output folder', self.v['out'], self.pick_out, width=40)

        o = ttk.Frame(r)
        o.grid(row=1, column=0, sticky='w', pady=(4, 0))
        ttk.Label(o, text='Language').grid(row=0, column=0, sticky='w', pady=2)
        ttk.Combobox(o, textvariable=self.v['lang'], values=('auto', 'ja', 'en'), width=7,
                     state='readonly').grid(row=0, column=1, sticky='w', padx=(6, 24))
        ttk.Label(o, text='Voice colours').grid(row=0, column=2, sticky='w')
        ttk.Combobox(o, textvariable=self.v['colors'], values=('main', 'evec', 'merge'), width=7,
                     state='readonly').grid(row=0, column=3, sticky='w', padx=6)
        ttk.Label(o, text='Samples per unit').grid(row=1, column=0, sticky='w', pady=2)
        ttk.Spinbox(o, textvariable=self.v['keep'], from_=1, to=5, width=6).grid(row=1, column=1, sticky='w',
                                                                                 padx=(6, 24))
        ttk.Label(o, text='ヴ sings as').grid(row=1, column=2, sticky='w')
        ttk.Combobox(o, textvariable=self.v['v_mode'], values=V_MODE_LABELS, width=30,
                     state='readonly').grid(row=1, column=3, sticky='w', padx=6)

        self.sections = {}
        s = self._section(r, 2, 'processing', 'Processing').body
        checks = [('splice', 'Fake missing transitions (CV legato)'), ('fill', 'Fill missing units'),
                  ('normalize', 'Even out loudness'), ('derive', 'Units from silence'),
                  ('extend_dict', "Add b, b', p\\' to dictionary"), ('merge_groups', 'Treat folders as one set'),
                  ('triphones', 'Prefer matching VCV oto.ini aliases (triphones)')]
        for i, (k, t) in enumerate(checks):
            ttk.Checkbutton(s, text=t, variable=self.v[k]).grid(row=i // 2, column=i % 2, sticky='w',
                                                                padx=(0, 24), pady=1)

        s = self._section(r, 3, 'extras', 'Extras & transitions').body
        s.columnconfigure(1, weight=1)
        ttk.Checkbutton(s, text='Vowel endings  ("a R" → [a Sil])', variable=self.v['extras']).grid(
            row=0, column=0, columnspan=3, sticky='w')
        self._row(s, 1, 'Custom extras', self.v['custom_extras'], self._edit_custom, width=30,
                  browse_text='Edit…')
        self._hint(s, 2, 'Any sound in the bank as a phoneme of your own: breaths, a rolled r, a laugh …\n'
                         'An alias that starts with a vowel keeps its recorded transition, so a R, i R … → hh\n'
                         'gives every vowel its own way in. Edit… shows which transitions are recorded.')
        ttk.Label(s, text='Import VQM samples').grid(row=3, column=0, sticky='w', pady=(8, 3))
        ttk.Entry(s, textvariable=self.v['growl_wav'], width=60).grid(
            row=3, column=1, sticky='ew', padx=6, pady=(8, 3))
        growl_browse = ttk.Frame(s)
        growl_browse.grid(row=3, column=2, sticky='w', pady=(8, 3))
        ttk.Button(growl_browse, text='Browse folder…', command=self.pick_vqm_folder).pack(side='left')
        ttk.Button(growl_browse, text='Browse one WAV…', command=self.pick_growl_wav).pack(side='left', padx=(4, 0))
        ttk.Button(growl_browse, text='Clear', command=lambda: self.v['growl_wav'].set('')).pack(
            side='left', padx=(4, 0))
        self._hint(s, 4, 'Optional: select the commercial voicebank root (art/ sta/ vqm/), the vqm/ folder, '
                         'or one WAV. Henkan imports every numbered vqm_N.wav in numeric order and preserves '
                         'each N in the native V4 VQM records. art/ and sta/ are not copied: those are built '
                         'from the UTAU bank being converted.')

        s = self._section(r, 4, 'colours', 'Voice colours (EVEC)').body
        s.columnconfigure(1, weight=1)
        ttk.Label(s, textvariable=self.evec_info, style='Info.TLabel', wraplength=560,
                  justify='left').grid(row=0, column=0, columnspan=3, sticky='w')
        self.evec_box = ttk.Frame(s)
        self.evec_box.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Button(s, text='Auto', command=self._auto_evec).grid(row=1, column=2, sticky='ne', pady=(4, 0))
        ttk.Label(s, textvariable=self.v['evec_map'], style='Hint.TLabel', wraplength=560).grid(
            row=2, column=0, columnspan=3, sticky='w', pady=(2, 0))
        self._hint(s, 3, 'The first colour is the main voice (no suffix). skip drops a colour; '
                         'a # number switches Voice colours to evec.', column=0, columnspan=3)
        ttk.Checkbutton(s, text='EVEC support mode: only vowels get #numbers (consonants shared, easy crossfades)',
                        variable=self.v['evec_vowels']).grid(row=4, column=0, columnspan=3, sticky='w', pady=(6, 0))
        self._row(s, 5, 'EVEC phonemes', self.v['evec_phonemes'], width=30)
        self._hint(s, 6, 'e.g.  Soft:a,i,M,e,o; Whisper:a,e   (empty = all; a colour with no vowels '
                         'warns but still converts)')

        s = self._section(r, 5, 'tuning', 'Fine-tuning').body
        s.columnconfigure(1, weight=1)
        self._row(s, 0, 'Brighten', self.v['brighten'], width=30)
        self._hint(s, 1, 'e.g.  o=3   (dB of treble for a dull vowel; several: o=3, a=1)')
        self._row(s, 2, 'Avoid units', self.v['avoid'], width=30)
        self._hint(s, 3, "e.g.  g' i; k o   (rebuild these from other recordings)")

        b = ttk.Frame(f)
        b.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        ttk.Button(b, text='Convert', style='Accent.TButton', command=self.convert).pack(side='left')
        ttk.Button(b, text='Validate', command=self.validate).pack(side='left', padx=6)
        ttk.Button(b, text='Open report', command=lambda: self.open_file(os.path.join(self.v['out'].get(),
                                                                                       'report.txt'))).pack(side='left')
        ttk.Button(b, text='Audio QA', command=self.audio_qa).pack(side='left', padx=6)
        ttk.Button(b, text='Open folder', command=lambda: self.open_file(self.v['out'].get())).pack(side='left', padx=6)

        for k in ('splice', 'fill', 'normalize', 'derive', 'extend_dict', 'merge_groups', 'triphones', 'extras', 'custom_extras', 'evec_map', 'evec_vowels', 'evec_phonemes', 'colors', 'brighten', 'avoid'):
            self.v[k].trace_add('write', lambda *_a: self._summaries())
        self._summaries()
        return f

    def _summaries(self):
        """One-line state of each Convert section, shown next to its title (so closed sections still say
        what they will do)."""
        if not getattr(self, 'sections', None):
            return
        v = self.v
        names = {'splice': 'legato', 'fill': 'fill', 'normalize': 'loudness', 'derive': 'silence units',
                 'extend_dict': "b/b'/p\\'", 'merge_groups': 'one set'}
        off = [n for k, n in names.items() if k != 'merge_groups' and not v[k].get()]
        bits = ['all on' if not off else 'off: ' + ', '.join(off)]
        if v['merge_groups'].get():
            bits.append('folders merged')
        if v['triphones'].get():
            bits.append('triphones')
        self.sections['processing'].summary.set(' · '.join(bits))
        try:
            from u2v import select as _SelS
            phs = sorted({ph for ph, _v, _g in _SelS.parse_custom_extras(v['custom_extras'].get()).values()})
        except ValueError:
            phs = ['?']
        self.sections['extras'].summary.set('vowel endings %s · %s' % (
            'on' if v['extras'].get() else 'off', ', '.join(phs) if phs else 'no custom extras'))
        n_col = len(getattr(self, 'color_rows', []) or [])
        col = '%s' % v['colors'].get()
        if v['evec_vowels'].get():
            col += ', vowels only'
        self.sections['colours'].summary.set('%s · %d colour%s found' % (col, n_col, '' if n_col == 1 else 's')
                                             if n_col else col)
        tune = []
        if v['brighten'].get().strip():
            tune.append('brighten ' + v['brighten'].get().strip())
        n_av = len([u for u in v['avoid'].get().split(';') if u.strip()])
        if n_av:
            tune.append('avoid %d unit%s' % (n_av, '' if n_av == 1 else 's'))
        self.sections['tuning'].summary.set(' · '.join(tune) or 'none')

    def _intro(self, f, text):
        ttk.Label(f, text=text, style='Info.TLabel', wraplength=820, justify='left').grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))

    @staticmethod
    def _group(f, row, title):
        g = ttk.LabelFrame(f, labelwidget=ttk.Label(f, text=' %s ' % title, style='Sec.TLabel'), padding=(10, 6))
        g.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(0, 10))
        g.columnconfigure(1, weight=1)
        return g

    def _tab_build(self, nb):
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(0, weight=1)
        self._intro(f, 'Builds the singer DB from the converted folder with VocaloidDBTool3. It takes a few '
                       'minutes. The DBTool is driven automatically, out of sight on a hidden desktop, so you can '
                       'keep using the PC; if it gets stuck there, it runs again where you can see it.')
        g = self._group(f, 1, 'Singer DB')
        self._row(g, 0, 'Singer name', self.v['db_name'], width=30)
        self._row(g, 1, 'New DB (folder\\Name)', self.v['db_path'], self.pick_db)
        ttk.Label(g, text='Language').grid(row=2, column=0, sticky='w', pady=3)
        ttk.Combobox(g, textvariable=self.v['db_lang'], values=('Japanese', 'English', 'Spanish'), width=12,
                     state='readonly').grid(row=2, column=1, sticky='w', padx=6)
        g = self._group(f, 2, 'Devkit')
        self._row(g, 0, 'VocaloidDBTool3.exe', self.v['dbtool'], self.pick_dbtool)
        ttk.Checkbutton(g, text='Run the DBTool in the background (hidden)', variable=self.v['dbtool_hidden']).grid(
            row=1, column=1, sticky='w', padx=6, pady=(2, 0))
        self._hint(g, 2, 'Untick to watch it work. Leave it alone then: it clicks itself.')
        b = ttk.Frame(f)
        b.grid(row=3, column=0, sticky='w')
        ttk.Button(b, text='Build DB', style='Accent.TButton', command=self.build).pack(side='left')
        ttk.Button(b, text='Convert + Build', command=lambda: self.convert(then=self.build)).pack(side='left', padx=6)
        ttk.Button(b, text='Preflight / Validate', command=self.validate).pack(side='left', padx=6)
        ttk.Button(b, text='Audio QA', command=self.audio_qa).pack(side='left')
        return f

    def _tab_pack(self, nb):
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(0, weight=1)
        self._intro(f, 'Packs the built DB into a voicebank (.ddb + .ddi + .vvd) and installs it into any '
                       'VOCALOID editor on this PC.')
        g = self._group(f, 1, 'Package')
        self._row(g, 0, 'Built DB (folder\\Name)', self.v['pkg_db'], self.pick_built)
        self._row(g, 1, 'Voice name', self.v['pkg_name'], width=30)
        self._row(g, 2, 'CompID', self.v['pkg_compid'], self.gen_compid, browse_text='Generate…')
        self._hint(g, 3, '16 capital letters/digits, unique per voicebank (checked against this PC)')
        self._row(g, 4, 'Package folder', self.v['pkg_out'], self.pick_pkg_out)
        ttk.Label(g, text='Singer Editor voice offsets').grid(row=5, column=0, sticky='w', pady=(8, 3))
        profile = ttk.Frame(g)
        profile.grid(row=5, column=1, columnspan=2, sticky='w', padx=6, pady=(8, 3))
        for col, (label, key) in enumerate((('BRE', 'pkg_breathiness'), ('BRI', 'pkg_brightness'),
                                            ('CLE', 'pkg_clearness'), ('GEN', 'pkg_gender_factor'))):
            cell = ttk.Frame(profile)
            cell.pack(side='left', padx=(0, 12))
            ttk.Label(cell, text=label).pack(side='left')
            ttk.Spinbox(cell, from_=-127, to=127, increment=1, textvariable=self.v[key], width=5).pack(
                side='left', padx=(4, 0))
        self._hint(g, 6, 'Signed singer-level offsets (-127..127). Zero is neutral. Opening (OPE) and Velocity '
                         '(VEL) are note-level controls, so they are tested in the generated V4 parameter probe, '
                         'not stored as singer offsets here.')
        self._hint(g, 7, 'If Convert contains numbered VQM WAVs, Pack turns each Grw stationary into its native VQM '
                         'record and preserves the vqm_N index. Without imported VQM audio, Growl stays disabled.')
        b = ttk.Frame(g)
        b.grid(row=8, column=1, sticky='w', pady=(8, 2), padx=6)
        ttk.Button(b, text='Pack voicebank', style='Accent.TButton', command=self.pack).pack(side='left')
        ttk.Button(b, text='Open package', command=lambda: self.open_file(self.package_dir())).pack(
            side='left', padx=6)
        g = self._group(f, 2, 'Install')
        ttk.Label(g, text='Install into').grid(row=0, column=0, sticky='w', pady=3)
        self.editor_box = ttk.Combobox(g, textvariable=self.v['pkg_editor'], width=57, state='readonly')
        self.editor_box.grid(row=0, column=1, sticky='ew', padx=6)
        self.editor_box['values'] = self._editors()
        ttk.Button(g, text='Browse…', command=self.pick_editor).grid(row=0, column=2, pady=3)
        self._row(g, 1, 'Bank folder', self.v['pkg_dest'], self.pick_pkg_dest)
        self._hint(g, 2, 'Where the .ddb/.ddi/.vvd live (VOCALOID4 banks: VoiceDB\\<name>)')
        ttk.Checkbutton(g, text='Dev editor: register a DB_Dev.ini voice slot instead (VOCALOID4FE)',
                        variable=self.v['pkg_devini']).grid(row=3, column=1, sticky='w', padx=6, pady=3)
        b2 = ttk.Frame(g)
        b2.grid(row=4, column=1, sticky='w', pady=(6, 2), padx=6)
        ttk.Button(b2, text='Install', style='Accent.TButton', command=self.install).pack(side='left')
        ttk.Button(b2, text='Uninstall', command=self.uninstall).pack(side='left', padx=6)
        self._hint(g, 5, 'Install/Uninstall touch Program Files and the registry, so Windows asks for '
                         'permission (UAC) first.')
        return f

    def _tab_v3v2(self, nb):
        """V3→V2 backport: convert VOCALOID3/4 banks (or a Pack & Install .tree) for VOCALOID2."""
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)
        self._intro(f, 'VOCALOID2 cannot read V3/V4 banks: the index (.ddi) is rewritten the way its engine '
                       'reads it. Converted singers live in Documents\\VOCALOID2 Singers.')
        g = self._group(f, 1, 'Convert')
        ttk.Label(g, text='Voicebank').grid(row=0, column=0, sticky='w', pady=3)
        self.v3v2_box = ttk.Combobox(g, textvariable=self.v['v3v2_src'], width=57, state='readonly')
        self.v3v2_box.grid(row=0, column=1, sticky='ew', padx=6, pady=3)
        self.v3v2_box.bind('<<ComboboxSelected>>', lambda _e: self._v3v2_picked_source())
        ttk.Button(g, text='Browse…', command=self._v3v2_browse).grid(row=0, column=2, pady=3)
        self._row(g, 1, 'Name in VOCALOID2', self.v['v3v2_name'])
        ttk.Button(g, text='Convert', style='Accent.TButton', command=self._v3v2_convert).grid(
            row=2, column=1, sticky='w', padx=6, pady=(6, 2))
        g = self._group(f, 2, 'Converted singers')
        g.grid_configure(sticky='nsew')
        g.rowconfigure(0, weight=1)
        g.columnconfigure(0, weight=1)
        g.columnconfigure(1, weight=0)
        self.v3v2_lib = tk.Listbox(g, height=6, exportselection=False, activestyle='none', relief='flat',
                                   borderwidth=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                                   background=FIELD_BG, foreground=TEXT, selectbackground=ACCENT,
                                   selectforeground='#ffffff', font=(UI_FONT, 10))
        self.v3v2_lib.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        b = ttk.Frame(g)
        b.grid(row=0, column=1, sticky='n')
        ttk.Label(b, text='Language').pack(anchor='w')
        ttk.Combobox(b, textvariable=self.v['v3v2_lang'], values=('Auto', 'Japanese', 'English'),
                     width=20, state='readonly').pack(fill='x', pady=(0, 6))
        ttk.Button(b, text='Install in VOCALOID2', command=self._v3v2_install).pack(fill='x', pady=2)
        ttk.Button(b, text='Uninstall', command=self._v3v2_uninstall).pack(fill='x', pady=2)
        ttk.Button(b, text='Delete singer', command=self._v3v2_delete).pack(fill='x', pady=2)
        ttk.Button(b, text='Refresh', command=self._v3v2_refresh_singers).pack(fill='x', pady=2)
        self._v3v2_sources, self._v3v2_picked, self._v3v2_library = [], None, []
        self._v3v2_busy = False
        self._v3v2_refresh_sources()
        self._v3v2_refresh_singers()
        return f

    @staticmethod
    def _v3v2_api():
        """The vendored V3V2 engine (needs Windows for the registry parts)."""
        try:
            from v3v2 import convert, install, sources, tree
        except ImportError as ex:
            messagebox.showwarning('V3→V2', 'The backport engine needs Windows: %s' % ex)
            return None
        return convert, install, sources, tree

    def _v3v2_refresh_sources(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, _install, sources, _tree = api
        try:
            self._v3v2_sources = sources.installed()
        except OSError:
            self._v3v2_sources = []
        self.v3v2_box['values'] = (['%s  (installed)' % s.name for s in self._v3v2_sources]
                                   + [V3V2_BROWSE_FOLDER, V3V2_BROWSE_TREE, V3V2_PACK_DB])

    def _v3v2_refresh_singers(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, install, sources, _tree = api
        self._v3v2_library = sources.library()
        try:
            done = install.installed()
        except OSError:
            done = {}
        self._v3v2_installed = done
        self.v3v2_lib.delete(0, 'end')
        for name, folder, _info in self._v3v2_library:
            e = done.get(os.path.abspath(folder))
            self.v3v2_lib.insert('end', '%s   [installed, %s]' % (name, install.LANGUAGES[e['language']])
                                 if e else name)

    def _v3v2_take_source(self, src):
        api = self._v3v2_api()
        _convert, _install, sources, _tree = api
        self._v3v2_picked = src
        self.v['v3v2_name'].set(sources.safe_name(src.name))

    def _v3v2_picked_source(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, _install, sources, _tree = api
        i = self.v3v2_box.current()
        if i < 0:
            return
        if i < len(self._v3v2_sources):
            self._v3v2_take_source(self._v3v2_sources[i])
        elif self.v['v3v2_src'].get() == V3V2_BROWSE_TREE:
            path = filedialog.askopenfilename(title='Developer-kit singer', filetypes=[('Singer tree', '*.tree')])
            if path:
                self._v3v2_classify(path)
        elif self.v['v3v2_src'].get() == V3V2_PACK_DB:
            db = self.v['pkg_db'].get().rstrip('\\/')
            tree_path = db + '.tree'
            if not db or not os.path.isfile(tree_path):
                messagebox.showinfo('V3→V2', 'The Pack & Install page has no built DB yet — build one first.')
                return
            self._v3v2_classify(tree_path)
        else:
            path = filedialog.askdirectory(title='Voicebank folder (holds a .ddi and a .ddb)')
            if path:
                self._v3v2_classify(path)

    def _v3v2_classify(self, path):
        from v3v2 import sources
        try:
            src = sources.classify(path)
        except ValueError as ex:
            messagebox.showwarning('Cannot use this', str(ex))
            return
        self.v['v3v2_src'].set('%s  (%s)' % (src.name, src.folder))
        self._v3v2_take_source(src)

    def _v3v2_browse(self):
        path = filedialog.askdirectory(title='Voicebank folder (holds a .ddi and a .ddb)')
        if path:
            self._v3v2_classify(path)

    def _v3v2_convert(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, install, sources, _tree = api
        if self._v3v2_busy or self.runner.busy:
            messagebox.showinfo('Busy', 'Wait for the current step to finish.')
            return
        src = self._v3v2_picked
        if src is None:
            messagebox.showinfo('V3→V2', 'Pick a voicebank first.')
            return
        name = sources.safe_name(self.v['v3v2_name'].get() or src.name)
        out = os.path.join(sources.LIBRARY, name)
        if os.path.exists(out):
            if os.path.abspath(out) in getattr(self, '_v3v2_installed', {}):
                messagebox.showwarning('V3→V2', '%s is installed in VOCALOID2. Uninstall it first, '
                                                'or pick another name.' % name)
                return
            if not messagebox.askyesno('V3→V2', '%s already exists. Replace it?' % name):
                return
        self._v3v2_busy = True
        self.set_busy('Converting voicebank')
        self.log('\n$ v3v2 convert %s -> %s\n' % (src.name, out), 'cmd')
        threading.Thread(target=self._v3v2_job, args=(api, src, out, name), daemon=True).start()

    def _v3v2_job(self, api, src, out, name):
        convert, _install, sources, tree = api
        vlog = lambda msg, tag=None: self.after(0, self.log, msg + '\n', tag)  # noqa: E731
        try:
            os.makedirs(out, exist_ok=True)
            for fn in os.listdir(out):
                if fn.lower().endswith(('.ddi', '.ddb', '.vvd')):
                    os.remove(os.path.join(out, fn))
            if src.kind == 'tree':
                root = tree.pack(src.folder, os.path.join(out, name + '.ddb'), vlog)
                data, rep = convert.root_to_v2(root)
                with open(os.path.join(out, name + '.ddi'), 'wb') as fh:
                    fh.write(data)
                for line in rep.lines():
                    vlog(line)
            else:
                rep = convert.convert_bank(src.folder, out, name, vlog)
            sources.write_info(out, name, src, rep.lines())
            vlog('%s is ready. Install it in VOCALOID2 below.' % name, 'ok')
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            vlog('Failed: %s: %s' % (type(ex).__name__, ex), 'err')
        self.after(0, self._v3v2_done)

    def _v3v2_done(self):
        self._v3v2_busy = False
        self.set_busy(None)
        self._v3v2_refresh_singers()

    def _v3v2_selected(self):
        sel = self.v3v2_lib.curselection()
        if not sel or sel[0] >= len(self._v3v2_library):
            messagebox.showinfo('V3→V2', 'Select a converted singer first.')
            return None
        return self._v3v2_library[sel[0]]

    def _v3v2_language(self, folder):
        from v3v2 import install
        choice = self.v['v3v2_lang'].get()
        if choice == 'Japanese':
            return 0
        if choice == 'English':
            return 1
        ddis = [f for f in os.listdir(folder) if f.lower().endswith('.ddi')]
        try:
            return install.detect_language(os.path.join(folder, ddis[0]))
        except Exception:                                          # noqa: BLE001 - default Japanese
            return 0

    def _v3v2_install(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, install, _sources, _tree = api
        bank = self._v3v2_selected()
        if not bank:
            return
        name, folder, _info = bank
        if os.path.abspath(folder) in getattr(self, '_v3v2_installed', {}):
            messagebox.showinfo('V3→V2', '%s is already installed.' % name)
            return
        language = self._v3v2_language(folder)
        self.log('Installing %s as a %s singer (Windows will ask for administrator rights)...'
                 % (name, install.LANGUAGES[language]))
        self.update()
        try:
            install.install(folder, name, language)
        except install.InstallError as ex:
            self.log('Install failed: %s' % ex, 'err')
            messagebox.showwarning('Install', str(ex))
            return
        self.log('%s is installed. Open VOCALOID2 and pick "%s" as the singer.' % (name, name), 'ok')
        self._v3v2_refresh_singers()

    def _v3v2_uninstall(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, install, _sources, _tree = api
        bank = self._v3v2_selected()
        if not bank:
            return
        name, folder, _info = bank
        if os.path.abspath(folder) not in getattr(self, '_v3v2_installed', {}):
            messagebox.showinfo('V3→V2', '%s is not installed.' % name)
            return
        self.update()
        try:
            install.uninstall(folder)
        except install.InstallError as ex:
            self.log('Uninstall failed: %s' % ex, 'err')
            messagebox.showwarning('Uninstall', str(ex))
            return
        self.log('%s is removed from VOCALOID2 (the converted files are still here).' % name, 'ok')
        self._v3v2_refresh_singers()

    def _v3v2_delete(self):
        bank = self._v3v2_selected()
        if not bank:
            return
        name, folder, _info = bank
        if os.path.abspath(folder) in getattr(self, '_v3v2_installed', {}):
            messagebox.showwarning('V3→V2', '%s is installed in VOCALOID2. Uninstall it first.' % name)
            return
        if not messagebox.askyesno('V3→V2', 'Delete the converted singer %s?\n(The original voicebank is '
                                             'not touched.)' % name):
            return
        for fn in os.listdir(folder):
            os.remove(os.path.join(folder, fn))
        os.rmdir(folder)
        self.log('Deleted %s.\n' % name)
        self._v3v2_refresh_singers()

    # ---------------------------------------------------------------- helpers
    def log(self, s, tag=None):
        if tag is None and re.search(r'WARNING|warning:', s):
            tag = 'warn'
        if self.runner.busy:
            self._step_log.append(s)
        self.text.insert('end', s, tag)
        self.text.see('end')

    def clear_log(self):
        self.text.delete('1.0', 'end')

    def set_busy(self, title):
        if title:
            self._step_log = []
            self._busy = (title, time.monotonic())
            self.prog.configure(mode='indeterminate')
            self.prog.start(12)
            self.stop_btn.configure(state='normal')
            self._tick()
        else:
            took = time.monotonic() - self._busy[1] if getattr(self, '_busy', None) else 0
            self._busy = None
            self.status.set('Ready' + ('  (last step took %s)' % self._clock(took) if took >= 1 else ''))
            self.prog.stop()
            self.prog.configure(mode='determinate', value=0)
            self.stop_btn.configure(state='disabled')

    @staticmethod
    def _clock(sec):
        return '%d:%02d' % divmod(int(sec), 60)

    def _tick(self):
        """Status line with the running step's elapsed time, updated every second."""
        if not getattr(self, '_busy', None):
            return
        title, t0 = self._busy
        self.status.set('%s…  %s' % (title, self._clock(time.monotonic() - t0)))
        if getattr(self, '_tick_id', None):
            self.after_cancel(self._tick_id)
        self._tick_id = self.after(1000, self._tick)

    @staticmethod
    def open_file(path):
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showinfo('Not found', '%s does not exist yet.' % (path or 'That'))

    def _py(self, script, *args):
        if FROZEN:
            worker = os.path.join(APP_DIR, 'HenkanWorker.exe')
            worker_runtime = os.path.join(APP_DIR, '_henkan_worker_runtime')
            if not os.path.isfile(worker) or not os.path.isdir(worker_runtime):
                messagebox.showerror(
                    'Henkan worker files missing',
                    'HenkanWorker.exe and its _henkan_worker_runtime folder must be beside Henkan.exe.\n\n'
                    'Re-extract the complete Henkan-Windows package.')
                return []
            mode = '--qa' if os.path.basename(script).lower() == 'qa.py' else '--cli'
            return [worker, mode] + [str(a) for a in args]
        return [sys.executable, '-I', '-u', script] + [str(a) for a in args]

    # ------------------------------------------------------------------ pick
    def pick_bank(self):
        d = filedialog.askdirectory(title='UTAU voicebank folder', initialdir=self.v['bank'].get() or None)
        if d:
            self.v['bank'].set(os.path.normpath(d))
            name = os.path.basename(os.path.normpath(d))
            safe = re.sub(r'[^\w\- ]+', '_', name).strip() or 'bank'
            self.v['out'].set(os.path.join(DEVKIT, safe + '_seg'))
            ascii_name = re.sub(r'[^A-Za-z0-9]+', '', name)[:20] or 'Singer'
            self.v['db_name'].set(ascii_name)
            self.v['db_path'].set(os.path.join(DEVKIT, ascii_name, ascii_name))
            self.scan()
            self._easy_check()

    def pick_out(self):
        d = filedialog.askdirectory(title='Output folder for the DBTool files')
        if d:
            self.v['out'].set(os.path.normpath(d))

    def pick_db(self):
        d = filedialog.askdirectory(title='Folder to create the singer DB in')
        if d:
            name = self.v['db_name'].get() or 'Singer'
            self.v['db_path'].set(os.path.join(os.path.normpath(d), name, name))

    def pick_dict(self):
        p = filedialog.askopenfilename(title='VOCALOID phonetic dictionary (.txt from the devkit)',
                                       filetypes=[('Phonetic dictionary', '*.txt'), ('All files', '*.*')])
        if p:
            self.v['dict'].set(os.path.normpath(p))

    def _find_dict(self, lang):
        """Fill in the devkit dictionary for lang unless the user chose one for that language."""
        from u2v import dbtool_io
        cur = self.v['dict'].get()
        if cur and os.path.exists(cur) and dbtool_io.dictionary_language(cur) in (lang, None):
            return
        roots = [DEVKIT, os.path.dirname(self.v['dbtool'].get()), os.path.dirname(DEVKIT)]
        found = dbtool_io.find_dictionary(lang, roots)
        self.v['dict'].set(found or '')

    def pick_dbtool(self):
        p = filedialog.askopenfilename(title='VocaloidDBTool3.exe', filetypes=[('DBTool', '*.exe')])
        if p:
            self.v['dbtool'].set(os.path.normpath(p))

    def audio_qa(self):
        """Run the standalone converted-folder acoustic QA report."""
        out = self.v['out'].get().strip()
        if not os.path.isdir(out):
            return messagebox.showwarning('Audio QA', 'Choose an existing converted output folder first.')
        if not os.path.isfile(os.path.join(out, 'manifest.csv')):
            return messagebox.showwarning('Audio QA', 'No manifest.csv was found. Convert the bank first.')
        script = os.path.join(ROOT, 'tools', 'qa.py')
        self.runner.start(self._py(script, out), 'Audio QA')

    def show_oto_report(self):
        """Show an actionable OTO/alias report without starting a conversion."""
        bank = self.v['bank'].get().strip()
        if not os.path.isdir(bank):
            return messagebox.showwarning('OTO Inspector', 'Choose a UTAU voicebank folder first.')
        from u2v import oto, phonemes as P
        diagnostics = []
        try:
            entries = oto.read_bank(bank, diagnostics=diagnostics)
        except Exception as ex:  # noqa: BLE001
            return messagebox.showerror('OTO Inspector', 'Could not read the voicebank:\n%s' % ex)
        lang = self.detected_lang or P.detect_language([e.alias for e in entries]) or 'ja'
        group_counts = {}
        unknown = []
        for entry in entries:
            group_counts[entry.group] = group_counts.get(entry.group, 0) + 1
            mapped = P.extra_alias(entry.alias) or P.alias_to_phonemes(entry.alias, lang)
            if not mapped:
                unknown.append((entry.raw_alias, entry.alias, entry.wav))
        lines = [
            'HENKAN — OTO INSPECTOR',
            'Bank: %s' % bank,
            'Detected language: %s' % lang,
            'Usable OTO entries: %d' % len(entries),
            'OTO warnings: %d' % len(diagnostics),
            'Unmapped aliases: %d' % len(unknown),
            '', 'GROUPS']
        lines.extend('  %-35s %d entries' % (name or '(main)', count)
                     for name, count in sorted(group_counts.items()))
        lines += ['', 'UNMAPPED ALIASES (first 100)']
        lines.extend('  %s  ->  %s  [%s]' % (raw, alias, os.path.basename(wav))
                     for raw, alias, wav in unknown[:100])
        if not unknown:
            lines.append('  none')
        lines += ['', 'OTO WARNINGS (first 100)']
        lines.extend('  ' + message for message in diagnostics[:100])
        if not diagnostics:
            lines.append('  none')
        if len(unknown) > 100 or len(diagnostics) > 100:
            lines += ['', 'Display truncated. Full conversion diagnostics are also written to the conversion log.']
        top = ctk.CTkToplevel(self)
        top.title('OTO Inspector — Henkan')
        top.geometry('900x620')
        top.minsize(680, 420)
        top.transient(self)
        frame = ttk.Frame(top, padding=10)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='OTO / alias preflight', style='Head.TLabel').pack(anchor='w')
        ttk.Label(frame, text='Review unmapped aliases and malformed or missing WAV references before conversion.',
                  style='Hint.TLabel').pack(anchor='w', pady=(2, 8))
        body = ttk.Frame(frame)
        body.pack(fill='both', expand=True)
        view = tk.Text(body, wrap='none', font=('Consolas', 9), padx=8, pady=6,
                       background='#0d131c', foreground='#d8e1ed', insertbackground='#d8e1ed',
                       selectbackground='#5d3150', highlightthickness=1,
                       highlightbackground=BORDER, borderwidth=0)
        ys = ttk.Scrollbar(body, orient='vertical', command=view.yview)
        xs = ttk.Scrollbar(frame, orient='horizontal', command=view.xview)
        view.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        view.pack(side='left', fill='both', expand=True)
        ys.pack(side='right', fill='y')
        xs.pack(fill='x')
        view.insert('1.0', '\n'.join(lines))
        view.configure(state='disabled')
        foot = ttk.Frame(frame)
        foot.pack(fill='x', pady=(8, 0))
        ttk.Button(foot, text='Copy report', command=lambda: (top.clipboard_clear(), top.clipboard_append('\n'.join(lines))))\
            .pack(side='left')
        ttk.Button(foot, text='Close', command=top.destroy).pack(side='right')

    def scan(self):
        """List the bank's folders/suffixes and guess its language (reads oto.ini files only)."""
        bank = self.v['bank'].get()
        self.glist.delete(0, 'end')
        if not os.path.isdir(bank):
            return
        self.info.set('Reading oto.ini files…')
        self.update_idletasks()
        try:
            from u2v import oto, phonemes as P
            oto_diagnostics = []
            entries = oto.read_bank(bank, diagnostics=oto_diagnostics)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            self.info.set('Could not read the bank: %s' % ex)
            return
        if not entries:
            self.info.set('No oto.ini entries found in this folder.')
            return
        count = {}
        for e in entries:
            count[e.group] = count.get(e.group, 0) + 1
        self.groups = sorted(count.items())
        for g, n in self.groups:
            self.glist.insert('end', '%s   (%d)' % (g or '(main folder)', n))
        self.detected_lang = P.detect_language([e.alias for e in entries])
        lang = {'ja': 'Japanese', 'en': 'English'}.get(self.detected_lang, self.detected_lang)
        n_v = len(P.v_aliases([e.alias for e in entries])) if self.detected_lang == 'ja' else 0
        self.info.set('%d oto entries in %d folder/suffix group(s), looks %s%s%s' % (
            len(entries), len(count), lang, ', %d ヴ recordings' % n_v if n_v else '',
            ', %d OTO warning(s) — see OTO Inspector' % len(oto_diagnostics) if oto_diagnostics else ''))
        self._last_oto_diagnostics = oto_diagnostics
        if self.detected_lang in ('ja', 'en'):
            self._find_dict(self.detected_lang)
        if self.detected_lang == 'en':
            self.v['db_lang'].set('English')
        elif self.detected_lang == 'ja':
            self.v['db_lang'].set('Japanese')
        try:
            from u2v import select as _Sel, phonemes as _P
            _rows, _entries = _Sel.bank_colours(bank)
            _lang = 'en' if self.detected_lang == 'en' else 'ja'
            _vv = {}
            for _e in _entries:
                _ph = _P.extra_alias(_e.alias) or _P.alias_to_phonemes(_e.alias, _lang)
                if _ph:
                    for _p in _ph:
                        if _p in _P.VOWELS:
                            _vv.setdefault(_e.color, set()).add(_p)
            _bits = []
            for _c, _n, _pitches, _groups in _rows:
                _v = ''.join(sorted(_vv.get(_c, ()))) or 'no vowels!'
                _bits.append('%s (%d, vowels %s)' % (_c, _n, _v))
            _aff = _Sel.prefix_affixes(bank)
            self.color_rows = _rows
            self._build_evec_rows(_rows)
            self._summaries()
            self.evec_info.set('Colours: ' + '; '.join(_bits)
                               + (' | prefix.map: ' + ', '.join(_aff) if _aff else ''))
        except Exception:
            self.evec_info.set('')

    # ---------------------------------------------------------------- actions
    def _build_evec_rows(self, rows):
        """One label + number dropdown per colour. Preserves any previous picks."""
        if not hasattr(self, 'evec_box'):
            return
        for w in self.evec_box.winfo_children():
            w.destroy()
        prev = {}
        try:
            from u2v import select as _SelB
            if self.v['evec_map'].get().strip():
                prev = _SelB.parse_evec_map(self.v['evec_map'].get())
        except ValueError:
            prev = {}
        self.evec_pick, self.evec_pick_order = {}, []
        nums = ['main'] + ['#%d' % i for i in range(1, 10)] + ['skip']
        for i, (c, n, _p, _g) in enumerate(rows):
            if c in prev:
                v = prev[c]
            else:
                hit = [vv for k, vv in prev.items() if k.lower() in c.lower() or c.lower() in k.lower()]
                v = hit[0] if hit else ('' if i == 0 else '#%d' % min(i, 9))
            var = tk.StringVar(value='skip' if v is None else (v or 'main'))
            ttk.Label(self.evec_box, text='%s (%d)' % (c, n)).grid(row=i, column=0, sticky='w')
            ttk.OptionMenu(self.evec_box, var, var.get(), *nums,
                           command=self._sync_evec_map).grid(row=i, column=1, sticky='w', padx=6)
            self.evec_pick[c], self.evec_pick_order = var, self.evec_pick_order + [c]
        self._sync_evec_map()

    def _sync_evec_map(self, *_a):
        self.v['evec_map'].set(', '.join('%s=%s' % (c, self.evec_pick[c].get())
                                         for c in self.evec_pick_order))

    def _auto_evec(self):
        rows = getattr(self, 'color_rows', None)
        if not rows:
            from u2v import select as _SelA
            bank = self.v['bank'].get()
            if not os.path.isdir(bank):
                return messagebox.showwarning('EVEC', 'Choose a bank first.')
            try:
                rows, _e = _SelA.bank_colours(bank)
            except Exception as ex:                                # noqa: BLE001 - show, don't crash
                return messagebox.showwarning('EVEC', 'Could not read colours: %s' % ex)
            if rows:
                self.color_rows = rows
        if not rows:
            return messagebox.showinfo('EVEC', 'No colours detected.')
        self.v['evec_map'].set('')
        self._build_evec_rows(rows)
        if self.v['colors'].get() == 'main':
            self.v['colors'].set('evec')
        self.log('EVEC map: %s\n' % self.v['evec_map'].get())

    def _edit_custom(self):
        """Custom extras & transitions: tick aliases, name their phoneme (several may share one), pick its
        group, and see per phoneme which vowel transitions come from recordings and which get spliced."""
        bank = self.v['bank'].get()
        if not os.path.isdir(bank):
            return messagebox.showwarning('Custom extras', 'Choose a bank first.')
        from u2v import phonemes as _P, select as _S
        lang = 'en' if (self.v['lang'].get() == 'en' or self.detected_lang == 'en') else 'ja'
        try:
            rows = _S.alias_rows(bank, lang)
            cur = _S.parse_custom_extras(self.v['custom_extras'].get())
        except ValueError as ex:
            return messagebox.showwarning('Custom extras', 'The custom extras field: %s' % ex)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            return messagebox.showwarning('Custom extras', 'Could not read the bank: %s' % ex)
        known = {r['key'] for r in rows}
        for alias in cur:                                          # keep entries this bank doesn't have
            if alias not in known:
                rows.append({'key': alias, 'count': 0, 'groups': set(), 'maps_to': 'not in this bank',
                             'kind': 'unknown', 'lead': _P.lead_vowel(alias) if lang == 'ja' else None})
        groups = ['Breaths'] + list(_S.PHONETIC_GROUPS)
        sung = set().union(_P.VOWELS, _P.UNV_PLOSIVE, _P.UNV_FRIC, _P.UNV_AFFR, _P.V_PLOSIVE, _P.V_FRIC,
                           _P.NASAL, _P.LIQUID, _P.SEMI, _P.SIL, _P.V_PHONEMES)
        vowels = ['a', 'i', 'M', 'e', 'o', 'N\\'] if lang == 'ja' else []

        # prefilled names: "a R", "i R" ... share one ("hh"), so ticking them all just works
        # (vowel endings "a R" / "a -" all suggest hh; names already in the field win)
        by_rest, taken = {'R': 'hh', '-': 'hh'}, {ph for ph, _v, _g in cur.values()} | sung | {'hh'}
        for alias, (ph, _v, _g) in cur.items():
            if lang == 'ja' and _P.lead_vowel(alias):
                by_rest[alias.split(None, 1)[1].strip()] = ph

        def suggest(r):
            if r['lead']:
                rest = r['key'].split(None, 1)[1].strip()
                if rest not in by_rest:
                    by_rest[rest] = _S.suggest_extra_phoneme(rest, taken)
                    taken.add(by_rest[rest])
                return by_rest[rest]
            ph = _S.suggest_extra_phoneme(r['key'], taken)
            taken.add(ph)
            return ph

        items = []
        for r in rows:
            ph0, v0, g0 = cur.get(r['key'], (None, None, None))
            if ph0 is None:
                ph0 = suggest(r)
                breathy = ph0 == 'hh' or re.search(r'息|吸|breath|br', r['key'], re.I)
                g0 = 'Breaths' if breathy else 'Whisper'
            items.append(dict(r, inc=tk.BooleanVar(value=r['key'] in cur), ph=tk.StringVar(value=ph0),
                              grp=tk.StringVar(value=g0 if g0 in groups else
                                               ('Vowels' if v0 else 'Breaths'))))
        ends = {ph: tk.BooleanVar(value=ph in cur.ends) for ph in {ph for ph, _v, _g in cur.values()}}
        for it in items:
            it['trans'] = tk.StringVar()
            show = lambda *_a, x=it: x['trans'].set(  # noqa: E731
                '%s → %s' % (x['lead'], x['ph'].get().strip() or '?') if x['lead'] else 'on its own')
            it['ph'].trace_add('write', show)
            show()

        top = ctk.CTkToplevel(self)
        top.title('Custom extras & transitions')
        top.transient(self)
        top.grab_set()
        top.geometry('980x720')
        top.minsize(760, 520)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(3, weight=1)
        ttk.Label(top, padding=(12, 10, 12, 0), style='Info.TLabel', wraplength=940, justify='left',
                  text='Tick the sounds to port and name their phoneme. An alias that starts with a vowel '
                       '(a R, a laugh) keeps the transition it was recorded with; give several the same name '
                       '(a R, i R … → hh) and each of those vowels gets its own recorded way in. Below: '
                       'which transitions come from recordings and which get spliced.').grid(
            row=0, column=0, sticky='ew')
        bar = ttk.Frame(top, padding=(12, 8, 12, 0))
        bar.grid(row=1, column=0, sticky='ew')
        ttk.Label(bar, text='Search').pack(side='left')
        q = tk.StringVar()
        search = ttk.Entry(bar, textvariable=q, width=28)
        search.pack(side='left', padx=6)
        show_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='Show every alias (also the ones that already sing)',
                        variable=show_all).pack(side='left', padx=(12, 0))
        count = ttk.Label(bar, style='Hint.TLabel')
        count.pack(side='right')
        sf = ScrollFrame(top)
        sf.grid(row=3, column=0, sticky='nsew', padx=12, pady=(6, 0))
        body = sf.body
        SHOW = 200                                    # widgets per view; search always covers every alias

        cov_box = ttk.Frame(top, padding=(10, 4), fg_color=CARD_BG, corner_radius=10,
                            border_width=1, border_color=BORDER)
        cov_box.grid(row=4, column=0, sticky='ew', padx=12, pady=(8, 0))
        ttk.Label(cov_box, text='Transitions', style='Sec.TLabel').pack(anchor='w', pady=(2, 5))
        cov_sf = ScrollFrame(cov_box)
        cov_sf.configure(height=150)
        cov_sf.pack(fill='both', expand=True)
        cov_sf.pack_propagate(False)
        cov = cov_sf.body
        pending = []

        def coverage(*_a):
            if pending:
                return
            pending.append(top.after_idle(_coverage))

        def _coverage():
            pending.clear()
            for w in cov.winfo_children():
                w.destroy()
            chosen = {}
            for it in items:
                ph = it['ph'].get().strip()
                if it['inc'].get() and ph:
                    chosen.setdefault(ph, []).append(it)
            if not chosen:
                ttk.Label(cov, text='Nothing ticked yet.', style='Hint.TLabel').grid(row=0, column=0, sticky='w')
                return
            r = 0
            for ph in sorted(chosen):
                its = chosen[ph]
                if ph not in ends:                    # endings ("a R" only) default to phrase end
                    ends[ph] = tk.BooleanVar(value=all(it['lead'] for it in its))
                ttk.Label(cov, text=ph, style='Sec.TLabel').grid(row=r, column=0, sticky='nw', padx=(0, 10))
                ttk.Label(cov, style='Hint.TLabel', wraplength=760, justify='left',
                          text='%s · from %s' % (its[0]['grp'].get(), ', '.join(
                              '%s ×%d' % (it['key'], it['count']) for it in its))).grid(
                    row=r, column=1, columnspan=8, sticky='w')
                r += 1
                rec = {}
                for it in its:
                    if it['lead']:
                        rec[it['lead']] = rec.get(it['lead'], 0) + it['count']
                line = ttk.Frame(cov)
                line.grid(row=r, column=1, columnspan=8, sticky='w')
                for v in vowels:
                    n = rec.get(v, 0)
                    ttk.Label(line, text='%s→%s %s' % (v, ph, '✔ %d' % n if n else '~ splice'),
                              style='Rec.TLabel' if n else 'Splice.TLabel').pack(side='left', padx=(0, 12))
                alone = sum(it['count'] for it in its if not it['lead'])
                ttk.Label(line, text='Sil→%s %s' % (ph, '✔ %d' % alone if alone else '—'),
                          style='Rec.TLabel' if alone else 'Hint.TLabel').pack(side='left', padx=(0, 12))
                ttk.Label(line, text='%s→Sil ✔' % ph, style='Rec.TLabel').pack(side='left', padx=(0, 12))
                r += 1
                into = ttk.Frame(cov)
                into.grid(row=r, column=1, columnspan=8, sticky='w', pady=(0, 6))
                lead_in = tk.BooleanVar(value=not ends[ph].get())
                lead_in.trace_add('write', lambda *_a, p=ph, b=lead_in: ends[p].set(not b.get()))
                ttk.Checkbutton(into, variable=lead_in,
                                text='%s leads into singing (%s→a, %s→i …, spliced). Off = it only ends a phrase, '
                                     'like a breath out.' % (ph, ph, ph)).pack(side='left')
                r += 1

        def sync_group(it):
            ph = it['ph'].get().strip()
            for other in items:
                if other is not it and other['ph'].get().strip() == ph:
                    other['grp'].set(it['grp'].get())
            coverage()

        def render(vis):
            for w in body.winfo_children():
                w.destroy()
            for col, txt in enumerate(('', 'Alias', 'Now', 'Phoneme', 'Group', 'Transition')):
                ttk.Label(body, text=txt, style='Sec.TLabel').grid(row=0, column=col, sticky='w', padx=2,
                                                                   pady=(0, 2))
            for i, it in enumerate(vis[:SHOW], start=1):
                ttk.Checkbutton(body, variable=it['inc'], width=2).grid(row=i, column=0, sticky='w', padx=2)
                ttk.Label(body, text='%s  ×%d' % (it['key'], it['count']) if it['count'] else it['key'],
                          width=24).grid(row=i, column=1, sticky='w', padx=2)
                ttk.Label(body, text=it['maps_to'], width=22, style='Hint.TLabel').grid(row=i, column=2,
                                                                                        sticky='w', padx=2)
                ttk.Entry(body, textvariable=it['ph'], width=10).grid(row=i, column=3, sticky='w', padx=2)
                cb = ttk.Combobox(body, textvariable=it['grp'], values=groups, width=17, state='readonly')
                cb.grid(row=i, column=4, sticky='w', padx=2)
                cb.bind('<<ComboboxSelected>>', lambda _e, x=it: sync_group(x))
                ttk.Label(body, textvariable=it['trans'], width=16, style='Info.TLabel').grid(row=i, column=5, sticky='w', padx=2)
            sf.canvas.yview_moveto(0)

        def refilter(*_a):
            needle = q.get().strip().lower()
            vis = [it for it in items if (show_all.get() or it['kind'] != 'sung' or it['inc'].get())
                   and (not needle or needle in it['key'].lower() or needle in it['maps_to'].lower())]
            vis.sort(key=lambda it: not it['inc'].get())             # ticked ones first
            render(vis)
            n_sung = sum(1 for it in items if it['kind'] == 'sung')
            count.configure(text='showing %d of %d%s' % (
                min(len(vis), SHOW), len(vis),
                '' if show_all.get() else ' (+%d that already sing)' % n_sung if n_sung else ''))

        q.trace_add('write', refilter)
        show_all.trace_add('write', refilter)
        for it in items:
            it['inc'].trace_add('write', coverage)
            it['ph'].trace_add('write', coverage)
        refilter()
        _coverage()
        search.focus_set()

        def wheel_off():
            try:
                top.unbind_all('<MouseWheel>')
            except tk.TclError:
                pass

        def close():
            wheel_off()
            top.destroy()

        def ok():
            parts, seen = [], {}
            for it in items:
                if not it['inc'].get():
                    continue
                ph, alias = it['ph'].get().strip(), it['key']
                if any(sep in alias for sep in ',;:=\n'):
                    return messagebox.showwarning('Custom extras', '%r has , ; : or = in it, so it cannot be '
                                                                   'written as a custom extra.' % alias, parent=top)
                if not ph:
                    return messagebox.showwarning('Custom extras', 'Give %r a phoneme name.' % alias, parent=top)
                if ph in sung:
                    return messagebox.showwarning('Custom extras', '%r is already a singing phoneme: pick another '
                                                                   'name for %r.' % (ph, alias), parent=top)
                if seen.setdefault(ph, it['grp'].get()) != it['grp'].get():
                    return messagebox.showwarning('Custom extras', '%r has two groups (%s, %s): pick one.'
                                                  % (ph, seen[ph], it['grp'].get()), parent=top)
                end = ends.get(ph)
                parts.append('%s=%s:%s%s' % (alias, ph, it['grp'].get(), ':end' if end and end.get() else ''))
            spec = '; '.join(parts)
            try:
                _S.parse_custom_extras(spec)
            except ValueError as ex:
                return messagebox.showwarning('Custom extras', str(ex), parent=top)
            self.v['custom_extras'].set(spec)
            self.log('Custom extras: %s\n' % (spec or 'none'))
            close()

        btns = ttk.Frame(top, padding=(12, 10))
        btns.grid(row=5, column=0, sticky='ew')
        ttk.Button(btns, text='OK', style='Accent.TButton', command=ok).pack(side='left')
        ttk.Button(btns, text='Cancel', command=close).pack(side='left', padx=6)
        ttk.Button(btns, text='Untick all', command=lambda: [it['inc'].set(False) for it in items]).pack(
            side='right')
        top.protocol('WM_DELETE_WINDOW', close)


    def _edit_pitch_assignments(self):
        """Assign an append/voice colour and recorded pitch to each detected OTO group."""
        bank = self.v['bank'].get()
        if not os.path.isdir(bank):
            return messagebox.showwarning('Pitch & Append', 'Choose a voicebank first.')
        try:
            from u2v import oto
            entries = oto.read_bank(bank)
        except Exception as ex:  # noqa: BLE001 - report bank parsing issues in the GUI
            return messagebox.showwarning('Pitch & Append', 'Could not read the bank: %s' % ex)
        if not entries:
            return messagebox.showinfo('Pitch & Append', 'No oto.ini entries were found.')

        grouped = {}
        for entry in entries:
            row = grouped.setdefault(entry.group, {
                'count': 0,
                'append': (entry.color or 'main').strip() or 'main',
                'pitch': entry.pitch.upper().lstrip('_') if oto.NOTE_RE.fullmatch(entry.pitch) else '',
            })
            row['count'] += 1

        try:
            saved_by_bank = json.loads(self.v['pitch_assignments'].get() or '{}')
            if not isinstance(saved_by_bank, dict):
                saved_by_bank = {}
        except (TypeError, ValueError):
            saved_by_bank = {}
        bank_key = os.path.normcase(os.path.abspath(bank))
        saved = saved_by_bank.get(bank_key, {})
        if not isinstance(saved, dict):
            saved = {}

        top = ctk.CTkToplevel(self)
        top.title('Pitch & Append Assignment')
        top.transient(self)
        top.grab_set()
        top.geometry('860x650')
        top.minsize(700, 440)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        ttk.Label(
            top, padding=(14, 12, 14, 8), justify='left', wraplength=820,
            text='Assign each detected OTO group to a voice append (colour) and its actual recording pitch. '
                 'The complete group name is used, so Natural/_0 and Power/_0 can have different pitches. '
                 'Leave Pitch blank when it is unknown; Henkan will keep its existing fallback behaviour. '
                 'To include several append names in one singer DB, use Voice colours = evec on the Convert page.'
        ).grid(row=0, column=0, sticky='ew')

        holder = ttk.Frame(top, padding=(14, 0, 14, 0))
        holder.grid(row=1, column=0, sticky='nsew')
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        canvas = tk.Canvas(holder, highlightthickness=0, borderwidth=0, background=APP_BG)
        scroll = ttk.Scrollbar(holder, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        table = tk.Frame(canvas, background=APP_BG, borderwidth=0, highlightthickness=0)
        window = canvas.create_window((0, 0), window=table, anchor='nw')
        table.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
        table.columnconfigure(0, weight=3)
        table.columnconfigure(2, weight=3)
        table.columnconfigure(3, weight=1)
        for col, title in enumerate(('Detected OTO group', 'Entries', 'Append / colour', 'Pitch')):
            ttk.Label(table, text=title, style='Head.TLabel').grid(
                row=0, column=col, sticky='w', padx=(4, 8), pady=(4, 8))

        fields = []
        for row_index, (group, info) in enumerate(sorted(grouped.items()), start=1):
            prior = saved.get(group, {}) if isinstance(saved.get(group, {}), dict) else {}
            append_var = tk.StringVar(value=str(prior.get('append', prior.get('color', info['append'])) or 'main'))
            pitch_var = tk.StringVar(value=str(prior.get('pitch', info['pitch']) or '').upper())
            ttk.Label(table, text=group, wraplength=300).grid(
                row=row_index, column=0, sticky='w', padx=(4, 8), pady=3)
            ttk.Label(table, text=str(info['count'])).grid(
                row=row_index, column=1, sticky='w', padx=(4, 8), pady=3)
            ttk.Entry(table, textvariable=append_var, width=22).grid(
                row=row_index, column=2, sticky='ew', padx=(4, 8), pady=3)
            ttk.Entry(table, textvariable=pitch_var, width=10).grid(
                row=row_index, column=3, sticky='ew', padx=(4, 8), pady=3)
            fields.append((group, append_var, pitch_var))

        buttons = ttk.Frame(top, padding=14)
        buttons.grid(row=2, column=0, sticky='ew')

        def close():
            top.destroy()

        def save():
            assignments = {}
            for group, append_var, pitch_var in fields:
                append = append_var.get().strip()
                pitch = pitch_var.get().strip().upper().lstrip('_')
                if not append:
                    messagebox.showwarning('Pitch & Append', 'Enter an append name for %s.' % group, parent=top)
                    return
                if pitch and not oto.NOTE_RE.fullmatch(pitch):
                    messagebox.showwarning(
                        'Pitch & Append', 'Pitch %r for %s is not a note. Use values such as E3 or F#4, '
                        'or leave it blank.' % (pitch, group), parent=top)
                    return
                assignments[group] = {'append': append, 'pitch': pitch}
            saved_by_bank[bank_key] = assignments
            append_names = {item['append'] for item in assignments.values()}
            self.v['pitch_assignments'].set(json.dumps(saved_by_bank, ensure_ascii=False))
            if len(append_names) > 1 and self.v['colors'].get() == 'main':
                if messagebox.askyesno(
                        'Multiple appends assigned',
                        'You assigned %d append names. The current Voice colours setting is main, which '
                        'converts only one append. Switch it to evec so the database includes all assigned '
                        'appends?' % len(append_names), parent=top):
                    self.v['colors'].set('evec')
            self._save()
            self.log('Saved pitch/append assignments for %d OTO groups in %s.\n' %
                     (len(assignments), os.path.basename(os.path.normpath(bank))), 'ok')
            close()

        def reset():
            saved_by_bank.pop(bank_key, None)
            self.v['pitch_assignments'].set(json.dumps(saved_by_bank, ensure_ascii=False))
            self._save()
            self.log('Cleared manual pitch/append assignments; Henkan automatic detection is restored.\n')
            close()

        ttk.Button(buttons, text='Save assignments', style='Accent.TButton', command=save).pack(side='left')
        ttk.Button(buttons, text='Reset to automatic', command=reset).pack(side='left', padx=8)
        ttk.Button(buttons, text='Cancel', command=close).pack(side='right')
        top.protocol('WM_DELETE_WINDOW', close)

    def _v_mode(self):
        return (self.v['v_mode'].get().split() or ['auto'])[0]

    def _convert_args(self):
        v = self.v
        _colors = v['colors'].get()
        if _colors == 'main' and re.search(r'#\d', v['evec_map'].get()):
            _colors = 'evec'
        if v['evec_vowels'].get():
            _colors = 'evec'
        args = ['convert', v['bank'].get(), v['out'].get(), '--lang', v['lang'].get(), '--colors', _colors,
                '--keep', v['keep'].get()]
        growl_wav = v['growl_wav'].get().strip()
        if growl_wav:
            if not os.path.exists(growl_wav):
                raise ValueError('The selected VQM source does not exist:\n%s' % growl_wav)
            if os.path.isfile(growl_wav) and os.path.splitext(growl_wav)[1].lower() != '.wav':
                raise ValueError('A single VQM source file must be a .wav file; folders are also accepted.')
            args += ['--growl-wav', growl_wav]
        if v['dict'].get():
            if not os.path.exists(v['dict'].get()):
                raise ValueError('The phonetic dictionary file does not exist:\n%s' % v['dict'].get())
            args += ['--dict', v['dict'].get()]
        sel = [self.groups[i][0] for i in self.glist.curselection()]
        if sel and len(sel) < len(self.groups):
            args += ['--groups', '^(%s)$' % '|'.join(re.escape(g) for g in sel)]
        for k, flag in (('splice', '--no-splice'), ('fill', '--no-fill'), ('normalize', '--no-normalize'),
                        ('derive', '--no-derive'), ('extend_dict', '--no-extend-dict')):
            if not v[k].get():
                args.append(flag)
        if v['merge_groups'].get():
            args.append('--merge-groups')
        if v['triphones'].get():
            args.append('--use-oto-aliases')
        _em = v['evec_map'].get().strip()
        _ep = v['evec_phonemes'].get().strip()
        if _em or _ep:
            from u2v import select as _Sel2
            try:
                if _em:
                    _Sel2.parse_evec_map(_em)
                if _ep:
                    _Sel2.parse_evec_phonemes(_ep)
            except ValueError as _ex:
                raise ValueError('EVEC: %s' % _ex)
        if not v['extras'].get():
            args += ['--extras', 'none']
        if _em:
            args += ['--evec-map', _em]
        if _ep:
            args += ['--evec-phonemes', _ep]
        _cx = v['custom_extras'].get().strip()
        if _cx:
            try:
                from u2v import select as _SelC2
                _SelC2.parse_custom_extras(_cx)
            except ValueError as _ex:
                raise ValueError('Custom extras: %s' % _ex)
            args += ['--custom-extra', _cx]
        if v['evec_vowels'].get():
            args += ['--evec-mode', 'vowels']
        if self._v_mode() != 'auto':
            args += ['--v-mode', self._v_mode()]
        for b in re.split(r'[,;\s]+', v['brighten'].get().strip()):
            if b:
                if not re.fullmatch(r"[^=]+=-?\d+(\.\d+)?", b):
                    raise ValueError('Brighten: "%s" should look like o=3' % b)
                args += ['--brighten', b]
        for u in v['avoid'].get().split(';'):
            if u.strip():
                if len(u.split()) != 2:
                    raise ValueError('Avoid units: "%s" should be two phonemes, e.g. g\' i' % u.strip())
                args += ['--avoid', ' '.join(u.split())]
        try:
            saved_by_bank = json.loads(v['pitch_assignments'].get() or '{}')
        except (TypeError, ValueError):
            saved_by_bank = {}
        bank_key = os.path.normcase(os.path.abspath(v['bank'].get()))
        assignments = saved_by_bank.get(bank_key, {}) if isinstance(saved_by_bank, dict) else {}
        if assignments:
            args += ['--group-assignments', json.dumps(assignments, ensure_ascii=False, separators=(',', ':'))]
        return args

    def _convert_warnings(self):
        sel = [self.groups[i][0] for i in self.glist.curselection()]
        names = sel or [g for g, _n in self.groups]
        em = self.v['evec_map'].get().strip()
        if em and self.v['colors'].get() == 'evec':
            try:
                from u2v import select as _SelW
                want = _SelW.parse_evec_map(em)
            except ValueError:
                return True
            rows = getattr(self, 'color_rows', []) or []
            colours = [c for c, _n, _p, _g in rows]
            bad = [w for w in want
                   if not any(w.lower() in n.lower() or n.lower() in w.lower() for n in names)
                   and not any(w.lower() in c.lower() or c.lower() in w.lower() for c in colours)]
            if bad:
                return messagebox.askokcancel(
                    'EVEC map',
                    'These EVEC names match nothing selected: %s.\n\n'
                    'Selected folders: %s.\n'
                    'That colour will convert with an automatic number instead. Proceed anyway?'
                    % (', '.join(bad), ', '.join(names[:6]) + (' ...' if len(names) > 6 else '')))
        if not self.v['splice'].get() and any('単独音' in n for n in names):
            return messagebox.askokcancel(
                'CV legato is off',
                '“Fake missing transitions” is off but single-note folders are selected.\n'
                'CV parts will not sing legato. Proceed anyway?')
        return True

    def convert(self, then=None):
        if not os.path.isdir(self.v['bank'].get()):
            return messagebox.showwarning('Convert', 'Choose an UTAU voicebank folder first.')
        if not self.v['out'].get():
            return messagebox.showwarning('Convert', 'Choose an output folder.')
        if not self.v['dict'].get():
            return messagebox.showwarning(
                'Convert', 'No phonetic dictionary found.\n\nIt comes with the VOCALOID3 devkit: '
                '"Japanese Dictionary\\Japanese_Dictionary.txt" (English banks: "English Dictionary\\'
                'english_phonetic_dictionary_20061220.txt").\n\nPress Browse… next to "Phonetic dictionary" and pick it.')
        try:
            args = self._convert_args()
        except ValueError as ex:
            return messagebox.showwarning('Convert', str(ex))
        if not self._convert_warnings():
            return
        self._save()
        self.runner.start(self._py(CLI, *args), 'Converting',
                          done=lambda code: then() if (then and code == 0) else None)

    def validate(self):
        out = self.v['out'].get()
        if not os.path.isdir(out):
            return messagebox.showwarning('Validate', 'Convert the bank first.')
        lang = 'en' if (self.v['lang'].get() == 'en' or self.detected_lang == 'en') else 'ja'
        self.runner.start(self._py(CLI, 'validate', out, '--lang', lang), 'Validating')

    def build(self, then=None):
        out, db = self.v['out'].get(), self.v['db_path'].get()
        if not os.path.exists(os.path.join(out, 'dictionary.txt')):
            return messagebox.showwarning('Build', 'The output folder has no dictionary.txt, so it was not converted '
                                          '(or the conversion could not find the phonetic dictionary).\n\n'
                                          'Set "Phonetic dictionary" at the top and press Convert again.')
        if not db:
            return messagebox.showwarning('Build', 'Enter where to create the DB (folder\\Name).')
        collisions = [p for p in (db, db + '.dat', db + '.tree') if os.path.exists(p)]
        if collisions:
            return messagebox.showwarning('Build', 'A DB artifact already exists:\n%s\n\nPick a new name or remove the old partial DB.'
                                          % '\n'.join(collisions))
        if not os.path.exists(self.v['dbtool'].get()):
            return messagebox.showwarning('Build', 'VocaloidDBTool3.exe not found. Set its path.')
        self._save()
        args = ['build', out, db, '--name', self.v['db_name'].get() or 'Singer',
                '--language', self.v['db_lang'].get(), '--dbtool', self.v['dbtool'].get()]
        if not self.v['dbtool_hidden'].get():
            args.append('--show-dbtool')
        self.runner.start(self._py(CLI, *args), 'Building the singer DB',
                          done=lambda code: (self._built(code), then() if (then and code == 0) else None))

    def _built(self, code):
        """A built DB is what the Pack & Install page packs next: point it there."""
        if code != 0:
            return
        db = self.v['db_path'].get()
        self.v['pkg_db'].set(db)
        if not self.v['pkg_name'].get():
            self.v['pkg_name'].set(self.v['db_name'].get() or os.path.basename(db))
        if not self.v['pkg_out'].get():
            self.v['pkg_out'].set(os.path.join(os.path.dirname(db), 'package'))
        self.log('Next: ③ Pack & Install packs %s into a voicebank.\n' % db, 'ok')

    # ------------------------------------------------------------ pack & install
    def pick_built(self):
        d = filedialog.askdirectory(title='Folder holding the built singer DB (<Name>.tree + <Name>/voice/)')
        if not d:
            return
        d = os.path.normpath(d)
        trees = sorted(f for f in os.listdir(d) if f.endswith('.tree'))
        if len(trees) == 1:
            self.v['pkg_db'].set(os.path.join(d, trees[0][:-5]))
        elif not trees:
            parent_tree = os.path.join(os.path.dirname(d), os.path.basename(d) + '.tree')
            if os.path.exists(parent_tree):
                self.v['pkg_db'].set(parent_tree[:-5])  # pointed at <Name>/ itself
            else:
                self.v['pkg_db'].set(d)
                messagebox.showinfo('Built DB', 'No .tree in %s: pick the folder holding <Name>.tree.' % d)
        else:
            self.v['pkg_db'].set(os.path.join(d, trees[0][:-5]))
        db = self.v['pkg_db'].get()
        if not self.v['pkg_name'].get():
            self.v['pkg_name'].set(os.path.basename(db.rstrip('\\/')) or 'Singer')
        if not self.v['pkg_out'].get():
            self.v['pkg_out'].set(os.path.join(os.path.dirname(db.rstrip('\\/')), 'package'))

    def gen_compid(self):
        """CompID generator: pick a language, take a pool ID or roll a random one, check it, use it."""
        from u2v import compid as _C
        top = ctk.CTkToplevel(self)
        top.title('CompID Generator — your bank\'s ID')
        top.transient(self)
        top.grab_set()
        top.minsize(520, 460)
        ttk.Label(top, text=_C.WHAT_IS_IT, wraplength=500, padding=(10, 8, 10, 0),
                  style='Info.TLabel', justify='left').pack(anchor='w')
        row = ttk.Frame(top, padding=(10, 8, 10, 0))
        row.pack(fill='x')
        ttk.Label(row, text='Bank language').pack(side='left')
        langs = ('Japanese', 'Chinese', 'English', 'Korean', 'Spanish', 'Other')
        cur_lang = (self.v['db_lang'].get() or 'Japanese').capitalize()
        lang = tk.StringVar(value=cur_lang if cur_lang in langs else 'Japanese')
        ttk.Combobox(row, textvariable=lang, values=langs, width=12,
                     state='readonly').pack(side='left', padx=6)
        cand = tk.StringVar()
        show = ttk.Label(top, textvariable=cand, font=('Consolas', 16, 'bold'), padding=(10, 6, 10, 0))
        show.pack(anchor='w')
        status = tk.StringVar()
        status_lb = ttk.Label(top, textvariable=status, wraplength=500, padding=(10, 0, 10, 0))
        status_lb.pack(anchor='w')
        info = tk.StringVar()
        ttk.Label(top, textvariable=info, wraplength=500, padding=(10, 0, 10, 0),
                  style='Info.TLabel').pack(anchor='w')

        def _show(cid):
            cand.set((cid or '').strip().upper())
            ok, msg = _C.status_of(cid)
            status.set(msg)
            try:
                status_lb.configure(foreground='#107010' if ok else '#c00000')
            except Exception:                                      # noqa: BLE001 - cosmetic only
                pass
            parts = _C.describe_compid(cand.get())
            if parts:
                info.set('raw %s — vendor %s, %s, product %s, version %s' % (
                    parts['raw'], parts['vendor'], parts['lang_name'],
                    parts['product'], parts['version']))
            elif cand.get():
                info.set('decodes to no keygen raw string — normal for real bank IDs, '
                         'the editor never checks the checksum.')
            else:
                info.set('')

        def _pool():
            try:
                _show(_C.pick_from_pool(lang.get()))
            except Exception as ex:                                # noqa: BLE001 - show, don't crash
                status.set('%s' % ex)
                try:
                    status_lb.configure(foreground='#c00000')
                except Exception:                                  # noqa: BLE001 - cosmetic only
                    pass

        def _random():
            try:
                _show(_C.random_compid())
            except Exception as ex:                                # noqa: BLE001 - show, don't crash
                status.set('%s' % ex)

        def _craft():
            try:
                _show(_C.generate(lang.get()))
            except Exception as ex:                                # noqa: BLE001 - show, don't crash
                status.set('%s' % ex)

        def _encode_raw():
            try:
                _show(_C.encode_raw(raw.get()))
            except Exception as ex:                                # noqa: BLE001 - show, don't crash
                status.set('%s' % ex)
                try:
                    status_lb.configure(foreground='#c00000')
                except Exception:                                  # noqa: BLE001 - cosmetic only
                    pass

        btns = ttk.Frame(top, padding=(10, 6, 10, 0))
        btns.pack(fill='x')
        ttk.Button(btns, text='Pick from pool', command=_pool).pack(side='left')
        ttk.Button(btns, text='Craft for language', command=_craft).pack(side='left', padx=6)
        ttk.Button(btns, text='Random', command=_random).pack(side='left')
        ttk.Label(top, text='Pool: known-valid V4 Japanese/Chinese/English IDs. Craft: fresh ID with '
                            'the right language digit (the Korean/Spanish move — no pools exist).',
                  wraplength=500, padding=(10, 2, 10, 0), style='Hint.TLabel',
                  justify='left').pack(anchor='w')
        crow = ttk.Frame(top, padding=(10, 6, 10, 0))
        crow.pack(fill='x')
        ttk.Label(crow, text='Or paste one:').pack(side='left')
        custom = tk.StringVar()
        ttk.Entry(crow, textvariable=custom, width=20, font=('Consolas', 11)).pack(side='left', padx=6)
        ttk.Button(crow, text='Check', command=lambda: _show(custom.get())).pack(side='left')
        rrow = ttk.Frame(top, padding=(10, 6, 10, 0))
        rrow.pack(fill='x')
        ttk.Label(rrow, text='Or raw (14 chars):').pack(side='left')
        raw = tk.StringVar()
        ttk.Entry(rrow, textvariable=raw, width=20, font=('Consolas', 11)).pack(side='left', padx=6)
        ttk.Button(rrow, text='Encode', command=_encode_raw).pack(side='left')
        end = ttk.Frame(top, padding=(10, 10))
        end.pack(fill='x')

        def _use():
            if not cand.get():
                return messagebox.showwarning('CompID', 'Pick or generate an ID first.')
            ok, msg = _C.status_of(cand.get())
            if not ok and not messagebox.askokcancel('CompID', '%s.\n\nUse it anyway?' % msg):
                return
            self.v['pkg_compid'].set(cand.get().strip().upper())
            self.log('CompID: %s\n' % cand.get().strip().upper())
            top.destroy()

        ttk.Button(end, text='Use this CompID', command=_use).pack(side='left')
        ttk.Button(end, text='Cancel', command=top.destroy).pack(side='left', padx=6)
        _pool() if _C.POOLS.get(lang.get()) else _craft()

    def pick_pkg_out(self):
        d = filedialog.askdirectory(title='Folder for the voicebank package')
        if d:
            self.v['pkg_out'].set(os.path.normpath(d))

    def pick_vqm_folder(self):
        path = filedialog.askdirectory(title='Choose the VOCALOID bank root or its vqm folder')
        if path:
            self.v['growl_wav'].set(os.path.normpath(path))

    def pick_growl_wav(self):
        path = filedialog.askopenfilename(
            title='Choose one numbered VQM WAV (for example vqm_0.wav)',
            filetypes=[('WAV audio', '*.wav'), ('All files', '*.*')])
        if path:
            self.v['growl_wav'].set(os.path.normpath(path))

    def package_dir(self):
        out, cid = self.v['pkg_out'].get(), self.v['pkg_compid'].get().strip().upper()
        return os.path.join(out, cid) if out and cid else (out or '')

    def _editors(self):
        try:
            from u2v import pack as _pack
            eds = _pack.find_editors()
            self._editor_list = eds
            return ['%s  (%s)' % (e['label'], e['root']) for e in eds] or ['(no editor detected — Browse…)']
        except Exception:                                          # noqa: BLE001 - editor not installed
            self._editor_list = []
            return ['(no editor detected — Browse…)']

    def _editor_pick(self):
        eds = getattr(self, '_editor_list', [])
        txt = self.v['pkg_editor'].get()
        for e in eds:
            if txt.startswith(e['label']):
                return e
        if os.path.isdir(txt):
            try:
                from u2v import pack as _pack
                return _pack.guess_editor(txt)
            except Exception:                                      # noqa: BLE001 - warned below
                pass
        return None

    def pick_editor(self):
        d = filedialog.askdirectory(title='Point at the VOCALOID editor install folder')
        if not d:
            return
        try:
            from u2v import pack as _pack
            e = _pack.guess_editor(os.path.normpath(d))
            self._editor_list = getattr(self, '_editor_list', []) + [e]
            self.editor_box['values'] = self._editors_refresh(e)
            self.v['pkg_editor'].set('%s  (%s)' % (e['label'], e['root']))
            self._fill_pkg_dest(e)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            messagebox.showwarning('Editor', '%s' % ex)

    def _editors_refresh(self, extra):
        eds = getattr(self, '_editor_list', [])
        if all(x['root'] != extra['root'] for x in eds):
            eds.append(extra)
        self._editor_list = eds
        return ['%s  (%s)' % (e['label'], e['root']) for e in eds]

    def _fill_pkg_dest(self, editor):
        if self.v['pkg_dest'].get():
            return
        try:
            from u2v import pack as _pack
            name = self.v['pkg_name'].get() or 'Singer'
            self.v['pkg_dest'].set(_pack.default_bank_dir(editor, name))
        except Exception:                                          # noqa: BLE001 - leave it empty
            pass

    def pick_pkg_dest(self):
        d = filedialog.askdirectory(title='Bank folder for the .ddb/.ddi/.vvd')
        if d:
            self.v['pkg_dest'].set(os.path.normpath(d))

    def pack(self, then=None):
        db = self.v['pkg_db'].get()
        if not db:
            db = self.v['db_path'].get()
            self.v['pkg_db'].set(db)
        if not db or not os.path.exists(db + '.tree'):
            return messagebox.showwarning('Pack', 'Build the singer DB first (Build singer DB page), then point at it.')
        args = [db]
        if self.v['pkg_out'].get():
            args += ['--out', self.v['pkg_out'].get()]
        if self.v['pkg_name'].get():
            args += ['--name', self.v['pkg_name'].get()]
        if self.v['pkg_compid'].get().strip():
            args += ['--compid', self.v['pkg_compid'].get().strip().upper()]
        for key, flag in (('pkg_breathiness', '--breathiness'), ('pkg_brightness', '--brightness'),
                          ('pkg_clearness', '--clearness'), ('pkg_gender_factor', '--gender-factor')):
            try:
                value = int(self.v[key].get().strip())
            except ValueError:
                return messagebox.showwarning('Pack', '%s must be an integer from -127 to 127.' % flag)
            if not -127 <= value <= 127:
                return messagebox.showwarning('Pack', '%s must be from -127 to 127.' % flag)
            if value:
                args += [flag, str(value)]
        self._save()

        def done(code):
            if code == 0:
                pkg = self.package_dir()
                if self.v['pkg_compid'].get().strip():
                    self.log('package: %s\n' % pkg)
                else:
                    try:
                        import glob as _glob
                        subs = sorted(_glob.glob(os.path.join(self.v['pkg_out'].get(), 'B*')))
                        if subs:
                            self.v['pkg_compid'].set(os.path.basename(subs[-1]))
                            self.log('package: %s\n' % self.package_dir())
                    except Exception:                              # noqa: BLE001 - log only
                        pass
                if then:
                    then()
        self.runner.start(self._py(CLI, 'pack', *args), 'Packing the voicebank', done=done)

    @staticmethod
    def _run_as_admin(bat):
        import ctypes
        rc = ctypes.windll.shell32.ShellExecuteW(None, 'runas', bat, None,
                                                 os.path.dirname(bat), 1)
        if rc <= 32:
            raise OSError('Windows refused to launch %s (code %s)' % (bat, rc))
        return rc

    def _stage_install(self):
        """Write install.bat/uninstall.bat (+ dev slot) without admin rights. Returns the .bat to elevate."""
        from u2v import pack as _pack
        pkg = self.package_dir()
        if not pkg or not os.path.isdir(pkg):
            raise ValueError('Pack the voicebank first: no package at %s.' % (pkg or 'that folder'))
        editor = self._editor_pick()
        if editor is None:
            raise ValueError('Choose the editor to install into (or Browse… to point at it).')
        if self.v['pkg_devini'].get():
            if editor['kind'] != 'V4FE-dev':
                raise ValueError('A DB_Dev.ini slot is for the VOCALOID4FE developer editor.')
            db = self.v['pkg_db'].get() or self.v['db_path'].get()
            profile = {'breathiness': self.v['pkg_breathiness'].get(),
                       'brightness': self.v['pkg_brightness'].get(),
                       'clearness': self.v['pkg_clearness'].get(),
                       'gender_factor': self.v['pkg_gender_factor'].get()}
            return _pack.write_dev_slot(editor['root'], db, self.v['pkg_name'].get() or None,
                                        log=self.log, voice_params=profile)
        _pack.write_install_scripts(pkg, editor,
                                    dest=self.v['pkg_dest'].get() or None,
                                    compid=self.v['pkg_compid'].get().strip().upper() or None,
                                    name=self.v['pkg_name'].get() or None, log=self.log)
        return os.path.join(pkg, 'install.bat')

    def install(self):
        try:
            bat = self._stage_install()
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            return messagebox.showwarning('Install', '%s' % ex)
        self._save()
        self.log('staged: %s\nrequesting administrator rights…\n' % bat)
        try:
            self._run_as_admin(bat)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            return messagebox.showwarning('Install', '%s' % ex)
        self.log('launched %s: press Yes on the Windows prompt, then watch its window.\n' % bat)

    def uninstall(self):
        pkg = self.package_dir()
        bat = os.path.join(pkg, 'uninstall.bat') if pkg else ''
        if not bat or not os.path.exists(bat):
            return messagebox.showwarning('Uninstall', 'No uninstall.bat in %s: pack + stage the install first.'
                                          % (pkg or 'that folder'))
        try:
            self._run_as_admin(bat)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            return messagebox.showwarning('Uninstall', '%s' % ex)
        self.log('launched %s: press Yes on the Windows prompt.\n' % bat)


def main():
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('dark-blue')
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)         # sharp text on scaled displays
        except Exception:                                          # noqa: BLE001
            pass
    App().mainloop()


if __name__ == '__main__':
    main()
