"""AI Footage Editor: local speech recognition, local image ranking, stock retrieval."""
if __name__ == '__main__':
    from bootstrap import enter_runtime
    enter_runtime()


import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from PIL import Image, ImageTk

import transcribe
import subtitles
import footage
import render
import effects
from accents import plan_accents
from project import Project
from semantic import SemanticSelector, safe_error


BG = "#1e1e2e"
PANEL = "#313244"
FG = "#cdd6f4"
ACCENT = "#89b4fa"
GREEN = "#a6e3a1"
DIM = "#6c7086"
YELLOW = "#f9e2af"


def _fmt_vtt(seconds: float) -> str:
    """3.5 -> '00:00:03.500' — тот же формат, что в .vtt (HH:MM:SS.mmm)."""
    total_ms = max(0, round(seconds * 1000))
    seconds, ms = divmod(total_ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}"


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("🎬 AI Footage Editor — переходы и акценты")
        self.root.geometry("1180x820")
        self.root.configure(bg=BG)
        self.root.minsize(1000, 650)

        self.project: Project | None = None
        self.selected_index: int | None = None
        self._thumb_refs = {}  # candidate_id -> ImageTk.PhotoImage (чтобы не собрал GC)
        self._autopick_stop_requested = False

        self._busy = False
        self._buttons = []
        self._ui_queue = queue.Queue()
        self.selector = None
        self._build_ui()
        self.root.after(50, self._poll_ui)

    # ───────────────────────── UI ─────────────────────────

    def _build_ui(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=14, pady=(14, 6))

        tk.Label(top, text="🎬 AI Footage Editor", font=("Arial", 18, "bold"),
                 bg=BG, fg=ACCENT).pack(side="left")

        self.audio_label = tk.Label(top, text="Аудио не выбрано", bg=BG, fg=DIM, font=("Arial", 11))
        self.audio_label.pack(side="left", padx=(20, 8))

        self.controls_bar = tk.Frame(self.root, bg=BG)
        self.controls_bar.pack(fill="x", padx=14, pady=3)
        self.audio_btn = self._btn(self.controls_bar, "📂 Аудио", ACCENT, self._pick_audio)
        self.transcribe_btn = self._btn(self.controls_bar, "📝 Транскрибировать", GREEN, self._run_transcribe)
        self.import_vtt_btn = self._btn(self.controls_bar, "📄 Импорт субтитров", "#45475a", self._import_subtitles)
        self.autopick_btn = self._btn(self.controls_bar, "✨ Подобрать все футажи", YELLOW, self._auto_pick_all)
        self.render_btn = self._btn(self.controls_bar, "🚀 Собрать видео", "#f38ba8", self._render)
        self.primary_buttons = [self.audio_btn, self.transcribe_btn, self.import_vtt_btn,
                                self.autopick_btn, self.render_btn]
        for button in self.primary_buttons:
            button.pack(side="left", padx=(0, 8))

        status_row = tk.Frame(self.root, bg=BG)
        status_row.pack(fill="x", padx=14, pady=(2, 0))
        self.transcribe_status_label = tk.Label(status_row, text="Транскрипция: —",
                                                  bg=BG, fg=DIM, font=("Arial", 10, "bold"))
        self.transcribe_status_label.pack(side="left")
        self.footage_status_label = tk.Label(status_row, text="", bg=BG, fg=DIM, font=("Arial", 10, "bold"))
        self.footage_status_label.pack(side="left", padx=(20, 0))
        self._btn(status_row, "Переподобрать сток", YELLOW,
                  lambda: self._auto_pick_all(replace_existing=True)).pack(side="right")

        self.stop_autopick_btn = self._btn(status_row, "⏹ Стоп", "#f38ba8", self._stop_autopick)
        # Stop stays in the status row so the five main actions keep one row.

        tk.Label(self.root, text="Эффекты (можно несколько — чередуются по кругу между сегментами)",
                 bg=BG, fg=DIM, font=("Arial", 10)).pack(anchor="w", padx=14, pady=(6, 0))
        effects_frame = tk.Frame(self.root, bg=BG)
        effects_frame.pack(fill="x", padx=12, pady=(2, 4))

        self.effect_vars: dict[str, tk.BooleanVar] = {}
        self._effect_checkboxes = []
        for effect_index, (key, label, color) in enumerate(effects.EFFECTS):
            var = tk.BooleanVar(value=False)
            self.effect_vars[key] = var
            checkbox = tk.Checkbutton(
                effects_frame, text=label, variable=var,
                bg=BG, fg=color, selectcolor=PANEL,
                activebackground=BG, activeforeground=color,
                font=("Arial", 10), cursor="hand2",
                command=self._on_effects_changed,
            )
            checkbox.grid(row=effect_index // 6, column=effect_index % 6, sticky="w", padx=(0, 8))
            self._effect_checkboxes.append(checkbox)

        accent_row=tk.Frame(self.root,bg=BG)
        accent_row.pack(fill='x',padx=14,pady=(2,4))
        self.text_accents_var=tk.BooleanVar(value=True)
        self.accent_sounds_var=tk.BooleanVar(value=False)
        self.accent_rate_var=tk.StringVar(value='2')
        self._accent_controls=[]
        for text,var in [('Текстовые акценты · автоматически',self.text_accents_var),('Звуки акцентов',self.accent_sounds_var)]:
            check=tk.Checkbutton(accent_row,text=text,variable=var,bg=BG,fg=FG,selectcolor=PANEL,
                activebackground=BG,activeforeground=FG,command=self._on_accents_changed)
            check.pack(side='left',padx=(0,10));self._accent_controls.append(check)
        tk.Label(accent_row,text='Крупных / мин:',bg=BG,fg=DIM).pack(side='left')
        rate=tk.Spinbox(accent_row,from_=0,to=4,width=2,textvariable=self.accent_rate_var,
            command=self._on_accents_changed,bg=PANEL,fg=FG,readonlybackground=PANEL,state='readonly')
        rate.pack(side='left',padx=(4,14));self._accent_controls.append(rate)
        self._btn(accent_row,'План акцентов',ACCENT,self._show_accents).pack(side='left')
        tk.Label(accent_row,text='Только из текста озвучки',bg=BG,fg=DIM,font=('Arial',9)).pack(side='left',padx=12)

        activity_row = tk.Frame(self.root, bg=BG)
        activity_row.pack(fill="x", padx=14, pady=(2, 0))
        tk.Label(activity_row, text="Сейчас:", bg=BG, fg=DIM, font=("Arial", 10)).pack(side="left")
        self.activity_label = tk.Label(activity_row, text="ничего не выполняется",
                                        bg=BG, fg=ACCENT, font=("Arial", 10, "bold"))
        self.activity_label.pack(side="left", padx=(6, 0))
        self.rotation_label=tk.Label(activity_row,text='Цикл: без эффектов',bg=BG,fg=DIM,font=('Arial',10))
        self.rotation_label.pack(side='right')

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", padx=14)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=8)

        # ── левая колонка: список сегментов ──
        left = tk.Frame(body, bg=BG, width=430)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(left, text="Сегменты речи", bg=BG, fg=ACCENT, font=("Arial", 12, "bold")).pack(anchor="w")

        cols = ("time", "text", "footage")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=22)
        self.tree.heading("time", text="Тайм")
        self.tree.heading("text", text="Текст")
        self.tree.heading("footage", text="Футаж")
        self.tree.column("time", width=95, anchor="w")
        self.tree.column("text", width=215, anchor="w")
        self.tree.column("footage", width=90, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=6)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_segment)

        # ── правая колонка: детали сегмента ──
        right_shell=tk.Frame(body,bg=BG)
        right_shell.pack(side='left',fill='both',expand=True,padx=(14,0))
        right_scroll=ttk.Scrollbar(right_shell,orient='vertical')
        right_scroll.pack(side='right',fill='y')
        self.details_canvas=tk.Canvas(right_shell,bg=BG,highlightthickness=0,yscrollcommand=right_scroll.set)
        self.details_canvas.pack(side='left',fill='both',expand=True)
        right_scroll.configure(command=self.details_canvas.yview)
        right=tk.Frame(self.details_canvas,bg=BG)
        details_window=self.details_canvas.create_window((0,0),window=right,anchor='nw')
        right.bind('<Configure>',lambda e:self.details_canvas.configure(scrollregion=self.details_canvas.bbox('all')))
        self.details_canvas.bind('<Configure>',lambda e:self.details_canvas.itemconfigure(details_window,width=e.width))
        def scroll_details(event):
            widget=self.root.winfo_containing(event.x_root,event.y_root)
            while widget is not None:
                if widget is self.candidates_canvas or isinstance(widget,tk.Text):return
                if widget is right:
                    if event.delta:self.details_canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
                    return
                widget=getattr(widget,'master',None)
        self.root.bind_all('<MouseWheel>',scroll_details,add='+')

        tk.Label(right, text="Сегмент", bg=BG, fg=ACCENT, font=("Arial", 12, "bold")).pack(anchor="w")

        self.seg_time_label = tk.Label(right, text="—", bg=BG, fg=DIM, font=("Arial", 10))
        self.seg_time_label.pack(anchor="w", pady=(2, 4))

        self.seg_text = tk.Text(right, height=3, bg=PANEL, fg=FG, wrap="word",
                                 relief="flat", padx=8, pady=6, font=("Arial", 11))
        self.seg_text.pack(fill="x")
        self.seg_text.config(state="disabled")
        tk.Label(right, text="Свой запрос для поиска (необязательно, RU или EN)", bg=BG, fg=DIM).pack(anchor="w")
        self.custom_query = tk.Entry(right, bg=PANEL, fg=FG, insertbackground=FG)
        self.custom_query.pack(fill="x", pady=3)

        actions = tk.Frame(right, bg=BG)
        actions.pack(fill="x", pady=8)
        self._btn(actions, "🔎 Искать футаж", ACCENT, self._search_selected).pack(side="left")
        self._btn(actions, "🔁 Ещё варианты", "#45475a", lambda: self._search_selected(more=True)).pack(side="left", padx=6)
        self._btn(actions, "📁 Свой файл...", "#45475a", self._pick_local_footage).pack(side="left")

        self.current_footage_label = tk.Label(right, text="Футаж не выбран", bg=BG, fg=DIM, font=("Arial", 10))
        self.current_footage_label.pack(anchor="w")
        self.reason_label = tk.Label(right, text="", wraplength=650, justify="left", bg=BG, fg=YELLOW, font=("Arial", 10))
        self.reason_label.pack(anchor="w")

        tk.Label(right, text="Варианты футажей (клик по картинке = выбрать)",
                 bg=BG, fg=DIM, font=("Arial", 10)).pack(anchor="w", pady=(10, 2))

        self.candidates_canvas = tk.Canvas(right, bg=BG, height=210, highlightthickness=0)
        self.candidates_canvas.pack(fill="x")
        candidates_hscroll = ttk.Scrollbar(right, orient="horizontal", command=self.candidates_canvas.xview)
        candidates_hscroll.pack(fill="x")
        self.candidates_canvas.configure(xscrollcommand=candidates_hscroll.set)

        self.candidates_frame = tk.Frame(self.candidates_canvas, bg=BG)
        self._candidates_window = self.candidates_canvas.create_window(
            (0, 0), window=self.candidates_frame, anchor="nw"
        )
        self.candidates_frame.bind(
            "<Configure>",
            lambda _e: self.candidates_canvas.configure(scrollregion=self.candidates_canvas.bbox("all")),
        )
        # прокрутка колёсиком мыши / трекпадом (Shift+колесо тоже работает как горизонтальная)
        self.candidates_canvas.bind(
            "<MouseWheel>",
            lambda e: self.candidates_canvas.xview_scroll(int(-1 * (e.delta / 40)), "units"),
        )

        tk.Label(right, text="Лог", bg=BG, fg=ACCENT, font=("Arial", 11, "bold")).pack(anchor="w", pady=(14, 2))
        self.log = scrolledtext.ScrolledText(right, height=6, bg="#181825", fg=GREEN,
                                              font=("Courier", 10), relief="flat")
        self.log.pack(fill="both", expand=True)

    def _btn(self, parent, text, color, cmd):
        button = tk.Button(parent, text=text, command=cmd, bg=color, fg=BG,
                           font=("Arial", 10, "bold"), relief="flat", padx=10, pady=6, cursor="hand2")
        self._buttons.append(button)
        return button

    def _ui(self, function):
        if threading.current_thread() is threading.main_thread():
            function()
        else:
            self._ui_queue.put(function)

    def _poll_ui(self):
        try:
            for _ in range(100):
                try:
                    function = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    function()
                except tk.TclError:
                    pass  # a thumbnail widget may have been removed while downloading
        finally:
            self.root.after(50, self._poll_ui)

    def _begin_task(self):
        self._busy = True
        for button in self._buttons:
            if button is not self.stop_autopick_btn:
                button.config(state="disabled")
        for checkbox in self._effect_checkboxes:
            checkbox.config(state="disabled")
        for widget in self._accent_controls:widget.config(state='disabled')
        self.progress.start()

    def _finish_task(self):
        self._busy = False
        self.progress.stop()
        for button in self._buttons:
            button.config(state="normal")
        for checkbox in self._effect_checkboxes:
            checkbox.config(state="normal")
        for widget in self._accent_controls:widget.config(state='readonly' if isinstance(widget,tk.Spinbox) else 'normal')
        self.stop_autopick_btn.pack_forget()
        self._set_activity("ничего не выполняется")
        self._refresh_tree()

    def _log(self, msg):
        def show():
            self.log.insert("end", str(msg) + "\n")
            self.log.see("end")
        self._ui(show)

    def _set_activity(self, msg):
        self._ui(lambda: self.activity_label.config(text=msg, fg=ACCENT if msg != "ничего не выполняется" else DIM))

    def _update_status_labels(self):
        """Обновляет строку 'Транскрипция: ...' и 'Футажи: X/Y' — всегда на виду, без лога."""
        if not self.project or not self.project.segments:
            self.transcribe_status_label.config(text="Транскрипция: не выполнена", fg=DIM)
            self.footage_status_label.config(text="")
            return

        n = len(self.project.segments)
        self.transcribe_status_label.config(text=f"✅ Транскрипция: готово ({n} сегмент(ов))", fg=GREEN)

        done = sum(1 for s in self.project.segments if s.get("footage_path"))
        review = sum(1 for s in self.project.segments if s.get("selection_status") == "review")
        color = GREEN if done == n else (YELLOW if done else DIM)
        self.footage_status_label.config(text=f"🎬 Футажи: {done}/{n}; проверить: {review}", fg=color)

    # ───────────────────────── аудио / транскрибация ─────────────────────────

    def _selected_effects(self) -> list[str]:
        """Ключи эффектов, у которых чекбокс включён — в порядке EFFECTS (порядок ротации)."""
        return [key for key, _, _ in effects.EFFECTS if self.effect_vars[key].get()]

    def _set_effect_checkboxes(self, selected: list[str]):
        selected = set(selected or [])
        for key, var in self.effect_vars.items():
            var.set(key in selected)
        self._paint_effect_cycle()

    def _on_effects_changed(self):
        self._paint_effect_cycle()
        if not self.project:
            return
        self.project.effects = self._selected_effects()
        self.project.save()

    def _paint_effect_cycle(self):
        selected=self._selected_effects()
        self.rotation_label.config(text=f'Цикл: {len(selected)} эффектов · по кругу' if selected else 'Цикл: без эффектов')

    def _sync_accent_controls(self):
        self.text_accents_var.set(self.project.text_accents)
        self.accent_sounds_var.set(self.project.accent_sounds)
        self.accent_rate_var.set(str(self.project.accent_rate))

    def _on_accents_changed(self):
        if not self.project:return
        self.project.text_accents=self.text_accents_var.get()
        self.project.accent_sounds=self.accent_sounds_var.get()
        self.project.accent_rate=int(self.accent_rate_var.get())
        self.project.save()

    def _show_accents(self):
        if not self.project or not self.project.segments:
            return messagebox.showinfo('Акценты','Сначала распознай речь или импортируй SRT/VTT.')
        events=plan_accents(self.project.segments,major_rate=int(self.accent_rate_var.get()))
        if not events:return messagebox.showinfo('Акценты','В этом тексте пока нет подходящих коротких акцентов. Переходы и футажи сохраняются.')
        from accent_editor import AccentEditor
        self.accent_editor = AccentEditor(self.root, self.project,
                                          major_rate=int(self.accent_rate_var.get()))

    def _pick_audio(self):
        if self._busy:
            return
        p = filedialog.askopenfilename(filetypes=[("Аудио", "*.mp3 *.wav *.aac *.m4a"), ("Все", "*.*")])
        if not p:
            return
        audio_path = Path(p)
        self.project = Project(audio_path)
        self.project.ensure_dirs()
        self.selected_index = None
        self._thumb_refs.clear()
        self.selector = SemanticSelector(self.project)
        self.seg_text.config(state="normal")
        self.seg_text.delete("1.0", "end")
        self.seg_text.config(state="disabled")
        self.seg_time_label.config(text="—")
        self.current_footage_label.config(text="Футаж не выбран", fg=DIM)
        self.reason_label.config(text="")
        self._render_candidates({})
        self.audio_label.config(text=audio_path.name, fg=GREEN)

        if self.project.load():
            self._log(f"📂 Загружен существующий проект: {self.project.project_file}")
            self._set_effect_checkboxes(self.project.effects)
            self._refresh_tree()
        else:
            self._log("📂 Новый проект. Нажми «Транскрибировать».")
            self._set_effect_checkboxes([])
            self.tree.delete(*self.tree.get_children())
            self._update_status_labels()
        self._sync_accent_controls()

    def _import_subtitles(self):
        if self._busy:
            return
        if not self.project:
            return messagebox.showwarning("Внимание", "Сначала выбери аудио!")
        p = filedialog.askopenfilename(
            title="Выбери файл субтитров",
            filetypes=[("Субтитры", "*.vtt *.srt"), ("Все", "*.*")],
        )
        if not p:
            return

        self._begin_task()
        self._set_activity("импортирую субтитры...")
        self._log(f"📄 Импортирую субтитры: {Path(p).name}")

        def work():
            try:
                result = subtitles.import_subtitle_file(p)
                if not result["segments"]:
                    self._log("⚠️ В файле не нашлось ни одной реплики.")
                    return

                carried = self.project.set_segments_from_transcript(result["segments"])
                self.project.save()
                self._log(f"✅ Импортировано {len(result['segments'])} сцен")
                if carried:
                    self._log(f"   ♻️ Футажи для {carried} сцен перенесены с прошлого раза (текст совпал)")

                # сверяем с длиной аудио — если файл явно не от этой дорожки, предупредим
                last_end = result["segments"][-1]["end"]
                audio_dur = None
                try:
                    import subprocess
                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1:nokey=1", str(self.project.audio_path)],
                        capture_output=True, text=True, timeout=15,
                    )
                    audio_dur = float(probe.stdout.strip())
                except Exception:
                    pass
                if audio_dur and abs(audio_dur - last_end) > 5:
                    self._log(f"⚠️ Длина аудио ({audio_dur:.0f}с) заметно отличается от конца субтитров "
                               f"({last_end:.0f}с) — точно тот файл выбран?")

                self._ui(self._refresh_tree)
            except Exception as e:
                self._log(f"❌ {e}")
            finally:
                self._ui(self._finish_task)

        threading.Thread(target=work, daemon=True).start()

    def _run_transcribe(self):
        if self._busy:
            return
        if not self.project:
            return messagebox.showwarning("Внимание", "Сначала выбери аудио!")
        self._begin_task()
        self._set_activity("транскрибирую аудио...")
        self._log("📝 Транскрибирую...")

        def work():
            try:
                result = transcribe.transcribe_audio(
                    self.project.audio_path,
                    progress_cb=lambda m: (self._log(f"   {m}"), self._set_activity(m)),
                )
                if not result["segments"]:
                    self._log("⚠️ Транскрипт пуст.")
                    return
                carried = self.project.set_segments_from_transcript(result["segments"])
                self.project.save()
                self._log(f"✅ Готово: {len(result['segments'])} сегментов")
                if carried:
                    self._log(f"   ♻️ Футажи для {carried} сегментов перенесены с прошлого раза (текст совпал)")
                self._ui(self._refresh_tree)
            except Exception as e:
                self._log(f"❌ {e}")
            finally:
                self._ui(self._finish_task)

        threading.Thread(target=work, daemon=True).start()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        if not self.project:
            return
        for seg in self.project.segments:
            footage_status = "⚠️" if seg.get("selection_status") == "review" else ("✅" if seg.get("footage_path") else "—")
            time_str = _fmt_vtt(seg['start'])
            preview = seg["text"][:40] + ("…" if len(seg["text"]) > 40 else "")
            self.tree.insert("", "end", iid=str(seg["index"]), values=(time_str, preview, footage_status))
        self._update_status_labels()
        if self.selected_index is not None and self.selected_index < len(self.project.segments):
            self.tree.selection_set(str(self.selected_index))
            self._on_select_segment(None)

    # ───────────────────────── выбор сегмента ─────────────────────────

    def _on_select_segment(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self.selected_index = idx
        seg = self.project.segments[idx]

        self.seg_time_label.config(text=f"#{idx + 1}   {_fmt_vtt(seg['start'])} --> {_fmt_vtt(seg['end'])}")
        self.seg_text.config(state="normal")
        self.seg_text.delete("1.0", "end")
        self.seg_text.insert("1.0", seg["text"])
        self.seg_text.config(state="disabled")

        if seg.get("footage_path"):
            name = Path(seg["footage_path"]).name
            src = seg.get("footage_source") or "?"
            self.current_footage_label.config(text=f"Текущий футаж ({src}): {name}", fg=GREEN)
        else:
            self.current_footage_label.config(text="Футаж не выбран", fg=DIM)

        chosen = seg.get("selection_details") or {}
        reason = chosen.get("reason", "")
        if seg.get("selection_status") == "review":
            reason = "Нужна ручная проверка. " + reason
        self.reason_label.config(text=reason)
        self.custom_query.delete(0, "end")
        self.custom_query.insert(0, seg.get("custom_query", ""))
        self._render_candidates(seg)

    def _render_candidates(self, seg: dict):
        for w in self.candidates_frame.winfo_children():
            w.destroy()

        candidates = seg.get("candidates") or []
        if not candidates:
            tk.Label(self.candidates_frame, text="Пока пусто — нажми «Искать футаж»",
                     bg=BG, fg=DIM, font=("Arial", 10)).pack(anchor="w")
            return

        for cand in candidates:
            cell = tk.Frame(self.candidates_frame, bg=BG)
            cell.pack(side="left", padx=4)

            img_label = tk.Label(cell, bg=PANEL, cursor="hand2")
            img_label.pack()
            self._load_thumb_async(cand, img_label)

            tk.Label(cell, text=cand.get("provider", "?"), bg=BG, fg=DIM,
                     font=("Arial", 8)).pack()

            description = f"{cand.get('match_label', 'Нужна проверка')} · {cand.get('score', 0):.1f}"
            tk.Label(cell, text=description, wraplength=170, bg=BG, fg=YELLOW, font=("Arial", 9)).pack()
            tk.Label(cell, text=cand.get('reason', '')[:160], wraplength=170, justify="left",
                     bg=BG, fg=FG, font=("Arial", 9)).pack()

            is_selected = seg.get("footage_source") == cand.get("provider") and \
                str(seg.get("_selected_candidate_id")) == str(cand["id"])
            mark = "✅" if is_selected else "Выбрать"
            btn = tk.Button(cell, text=mark, font=("Arial", 8), bg="#45475a", fg=FG,
                             relief="flat", cursor="hand2",
                             command=lambda c=cand: self._select_candidate(c))
            btn.pack(fill="x", pady=(2, 0))
            img_label.bind("<Button-1>", lambda _e, c=cand: self._select_candidate(c))

    def _load_thumb_async(self, cand, label_widget):
        project = self.project
        def show(img):
            if self.project is project and label_widget.winfo_exists():
                photo = ImageTk.PhotoImage(img)
                self._thumb_refs[cand['id']] = photo
                label_widget.config(image=photo, width=150, height=84)
        def work():
            try:
                thumb_path = project.thumbs_dir / f"{cand['id']}.jpg"
                if not thumb_path.exists():
                    footage.download_preview(cand['preview_image'], thumb_path)
                with Image.open(thumb_path) as source:
                    img = source.convert('RGB').resize((150,84))
                self._ui(lambda: show(img))
            except Exception:
                pass
        threading.Thread(target=work,daemon=True).start()

    # ───────────────────────── поиск / выбор футажа ─────────────────────────

    def _search_selected(self, more=False):
        if self._busy:
            return
        if self.selected_index is None:
            return messagebox.showinfo("Инфо", "Сначала выбери сегмент.")
        idx = self.selected_index
        seg = self.project.segments[idx]
        seg['custom_query'] = self.custom_query.get().strip()
        self.project.save()
        self._begin_task()
        self._set_activity(f"ищу футаж — сегмент {idx+1}")
        def work():
            try:
                candidates, warnings = self.selector.search_segment(idx, more=more, log=self._log)
                for warning in warnings:
                    self._log('⚠️ '+warning)
                self._log(f'Найдено {len(candidates)} вариантов; проверены и отсортированы по смыслу')
            except Exception as exc:
                self._log('Ошибка локального подбора: '+safe_error(exc))
            finally:
                self._ui(self._finish_task)
        threading.Thread(target=work,daemon=True).start()

    def _assign(self, seg, candidate, path, manual=False):
        seg.update(footage_path=str(path), footage_source=candidate.get('provider'),
                   footage_query=candidate.get('query'), _selected_candidate_id=candidate['id'],
                   selected_manually=manual, selection_status='manual' if manual else 'assigned',
                   selection_details={key:candidate.get(key) for key in
                                      ('score','evidence','match_label','reason','observed')})
        self.project.save()

    def _select_candidate(self, cand):
        if self._busy or self.selected_index is None:
            return
        idx = self.selected_index
        seg = self.project.segments[idx]
        self._begin_task()
        self._set_activity(f'скачиваю футаж — сегмент {idx+1}')
        def work():
            try:
                path = footage.download_footage(cand['video_url'],self.project.footage_dir / f"{cand['id']}.mp4")
                self._assign(seg,cand,path,manual=True)
                self._log(f'Футаж для сцены {idx+1} выбран вручную')
            except Exception as exc:
                self._log('Ошибка скачивания: '+safe_error(exc))
            finally:
                self._ui(self._finish_task)
        threading.Thread(target=work,daemon=True).start()

    def _pick_local_footage(self):
        if self._busy:
            return
        if self.selected_index is None:
            return messagebox.showinfo("Инфо", "Сначала выбери сегмент в списке слева.")
        idx = self.selected_index
        p = filedialog.askopenfilename(filetypes=[("Видео", "*.mp4 *.mov *.avi *.mkv"), ("Все", "*.*")])
        if not p:
            return
        seg = self.project.segments[idx]
        seg["footage_path"] = p
        seg["footage_source"] = "local"
        seg["footage_query"] = None
        seg["_selected_candidate_id"] = None
        seg["selected_manually"] = True
        seg["selection_status"] = "manual"
        seg["selection_details"] = {"reason": "Выбран свой файл"}
        self.project.save()
        self._log(f"📁 Сегменту #{idx + 1} назначен свой файл: {Path(p).name}")
        self._refresh_tree()
        self._on_select_segment(None)

    # ───────────────────────── автоподбор всех сегментов ─────────────────────────

    def _stop_autopick(self):
        self._autopick_stop_requested = True
        self._log("⏸ Останавливаю после текущего сегмента (уже готовое — сохранено)...")

    def _auto_pick_all(self, replace_existing=False):
        if self._busy:
            return
        if not self.project or not self.project.segments:
            return messagebox.showwarning("Внимание", "Сначала транскрибируй аудио или импортируй субтитры.")
        self._autopick_stop_requested = False
        self._begin_task()
        self.stop_autopick_btn.pack(side="left",padx=8)
        self._log('Подбираю клипы локальной моделью и сравниваю реальные кадры...')
        def work():
            try:
                total=len(self.project.segments)
                for index,seg in enumerate(self.project.segments):
                    if self._autopick_stop_requested:
                        break
                    exists = seg.get('footage_path') and Path(seg['footage_path']).exists()
                    if exists and (not replace_existing or seg.get('selected_manually') or seg.get('footage_source')=='local'):
                        continue
                    try:
                        self._set_activity(f'подбираю футаж — сцена {index+1}/{total}')
                        candidates,warnings=self.selector.search_segment(index,
                            stop=lambda:self._autopick_stop_requested,log=self._log)
                        for warning in warnings:
                            self._log('⚠️ '+warning)
                        candidate,path=footage.download_best(candidates,self.project.footage_dir,
                            log=self._log,stop=lambda:self._autopick_stop_requested)
                        if candidate:
                            self._assign(seg,candidate,path)
                            self._log(f"Сцена {index+1}: {candidate['match_label']} — {candidate['reason']}")
                        elif not self._autopick_stop_requested:
                            seg['selection_status']='review'
                            self.project.save()
                            self._log(f'Сцена {index+1}: нет подтверждённого подходящего клипа; нужна проверка')
                        self._ui(self._update_status_labels)
                    except InterruptedError:
                        break
                    except Exception as exc:
                        seg['selection_status']='review'
                        self.project.save()
                        self._log(f'Сцена {index+1}: {safe_error(exc)}')
                review=sum(s.get('selection_status')=='review' for s in self.project.segments)
                self._log(('Подбор остановлен' if self._autopick_stop_requested else 'Подбор завершён')+
                          f'. Нужна проверка: {review}. Результаты сохранены.')
            finally:
                self._ui(self._finish_task)
        threading.Thread(target=work,daemon=True).start()

    # ───────────────────────── рендер ─────────────────────────

    def _render(self):
        if self._busy:
            return
        if not self.project or not self.project.segments:
            return messagebox.showwarning("Внимание", "Сначала транскрибируй аудио!")
        missing = [i + 1 for i, s in enumerate(self.project.segments) if not s.get("footage_path")]
        if missing:
            proceed = messagebox.askyesno(
                "Не все футажи выбраны",
                f"У {len(missing)} сегмент(ов) нет футажа (будет чёрный экран). Продолжить?"
            )
            if not proceed:
                return

        self._begin_task()
        self._set_activity("собираю видео...")
        selected = self._selected_effects()
        resolved_effects = effects.assign_effects(len(self.project.segments), selected)
        if selected:
            self._log(f"🚀 Собираю видео (эффекты: {', '.join(effects.EFFECT_LABELS[e] for e in selected)})...")
        else:
            self._log("🚀 Собираю видео...")

        def work():
            try:
                out_dir = self.project.project_dir
                out_path = out_dir / "final_video.mp4"
                render.build_video(
                    self.project.segments, self.project.audio_path, out_path,
                    effects=resolved_effects,
                    text_accents=self.project.text_accents,accent_rate=self.project.accent_rate,
                    accent_sounds=self.project.accent_sounds,
                    accent_overrides=self.project.accent_overrides,
                    log_fn=lambda m: (self._log(m), self._set_activity(m.strip())),
                )
                self._log(f"🎉 Готово! {out_path.resolve()}")
                self._ui(lambda: messagebox.showinfo("Готово!", f"Видео: {out_path.resolve()}"))
            except Exception as e:
                self._log(f"❌ {e}")
            finally:
                self._ui(self._finish_task)

        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
