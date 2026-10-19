"""Native per-accent appearance editor with a looping animation preview."""
import time
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageTk
from accents import plan_accents
from accents.appearances import APPEARANCES, APPEARANCE_LABELS
from accents.track import event_frame

BG = '#1e1e2e'
FG = '#cdd6f4'
ACCENT = '#89b4fa'


def time_label(seconds):
    milliseconds = max(0, round(seconds * 1000))
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}.{ms:03}'


class AccentEditor(tk.Toplevel):
    def __init__(self, parent, project, major_rate=2):
        super().__init__(parent)
        self.project = project
        self.major_rate = major_rate
        self.events = []
        self._job = None
        self._playing = True
        self._epoch = time.monotonic()
        self._paused_at = 0
        self.title('Появления текстовых акцентов')
        self.configure(bg=BG)
        self.geometry('1100x720')
        self.minsize(900, 650)
        self.transient(parent)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _: self.close())
        self.bind('<Destroy>', self._on_destroy, add='+')
        self._build()
        self._refresh()
        self.grab_set()
        self._tick()

    def _button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, bg='#313244', fg=FG,
                         padx=10, pady=6, font=('Arial', 10))

    def _build(self):
        tk.Label(self, text='Выбери акцент и его появление', bg=BG, fg=ACCENT,
                 font=('Arial', 14, 'bold')).pack(anchor='w', padx=18, pady=(14, 8))
        table = tk.Frame(self, bg=BG)
        table.pack(fill='x', padx=18)
        self.listing = ttk.Treeview(table, columns=('time', 'text', 'style'),
                                   show='headings', height=6, selectmode='browse')
        for key, label, width in [('time', 'Время', 210), ('text', 'Текст из озвучки', 490),
                                  ('style', 'Появление', 310)]:
            self.listing.heading(key, text=label)
            self.listing.column(key, width=width, minwidth=150, stretch=key=='text')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.listing.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.listing.xview)
        self.listing.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.listing.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        table.columnconfigure(0, weight=1)
        self.listing.bind('<<TreeviewSelect>>', self._select)
        self.full_text = tk.StringVar()
        self.full_text_label = tk.Label(self, textvariable=self.full_text, wraplength=1030,
            justify='left', anchor='w', bg=BG, fg=FG, font=('Arial', 11))
        self.full_text_label.pack(fill='x', padx=18, pady=10)
        self.bind('<Configure>', self._resize_text, add='+')

        bottom = tk.Frame(self, bg=BG)
        bottom.pack(fill='both', expand=True, padx=18)
        options = tk.Frame(bottom, bg=BG, width=290)
        options.pack(side='right', fill='y', padx=(18, 0))
        tk.Label(options, text='Появление выбранного акцента', bg=BG, fg=ACCENT,
                 font=('Arial', 11, 'bold')).pack(anchor='w', pady=(0, 8))
        tk.Label(options, text=f'{len(APPEARANCES)-1} вариантов + автоматический выбор',
                 bg=BG, fg=FG).pack(anchor='w', pady=(0, 6))
        self.style_var = tk.StringVar(value=APPEARANCE_LABELS['auto'])
        self.appearance_combo = ttk.Combobox(options, textvariable=self.style_var,
            values=[item[1] for item in APPEARANCES], state='readonly', width=29, height=16)
        self.appearance_combo.pack(fill='x')
        self.appearance_combo.bind('<<ComboboxSelected>>', self._change)
        self._button(options, 'Применить ко всем акцентам', self.apply_all).pack(fill='x', pady=(12, 6))
        self._button(options, 'Вернуть всем автоматический выбор', self.reset_all).pack(fill='x')
        self.play_button = self._button(options, 'Пауза предпросмотра', self.toggle_play)
        self.play_button.pack(fill='x', pady=(18, 8))
        self.status = tk.StringVar(value='Выбор сохраняется автоматически.')
        tk.Label(options, textvariable=self.status, bg=BG, fg='#a6e3a1', justify='left',
                 wraplength=280).pack(anchor='w', pady=6)
        tk.Label(options, text='Текст и таймкоды сохраняются.\nПоиск футажей и цикл переходов остаются прежними.',
                 bg=BG, fg=FG, justify='left', wraplength=280).pack(anchor='w', pady=12)

        preview_area = tk.Frame(bottom, bg=BG)
        preview_area.pack(side='left', fill='both', expand=True)
        tk.Label(preview_area, text='Анимация · условный фон', bg=BG, fg=FG).pack(pady=(0, 4))
        self.preview = tk.Label(preview_area, bg='#101520')
        self.preview.pack(expand=True)
        self._button(self, 'Готово', self.close).pack(pady=10)

    def _selected(self):
        selected = self.listing.selection()
        return next((event for event in self.events if selected and event['id']==selected[0]), None)

    def _resize_text(self, event):
        if event.widget is self:
            self.full_text_label.configure(wraplength=max(300, event.width-36))

    def _refresh(self, selected=None):
        self.events = plan_accents(self.project.segments, major_rate=self.major_rate,
                                  overrides=self.project.accent_overrides)
        self.listing.delete(*self.listing.get_children())
        for event in self.events:
            label = event['style_label']
            if event['appearance']=='auto': label = 'Авто · ' + label
            self.listing.insert('', 'end', iid=event['id'], values=(
                f"{time_label(event['start'])}–{time_label(event['end'])}", event['text'], label))
        if self.events:
            choice = selected if any(e['id']==selected for e in self.events) else self.events[0]['id']
            self.listing.selection_set(choice)
            self.listing.see(choice)
            self._select()

    def _select(self, _=None):
        event = self._selected()
        if event:
            self.style_var.set(APPEARANCE_LABELS[event['appearance']])
            self.full_text.set(event['text'])
            self._paused_at = 0 if self._playing else min(1.5, (event['end']-event['start'])/2)
            self._epoch = time.monotonic() - self._paused_at
            self._paint(self._paused_at)

    def _choice(self):
        return next((key for key, label in APPEARANCE_LABELS.items() if label==self.style_var.get()), 'auto')

    def _save(self, overrides):
        previous = self.project.accent_overrides
        self.project.accent_overrides = overrides
        try:
            self.project.save()
        except OSError as error:
            self.project.accent_overrides = previous
            messagebox.showerror('Не удалось сохранить появление', str(error), parent=self)
            self._refresh(self.listing.selection()[0] if self.listing.selection() else None)
            return False
        return True

    def _change(self, _=None):
        event = self._selected()
        if not event: return
        key = self._choice()
        updated = dict(self.project.accent_overrides)
        if key=='auto': updated.pop(event['id'], None)
        else: updated[event['id']] = {'appearance': key}
        if self._save(updated):
            self._refresh(event['id'])
            self.status.set('Сохранено: ' + APPEARANCE_LABELS[key])

    def apply_all(self):
        key = self._choice()
        selected = self.listing.selection()
        updated = dict(self.project.accent_overrides)
        for event in self.events:
            if key=='auto': updated.pop(event['id'], None)
            else: updated[event['id']] = {'appearance': key}
        if self._save(updated):
            self._refresh(selected[0] if selected else None)
            self.status.set(f'Сохранено для всех акцентов: {APPEARANCE_LABELS[key]}')

    def reset_all(self):
        selected = self.listing.selection()
        if self._save({}):
            self._refresh(selected[0] if selected else None)
            self.status.set('Всем акцентам возвращён автоматический выбор.')

    def toggle_play(self):
        self._playing = not self._playing
        if self._playing:
            self._epoch = time.monotonic() - self._paused_at
        else:
            self._paused_at = time.monotonic() - self._epoch
        self.play_button.configure(text='Пауза предпросмотра' if self._playing else 'Продолжить предпросмотр')

    def _paint(self, elapsed):
        event = self._selected()
        if not event: return
        span = event['end'] - event['start']
        elapsed %= span + .7
        image = Image.new('RGBA', (640, 360), '#233630')
        if elapsed<span:
            image.alpha_composite(event_frame(event, image.size, elapsed, span-elapsed))
        image.thumbnail((640, 300))
        photo = ImageTk.PhotoImage(image.convert('RGB'), master=self)
        self.preview.configure(image=photo)
        self.preview.image = photo

    def _tick(self):
        if self._playing:
            self._paint(time.monotonic() - self._epoch)
        self._job = self.after(60, self._tick)

    def _on_destroy(self, event):
        if event.widget is self and self._job:
            try: self.after_cancel(self._job)
            except tk.TclError: pass
            self._job = None

    def close(self):
        self.grab_release()
        self.destroy()
