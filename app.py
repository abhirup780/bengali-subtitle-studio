from __future__ import annotations
import bisect
import ctypes
import os
import queue
import sys
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from bnsrt.config import Settings
from bnsrt.errors import CancelledError, PipelineError
from bnsrt.ffmpeg import extract_preview_wav
from bnsrt.openrouter import OpenRouterClient
from bnsrt.pairing import find_pair
from bnsrt.passes import MAX_INSTRUCTION_CHARS
from bnsrt.pipeline import Pipeline
from bnsrt.player import AudioPlayer, PlayerError
from bnsrt.providers import OpenRouterLlmProvider, OpenRouterTranscriptionProvider
from bnsrt.srt import parse_srt_with_lines
from bnsrt import winauth
MEDIA_FILETYPES = [('Audio / Video', '*.mp3 *.wav *.m4a *.mp4 *.mkv *.mov *.flac *.ogg *.aac *.webm'), ('All files', '*.*')]
LANGUAGES = ['bn-IN', 'bn-BD']
BG = '#12141a'
CARD = '#1a1d25'
FIELD = '#242836'
BORDER = '#2b3040'
TEXT = '#e8eaf0'
MUTED = '#8b93a7'
FAINT = '#5c6375'
ACCENT = '#4f7cff'
ACCENT_HOVER = '#3d68e8'
ACCENT_DIM = '#2c3a63'
SELECT_BG = '#31406e'
OK_GREEN = '#4ade80'
ERR_RED = '#f87171'
def _resource(name: str) -> str:
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)
def _enable_windows_dpi() -> float:
    if sys.platform != 'win32':
        return 1.0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            return 1.0
    try:
        return ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
    except (AttributeError, OSError):
        return 1.0
def _darken_title_bar(window: tk.Tk) -> None:
    if sys.platform != 'win32':
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass
class App(tk.Tk):
    def __init__(self, scale: float):
        super().__init__()
        self.scale = scale
        self.title('Bengali Subtitle Studio')
        self.configure(bg=BG)
        try:
            self.iconbitmap(_resource('app.ico'))
        except tk.TclError:
            pass
        self._pick_fonts()
        self._set_geometry()
        self.settings = Settings.load()
        if self.settings._had_plaintext_key:
            try:
                self.settings.save()
            except OSError:
                pass
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.result_paths: dict[str, str] = {}
        self.active_tab = 'bn'
        self.player = AudioPlayer()
        self._audio_token = 0
        self._wav_loaded = ''
        self.player_len = 0
        self.slider_drag = False
        self.cue_cache: dict[str, list] = {'bn': [], 'en': []}
        self.cue_dirty = {'bn': True, 'en': True}
        self.current_cue: tuple[str, int] | None = None
        self._build_styles()
        self._build_ui()
        _darken_title_bar(self)
        self.after(100, self._poll_events)
        self.after(100, self._tick)
        self.protocol('WM_DELETE_WINDOW', self._on_close)
    def _pick_fonts(self) -> None:
        families = set(tkfont.families(self))
        title = next((f for f in ('Inter SemiBold', 'Inter', 'Segoe UI Semibold') if f in families), 'Segoe UI')
        bengali = next((f for f in ('Noto Serif Bengali', 'Nirmala UI') if f in families), 'Nirmala UI')
        self.f_ui = ('Segoe UI', 10)
        self.f_ui_bold = ('Segoe UI', 10, 'bold')
        self.f_title = (title, 17)
        self.f_label = ('Segoe UI', 9)
        self.f_section = ('Segoe UI Semibold' if 'Segoe UI Semibold' in families else 'Segoe UI', 9)
        self.f_tab = ('Segoe UI Semibold' if 'Segoe UI Semibold' in families else 'Segoe UI', 10)
        self.f_preview_bn = (bengali, 11)
        self.f_preview_en = ('Segoe UI', 11)
    def _set_geometry(self) -> None:
        self.tk.call('tk', 'scaling', self.scale * 96 / 72)
        self.update_idletasks()
        sw, sh = (self.winfo_screenwidth(), self.winfo_screenheight())
        w = min(int(1280 * self.scale), sw - int(60 * self.scale))
        h = min(int(780 * self.scale), sh - int(100 * self.scale))
        self.geometry(f'{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}')
        self.minsize(int(1000 * self.scale), int(620 * self.scale))
    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use('clam')
        px = lambda n: int(n * self.scale)
        style.configure('.', background=BG, foreground=TEXT, font=self.f_ui, bordercolor=BORDER, focuscolor=ACCENT, selectbackground=SELECT_BG, selectforeground=TEXT)
        style.configure('Card.TFrame', background=CARD)
        style.configure('TLabel', background=BG, foreground=TEXT)
        style.configure('Field.TLabel', background=CARD, foreground=MUTED, font=self.f_label)
        style.configure('Section.TLabel', background=CARD, foreground=FAINT, font=self.f_section)
        style.configure('Title.TLabel', background=BG, foreground=TEXT, font=self.f_title)
        style.configure('Status.TLabel', background=BG, foreground=MUTED, font=self.f_ui)
        style.configure('TEntry', fieldbackground=FIELD, foreground=TEXT, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, insertcolor=TEXT, padding=(10, 8))
        style.map('TEntry', bordercolor=[('focus', ACCENT)], lightcolor=[('focus', ACCENT)], darkcolor=[('focus', ACCENT)])
        style.configure('Field.TMenubutton', background=FIELD, foreground=TEXT, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, arrowcolor=MUTED, padding=(12, 7), relief='flat')
        style.map('Field.TMenubutton', background=[('active', '#2e3342')], arrowcolor=[('active', TEXT)])
        style.configure('TButton', padding=(16, 8), background=FIELD, foreground=TEXT, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, relief='flat')
        style.map('TButton', background=[('active', '#2e3342'), ('disabled', CARD)], foreground=[('disabled', FAINT)])
        style.configure('Accent.TButton', background=ACCENT, foreground='#ffffff', bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT, font=self.f_ui_bold, padding=(24, 11))
        style.map('Accent.TButton', background=[('active', ACCENT_HOVER), ('disabled', ACCENT_DIM)], foreground=[('disabled', '#7d8bb8')])
        style.configure('TProgressbar', troughcolor=FIELD, background=ACCENT, bordercolor=FIELD, lightcolor=ACCENT, darkcolor=ACCENT, thickness=px(5))
        style.configure('Vertical.TScrollbar', background=FIELD, troughcolor=CARD, bordercolor=CARD, arrowcolor=MUTED, arrowsize=px(14), lightcolor=FIELD, darkcolor=FIELD)
        style.map('Vertical.TScrollbar', background=[('active', BORDER)])
        style.configure('Preview.Horizontal.TScale', background=ACCENT, troughcolor=FIELD, bordercolor=CARD, lightcolor=ACCENT, darkcolor=ACCENT, gripcount=0)
        style.map('Preview.Horizontal.TScale', background=[('active', ACCENT_HOVER)])
    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(24, 18, 24, 18))
        root.pack(fill='both', expand=True)
        root.columnconfigure(0, weight=0, minsize=int(440 * self.scale))
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)
        ttk.Label(root, text='Bengali Subtitle Studio', style='Title.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 16))
        left = ttk.Frame(root)
        left.grid(row=1, column=0, sticky='nsew', padx=(0, 20))
        source = self._card(left, 'SOURCE')
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self._field(source, 'Audio or video file', self.input_var, self._pick_input)
        self._field(source, 'Output folder (optional)', self.output_var, self._pick_output, last=True)
        api = self._card(left, 'OPENROUTER')
        self.key_var = tk.StringVar(value=self.settings.api_key)
        key_row = self._field(api, 'API key', self.key_var, None, show='•')
        self.key_entry = key_row
        self.key_toggle = ttk.Button(key_row.master, text='Show', width=6, command=self._toggle_key)
        self.key_toggle.pack(side='left', padx=(8, 0))
        models = tk.Frame(api, bg=CARD)
        models.pack(fill='x', padx=18, pady=(0, 15))
        models.columnconfigure(0, weight=1, uniform='m')
        models.columnconfigure(1, weight=1, uniform='m')
        mleft = tk.Frame(models, bg=CARD)
        mleft.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        ttk.Label(mleft, text='Speech to text', style='Field.TLabel').pack(anchor='w', pady=(0, 5))
        self.stt_var = tk.StringVar(value=self.settings.stt_model)
        ttk.Entry(mleft, textvariable=self.stt_var).pack(fill='x')
        mright = tk.Frame(models, bg=CARD)
        mright.grid(row=0, column=1, sticky='ew', padx=(8, 0))
        ttk.Label(mright, text='LLM', style='Field.TLabel').pack(anchor='w', pady=(0, 5))
        self.llm_var = tk.StringVar(value=self.settings.llm_model)
        ttk.Entry(mright, textvariable=self.llm_var).pack(fill='x')
        lang_wrap = tk.Frame(api, bg=CARD)
        lang_wrap.pack(fill='x', padx=18, pady=(0, 18))
        ttk.Label(lang_wrap, text='Bengali variant', style='Field.TLabel').pack(anchor='w', pady=(0, 5))
        lang = self.settings.language if self.settings.language in LANGUAGES else LANGUAGES[0]
        self.lang_var = tk.StringVar(value=lang)
        lang_btn = ttk.Menubutton(lang_wrap, textvariable=self.lang_var, style='Field.TMenubutton', width=12)
        lang_menu = tk.Menu(lang_btn, tearoff=0, bg=FIELD, fg=TEXT, activebackground=SELECT_BG, activeforeground=TEXT, relief='flat', bd=0, font=self.f_ui)
        for code, label in (('bn-IN', 'bn-IN   Bengali (India)'), ('bn-BD', 'bn-BD   Bengali (Bangladesh)')):
            lang_menu.add_radiobutton(label=label, variable=self.lang_var, value=code)
        lang_btn.config(menu=lang_menu)
        lang_btn.pack(anchor='w')
        instr = ttk.Frame(left, style='Card.TFrame')
        instr.pack(fill='both', expand=True, pady=(0, 16))
        ttk.Label(instr, text='LLM INSTRUCTION  ·  OPTIONAL', style='Section.TLabel').pack(anchor='w', padx=18, pady=(16, 10))
        self.instr_text = tk.Text(instr, height=4, wrap='word', font=self.f_ui, bg=FIELD, fg=TEXT, insertbackground=TEXT, relief='flat', padx=12, pady=10, selectbackground=SELECT_BG, selectforeground=TEXT, highlightthickness=1, highlightbackground=FIELD, highlightcolor=ACCENT)
        self.instr_text.pack(fill='both', expand=True, padx=18, pady=(0, 6))
        self.instr_count = ttk.Label(instr, text='', style='Field.TLabel')
        self.instr_count.pack(anchor='e', padx=18, pady=(0, 14))
        self.instr_text.bind('<KeyRelease>', self._limit_instruction)
        self.instr_text.bind('<<Paste>>', lambda e: self.after(1, self._limit_instruction))
        self._limit_instruction()
        actions = ttk.Frame(left)
        actions.pack(fill='x')
        self.generate_btn = ttk.Button(actions, text='Generate subtitles', style='Accent.TButton', command=self._on_generate)
        self.generate_btn.pack(side='left', fill='x', expand=True)
        self.cancel_btn = ttk.Button(actions, text='Cancel', state='disabled', command=self.cancel_event.set)
        self.cancel_btn.pack(side='left', padx=(12, 0))
        self.progress = ttk.Progressbar(left, maximum=1000)
        self.progress.pack(fill='x', pady=(18, 0))
        self.stage_var = tk.StringVar(value='Ready.')
        self.stage_label = ttk.Label(left, textvariable=self.stage_var, style='Status.TLabel')
        self.stage_label.pack(anchor='w', pady=(10, 0))
        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky='nsew')
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        tabs = tk.Frame(right, bg=BG)
        tabs.grid(row=0, column=0, sticky='w', pady=(0, 0))
        self.tab_widgets: dict[str, tuple[tk.Label, tk.Frame]] = {}
        for key, label in (('bn', 'Bengali'), ('en', 'English')):
            holder = tk.Frame(tabs, bg=BG)
            holder.pack(side='left', padx=(0, 28))
            lbl = tk.Label(holder, text=label, bg=BG, fg=MUTED, font=self.f_tab, cursor='hand2', pady=6)
            lbl.pack()
            underline = tk.Frame(holder, bg=BG, height=max(2, int(2 * self.scale)))
            underline.pack(fill='x')
            lbl.bind('<Button-1>', lambda e, k=key: self._show_tab(k))
            self.tab_widgets[key] = (lbl, underline)
        self.preview_holder = tk.Frame(right, bg=CARD)
        self.preview_holder.grid(row=1, column=0, sticky='nsew', pady=(8, 0))
        self.bn_text = self._preview_text(self.f_preview_bn)
        self.en_text = self._preview_text(self.f_preview_en)
        player_bar = tk.Frame(right, bg=CARD)
        player_bar.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        caption_strip = tk.Frame(player_bar, bg='#0b0d12', height=int(64 * self.scale))
        caption_strip.pack(fill='x')
        caption_strip.pack_propagate(False)
        self.caption = tk.Label(caption_strip, text='', bg='#0b0d12', fg='#f5f6fa', font=(self.f_preview_bn[0], 12), justify='center')
        self.caption.pack(expand=True)
        caption_strip.bind('<Configure>', lambda e: self.caption.config(wraplength=max(200, e.width - int(48 * self.scale))))
        controls = tk.Frame(player_bar, bg=CARD)
        controls.pack(fill='x', padx=14, pady=10)
        self.play_btn = ttk.Button(controls, text='Play', width=7, state='disabled', command=self._toggle_play)
        self.play_btn.pack(side='left')
        self.time_var = tk.StringVar(value='0:00 / 0:00')
        tk.Label(controls, textvariable=self.time_var, bg=CARD, fg=MUTED, font=self.f_label).pack(side='left', padx=(14, 14))
        self.seek_var = tk.DoubleVar(value=0.0)
        self.seek_scale = ttk.Scale(controls, style='Preview.Horizontal.TScale', variable=self.seek_var, from_=0.0, to=1000.0, state='disabled', command=self._on_scrub)
        self.seek_scale.pack(side='left', fill='x', expand=True)
        self.seek_scale.bind('<ButtonPress-1>', self._on_seek_press)
        self.seek_scale.bind('<B1-Motion>', self._on_seek_motion)
        self.seek_scale.bind('<ButtonRelease-1>', self._on_seek_release)
        toolbar = ttk.Frame(right)
        toolbar.grid(row=3, column=0, sticky='ew', pady=(12, 0))
        self.copy_btn = ttk.Button(toolbar, text='Copy SRT', state='disabled', command=self._copy_current)
        self.copy_btn.pack(side='left')
        self.save_btn = ttk.Button(toolbar, text='Save edits', state='disabled', command=self._save_current)
        self.save_btn.pack(side='left', padx=(12, 0))
        self.open_btn = ttk.Button(toolbar, text='Open preview', command=self._open_preview)
        self.open_btn.pack(side='right')
        self.close_btn = ttk.Button(toolbar, text='Close preview', state='disabled', command=self._close_preview)
        self.close_btn.pack(side='right', padx=(0, 12))
        self._show_tab('bn')
    def _card(self, parent: ttk.Frame, title: str) -> ttk.Frame:
        card = ttk.Frame(parent, style='Card.TFrame')
        card.pack(fill='x', pady=(0, 16))
        ttk.Label(card, text=title, style='Section.TLabel').pack(anchor='w', padx=18, pady=(16, 12))
        return card
    def _field(self, card: ttk.Frame, label: str, var: tk.StringVar, browse, show: str='', last: bool=False) -> ttk.Entry:
        wrap = tk.Frame(card, bg=CARD)
        wrap.pack(fill='x', padx=18, pady=(0, 18 if last else 15))
        ttk.Label(wrap, text=label, style='Field.TLabel').pack(anchor='w', pady=(0, 5))
        row = tk.Frame(wrap, bg=CARD)
        row.pack(fill='x')
        entry = ttk.Entry(row, textvariable=var, show=show)
        entry.pack(side='left', fill='x', expand=True)
        if browse:
            ttk.Button(row, text='Browse', command=browse).pack(side='left', padx=(8, 0))
        return entry
    def _preview_text(self, font: tuple) -> tk.Text:
        frame = tk.Frame(self.preview_holder, bg=CARD)
        text = tk.Text(frame, wrap='word', font=font, relief='flat', bg=CARD, fg=TEXT, padx=18, pady=14, insertbackground=TEXT, selectbackground=SELECT_BG, selectforeground=TEXT, spacing1=2, spacing3=2, undo=True)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.config(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        text._frame = frame
        text.tag_configure('cur', background=ACCENT_HOVER, foreground='#ffffff', lmargin1=8, lmargin2=8, rmargin=8, spacing1=3, spacing3=3)
        text.bind('<<Modified>>', self._on_text_modified)
        return text
    def _on_text_modified(self, event) -> None:
        widget = event.widget
        if widget.edit_modified():
            key = 'bn' if widget is self.bn_text else 'en'
            self.cue_dirty[key] = True
            widget.edit_modified(False)
    def _show_tab(self, key: str) -> None:
        self.active_tab = key
        for k, (lbl, underline) in self.tab_widgets.items():
            active = k == key
            lbl.config(fg=TEXT if active else MUTED)
            underline.config(bg=ACCENT if active else BG)
        for k, widget in (('bn', getattr(self, 'bn_text', None)), ('en', getattr(self, 'en_text', None))):
            if widget is None:
                continue
            if k == key:
                widget._frame.pack(fill='both', expand=True)
            else:
                widget._frame.pack_forget()
                widget.tag_remove('cur', '1.0', 'end')
        if hasattr(self, 'caption'):
            family = self.f_preview_bn[0] if key == 'bn' else 'Segoe UI'
            self.caption.config(font=(family, 12))
            self.current_cue = None
    def _toggle_key(self) -> None:
        hidden = self.key_entry.cget('show') == '•'
        if hidden and self.key_var.get().strip():
            self.key_toggle.config(state='disabled')
            self.stage_var.set('Waiting for Windows Hello…')
            self.stage_label.configure(foreground=MUTED)
            try:
                verified = winauth.verify_user('Confirm your identity to reveal the OpenRouter API key.', password_prompt=self._ask_windows_password, pump=self.update)
            finally:
                self.key_toggle.config(state='normal')
                if self.stage_var.get().startswith('Waiting'):
                    self.stage_var.set('Ready.')
            if verified is False:
                self.stage_var.set('Identity not verified. Key stays hidden.')
                self.stage_label.configure(foreground=ERR_RED)
                return
            if verified is None:
                if not messagebox.askyesno('Verification unavailable', 'Windows could not verify your identity (no Windows Hello and password check failed to run).\n\nReveal the API key anyway?'):
                    return
        self.key_entry.config(show='' if hidden else '•')
        self.key_toggle.config(text='Hide' if hidden else 'Show')
    def _ask_windows_password(self, reason: str) -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title('Verify your identity')
        dialog.configure(bg=CARD)
        dialog.transient(self)
        dialog.resizable(False, False)
        _darken_title_bar(dialog)
        tk.Label(dialog, text=reason, bg=CARD, fg=TEXT, font=self.f_ui, wraplength=int(360 * self.scale), justify='left').pack(padx=20, pady=(18, 6), anchor='w')
        tk.Label(dialog, text='Windows account password', bg=CARD, fg=MUTED, font=self.f_label).pack(padx=20, pady=(4, 4), anchor='w')
        pw_var = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=pw_var, show='•', width=34)
        entry.pack(padx=20, fill='x')
        outcome: list[str | None] = [None]
        def submit(_e=None):
            outcome[0] = pw_var.get()
            dialog.destroy()
        row = tk.Frame(dialog, bg=CARD)
        row.pack(fill='x', padx=20, pady=16)
        ttk.Button(row, text='Verify', style='Accent.TButton', command=submit).pack(side='right')
        ttk.Button(row, text='Cancel', command=dialog.destroy).pack(side='right', padx=(0, 10))
        entry.bind('<Return>', submit)
        entry.focus_set()
        dialog.grab_set()
        self.wait_window(dialog)
        return outcome[0]
    def _limit_instruction(self, _event=None) -> None:
        content = self.instr_text.get('1.0', 'end-1c')
        if len(content) > MAX_INSTRUCTION_CHARS:
            self.instr_text.delete('1.0', 'end')
            self.instr_text.insert('1.0', content[:MAX_INSTRUCTION_CHARS])
            content = content[:MAX_INSTRUCTION_CHARS]
        remaining = MAX_INSTRUCTION_CHARS - len(content)
        self.instr_count.config(text=f'{len(content)}/{MAX_INSTRUCTION_CHARS}', foreground=ERR_RED if remaining <= 25 else FAINT)
    def _pick_input(self) -> None:
        path = filedialog.askopenfilename(title='Choose audio or video', filetypes=MEDIA_FILETYPES)
        if path:
            self.input_var.set(path)
            self.output_var.set(os.path.dirname(path))
            if self.result_paths or self.player.loaded:
                self._close_preview(silent=True)
    def _close_preview(self, silent: bool=False) -> None:
        self._reset_player()
        self.result_paths = {}
        self._set_preview(self.bn_text, '')
        self._set_preview(self.en_text, '')
        self._set_running(False)
        if not silent:
            self.stage_var.set('Preview closed.')
            self.stage_label.configure(foreground=MUTED)
    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title='Choose output folder')
        if path:
            self.output_var.set(path)
    def _current_text(self) -> tk.Text:
        return self.bn_text if self.active_tab == 'bn' else self.en_text
    def _copy_current(self) -> None:
        content = self._current_text().get('1.0', 'end-1c')
        if content.strip():
            self.clipboard_clear()
            self.clipboard_append(content)
            self.stage_var.set('Copied to clipboard.')
            self.stage_label.configure(foreground=MUTED)
    def _save_current(self) -> None:
        path = self.result_paths.get(self.active_tab)
        if not path:
            return
        content = self._current_text().get('1.0', 'end-1c')
        if not content.endswith('\n'):
            content += '\n'
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='\n') as f:
                f.write(content)
            self.stage_var.set(f'Saved {os.path.basename(path)}')
            self.stage_label.configure(foreground=OK_GREEN)
        except OSError as exc:
            messagebox.showerror('Save failed', str(exc))
    def _on_generate(self) -> None:
        input_path = self.input_var.get().strip()
        output_dir = self.output_var.get().strip()
        api_key = self.key_var.get().strip() or os.environ.get('OPENROUTER_API_KEY', '')
        instruction = self.instr_text.get('1.0', 'end-1c').strip()[:MAX_INSTRUCTION_CHARS]
        if not input_path:
            messagebox.showwarning('Missing input', 'Choose an audio or video file first.')
            return
        if not api_key:
            messagebox.showwarning('Missing API key', 'Enter your OpenRouter API key (or set OPENROUTER_API_KEY).')
            return
        self.settings.api_key = self.key_var.get().strip()
        self.settings.stt_model = self.stt_var.get().strip() or 'google/chirp-3'
        self.settings.llm_model = self.llm_var.get().strip() or 'google/gemini-3.1-flash-lite'
        self.settings.language = self.lang_var.get() if self.lang_var.get() in LANGUAGES else 'bn-IN'
        self.settings.custom_instruction = ''
        self.settings.last_output_dir = ''
        try:
            self.settings.save()
        except OSError:
            pass
        self.cancel_event.clear()
        self.result_paths = {}
        self._reset_player()
        self._set_running(True)
        self._set_preview(self.bn_text, '')
        self._set_preview(self.en_text, '')
        self._pending_audio = input_path
        settings = self.settings
        cancel_event = self.cancel_event
        events = self.events
        def work() -> None:
            try:
                client = OpenRouterClient(api_key)
                pipeline = Pipeline(transcriber=OpenRouterTranscriptionProvider(client, settings.stt_model), llm=OpenRouterLlmProvider(client, settings.llm_model), progress=lambda label, frac, detail: events.put(('progress', label, frac, detail)), cancel_event=cancel_event, language=settings.language, save_raw_transcript=settings.save_raw_transcript, custom_instruction=instruction)
                result = pipeline.run(input_path, output_dir)
                events.put(('done', result))
            except CancelledError:
                events.put(('cancelled',))
            except PipelineError as exc:
                events.put(('error', str(exc)))
            except Exception as exc:
                events.put(('error', f'Unexpected error: {exc!r}'))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                try:
                    self._handle_event(event)
                except Exception as exc:
                    self.stage_var.set(f'UI error: {exc}')
                    self.stage_label.configure(foreground=ERR_RED)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll_events)
    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == 'progress':
            _, label, frac, detail = event
            self.stage_var.set(f'{label}… {detail}'.rstrip('… '))
            self.stage_label.configure(foreground=MUTED)
            self.progress['value'] = int(frac * 1000)
        elif kind == 'done':
            result = event[1]
            self.progress['value'] = 1000
            self.stage_var.set(f'Done. {result.cue_count} subtitles written.')
            self.stage_label.configure(foreground=OK_GREEN)
            self.result_paths = {'bn': result.bn_path, 'en': result.en_path}
            self._set_preview(self.bn_text, result.bn_srt)
            self._set_preview(self.en_text, result.en_srt)
            self._set_running(False)
            self._load_preview_audio(self._pending_audio)
        elif kind == 'player_ready':
            self._on_player_ready(event[1], event[2])
        elif kind == 'player_error':
            self.stage_var.set(f'Preview unavailable: {event[1]}')
        elif kind == 'cancelled':
            self.stage_var.set('Cancelled.')
            self.stage_label.configure(foreground=MUTED)
            self.progress['value'] = 0
            self._set_running(False)
        elif kind == 'error':
            self.stage_var.set('Failed.')
            self.stage_label.configure(foreground=ERR_RED)
            self._set_running(False)
            messagebox.showerror('Subtitle generation failed', event[1])
    def _load_preview_audio(self, source_path: str) -> None:
        self._audio_token += 1
        token = self._audio_token
        wav = os.path.join(tempfile.gettempdir(), f'bnsrt_preview_{os.getpid()}_{token}.wav')
        def work() -> None:
            try:
                extract_preview_wav(source_path, wav)
                self.events.put(('player_ready', token, wav))
            except PipelineError as exc:
                self.events.put(('player_error', str(exc)))
        threading.Thread(target=work, daemon=True).start()
    def _on_player_ready(self, token: int, wav: str) -> None:
        if token != self._audio_token:
            _try_remove(wav)
            return
        try:
            self.player.load(wav)
            self.player_len = self.player.length()
        except PlayerError as exc:
            self.stage_var.set(f'Preview unavailable: {exc}')
            return
        if self._wav_loaded and self._wav_loaded != wav:
            _try_remove(self._wav_loaded)
        self._wav_loaded = wav
        self.seek_scale.config(state='normal', to=float(max(1, self.player_len)))
        self.seek_var.set(0.0)
        self.play_btn.config(state='normal', text='Play')
        self.time_var.set(f'0:00 / {_fmt_ms(self.player_len)}')
        self._set_running(False)
        if not self.stage_var.get().startswith('Done'):
            self.stage_var.set('Preview ready.')
            self.stage_label.configure(foreground=OK_GREEN)
    def _toggle_play(self) -> None:
        if not self.player.loaded:
            return
        try:
            mode = self.player.mode()
            if mode == 'playing':
                self.player.pause()
                self.play_btn.config(text='Play')
            elif mode == 'paused':
                self.player.resume()
                self.play_btn.config(text='Pause')
            else:
                pos = self.player.position()
                self.player.play(0 if pos >= self.player_len - 100 else pos)
                self.play_btn.config(text='Pause')
        except PlayerError as exc:
            self.stage_var.set(f'Playback error: {exc}')
    def _set_seek_from_x(self, x: int) -> None:
        width = max(1, self.seek_scale.winfo_width())
        frac = min(1.0, max(0.0, x / width))
        self.seek_var.set(frac * float(self.seek_scale.cget('to')))
    def _on_seek_press(self, event) -> str | None:
        if not self.player.loaded:
            return None
        self.slider_drag = True
        self._set_seek_from_x(event.x)
        return 'break'
    def _on_seek_motion(self, event) -> str | None:
        if not self.slider_drag:
            return None
        self._set_seek_from_x(event.x)
        return 'break'
    def _on_seek_release(self, _event) -> str | None:
        if not self.player.loaded:
            return None
        self.slider_drag = False
        target = int(self.seek_var.get())
        try:
            was_playing = self.player.mode() == 'playing'
            self.player.seek(target)
            if was_playing:
                self.player.play()
        except PlayerError as exc:
            self.stage_var.set(f'Playback error: {exc}')
        return 'break'
    def _on_scrub(self, _value) -> None:
        if self.slider_drag:
            pos = int(self.seek_var.get())
            self.time_var.set(f'{_fmt_ms(pos)} / {_fmt_ms(self.player_len)}')
            self._sync_subtitle(pos)
    def _tick(self) -> None:
        try:
            if self.player.loaded and (not self.slider_drag):
                try:
                    pos = self.player.position()
                    mode = self.player.mode()
                except PlayerError:
                    pos, mode = (0, '')
                self.seek_var.set(float(pos))
                self.time_var.set(f'{_fmt_ms(pos)} / {_fmt_ms(self.player_len)}')
                if mode == 'stopped' and self.play_btn.cget('text') == 'Pause':
                    self.play_btn.config(text='Play')
                self._sync_subtitle(pos)
        except Exception:
            pass
        finally:
            self.after(100, self._tick)
    def _cues(self, key: str) -> list:
        if self.cue_dirty[key]:
            widget = self.bn_text if key == 'bn' else self.en_text
            self.cue_cache[key] = parse_srt_with_lines(widget.get('1.0', 'end-1c'))
            self.cue_dirty[key] = False
        return self.cue_cache[key]
    def _sync_subtitle(self, pos_ms: int) -> None:
        key = self.active_tab
        entries = self._cues(key)
        t = pos_ms / 1000.0
        idx = None
        if entries:
            starts = [e[0].start for e in entries]
            i = bisect.bisect_right(starts, t) - 1
            if i >= 0 and entries[i][0].start <= t < entries[i][0].end:
                idx = i
        if self.current_cue == (key, idx):
            return
        self.current_cue = (key, idx)
        widget = self._current_text()
        widget.tag_remove('cur', '1.0', 'end')
        if idx is None:
            self.caption.config(text='')
            return
        cue, first, last = entries[idx]
        self.caption.config(text=cue.text)
        widget.tag_add('cur', f'{first}.0', f'{last}.end')
        if self.focus_get() is not widget:
            total = int(widget.index('end-1c').split('.')[0])
            top, bottom = widget.yview()
            center = (first + last) / 2 / max(1, total)
            widget.yview_moveto(max(0.0, center - (bottom - top) / 2))
    def _open_preview(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('Busy', 'Wait for the current run to finish (or cancel it) before opening a preview.')
            return
        path = filedialog.askopenfilename(title='Open audio, video, or subtitle', filetypes=[('Media or subtitles', '*.srt ' + MEDIA_FILETYPES[0][1]), ('Subtitles', '*.srt'), MEDIA_FILETYPES[0], ('All files', '*.*')])
        if not path:
            return
        pair = find_pair(path)
        if not pair.has_subtitles:
            messagebox.showwarning('No subtitles found', f'No matching _bn.srt or _en.srt was found next to:\n{os.path.basename(path)}')
            return
        self._reset_player()
        self.result_paths = {}
        loaded = []
        for key, srt_path, widget in (('bn', pair.bn, self.bn_text), ('en', pair.en, self.en_text)):
            if srt_path and os.path.exists(srt_path):
                try:
                    with open(srt_path, encoding='utf-8-sig') as f:
                        self._set_preview(widget, f.read())
                    self.result_paths[key] = srt_path
                    loaded.append(os.path.basename(srt_path))
                except OSError as exc:
                    messagebox.showerror('Open failed', f'{srt_path}\n\n{exc}')
                    return
            else:
                self._set_preview(widget, '')
        self._show_tab('bn' if pair.bn else 'en')
        self._set_running(False)
        if pair.audio:
            self.stage_var.set(f"Loaded {', '.join(loaded)}. Preparing audio…")
            self.stage_label.configure(foreground=MUTED)
            self._load_preview_audio(pair.audio)
        else:
            self.stage_var.set(f"Loaded {', '.join(loaded)}. No matching audio file found.")
            self.stage_label.configure(foreground=MUTED)
    def _reset_player(self) -> None:
        self.player.close()
        self.player_len = 0
        self.play_btn.config(state='disabled', text='Play')
        self.seek_scale.config(state='disabled')
        self.seek_var.set(0.0)
        self.time_var.set('0:00 / 0:00')
        self.caption.config(text='')
        self.current_cue = None
    def _set_running(self, running: bool) -> None:
        self.generate_btn.config(state='disabled' if running else 'normal')
        self.cancel_btn.config(state='normal' if running else 'disabled')
        self.open_btn.config(state='disabled' if running else 'normal')
        has_result = bool(self.result_paths)
        self.copy_btn.config(state='normal' if has_result and (not running) else 'disabled')
        self.save_btn.config(state='normal' if has_result and (not running) else 'disabled')
        closable = has_result or self.player.loaded
        self.close_btn.config(state='normal' if closable and (not running) else 'disabled')
        if running:
            self.progress['value'] = 0
            self.stage_var.set('Starting…')
            self.stage_label.configure(foreground=MUTED)
    def _set_preview(self, widget: tk.Text, content: str) -> None:
        widget.delete('1.0', 'end')
        widget.insert('1.0', content)
        widget.edit_reset()
        self.cue_dirty['bn' if widget is self.bn_text else 'en'] = True
    def _on_close(self) -> None:
        self.cancel_event.set()
        self.player.close()
        if self._wav_loaded:
            _try_remove(self._wav_loaded)
        self.destroy()
def _try_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
def _fmt_ms(ms: int) -> str:
    seconds = max(0, int(ms)) // 1000
    return f'{seconds // 60}:{seconds % 60:02d}'
def main() -> None:
    scale = _enable_windows_dpi()
    app = App(scale)
    app.mainloop()
if __name__ == '__main__':
    main()
