"""
Проект = аудио + сегменты транскрипта + подобранные под них футажи.
Сохраняется рядом с аудио в папке <имя_аудио>_project/.
"""

import json
import os
import tempfile
import threading
from pathlib import Path


class Project:
    def __init__(self, audio_path: Path):
        self.audio_path = Path(audio_path)
        self.project_dir = self.audio_path.parent / f"{self.audio_path.stem}_project"
        self.footage_dir = self.project_dir / "footage"
        self.thumbs_dir = self.project_dir / "thumbs"
        self.project_file = self.project_dir / "project.json"
        self.segments: list[dict] = []
        self.story_context: dict = {}
        self._save_lock = threading.RLock()
        self.effects: list[str] = []  # выбранные (чекбоксы) ключи эффектов, порядок = порядок ротации
        self.text_accents = True
        self.accent_sounds = False
        self.accent_rate = 2
        self.accent_overrides = {}

    def ensure_dirs(self):
        self.footage_dir.mkdir(parents=True, exist_ok=True)
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)

    def set_segments_from_transcript(self, segments: list[dict]):
        """
        Строит новый список сегментов из транскрибации/импорта субтитров.
        Если для сегмента с ТАКИМ ЖЕ текстом уже был подобран футаж раньше
        (повторный импорт того же файла, например после переоткрытия проекта) —
        переносим его, а не стираем. Совпадающий текст может встречаться
        несколько раз — сопоставляем по порядку появления.
        """
        old_by_text: dict[str, list[dict]] = {}
        for old in self.segments:
            old_by_text.setdefault(old["text"], []).append(old)

        new_segments = []
        for i, seg in enumerate(segments):
            carried = None
            bucket = old_by_text.get(seg["text"])
            if bucket:
                carried = bucket.pop(0)

            new_segments.append({
                **(carried or {}),
                "index": i,
                "text": seg["text"],
                "start": seg["start"],
                "end": seg["end"],
                "speaker": seg.get("speaker"),
                "candidates": carried.get("candidates", []) if carried else [],
                "footage_path": carried.get("footage_path") if carried else None,
                "footage_source": carried.get("footage_source") if carried else None,  # "pexels" | "pixabay" | "local"
                "footage_query": carried.get("footage_query") if carried else None,
                "_selected_candidate_id": carried.get("_selected_candidate_id") if carried else None,
            })

        carried_count = sum(1 for s in new_segments if s["footage_path"])
        self.segments = new_segments
        # Context-dependent plans are recalculated through content-keyed caches.
        for segment in self.segments:
            segment.pop("scene_plan", None)
        return carried_count

    def save(self):
        with self._save_lock:
            self.project_dir.mkdir(parents=True, exist_ok=True)
            data = {"schema_version": 4, "audio_path": str(self.audio_path),
                    "segments": self.segments, "effects": self.effects,
                    "text_accents": self.text_accents, "accent_sounds": self.accent_sounds,
                    "accent_rate": self.accent_rate,
                    "accent_overrides": self.accent_overrides,
                    "story_context": self.story_context}
            path = None
            try:
                with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.project_dir,
                                                 suffix='.tmp', delete=False) as file:
                    path = Path(file.name)
                    json.dump(data, file, ensure_ascii=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                path.replace(self.project_file)
            finally:
                if path:
                    path.unlink(missing_ok=True)

    def load(self) -> bool:
        if not self.project_file.exists():
            return False
        data = json.loads(self.project_file.read_text(encoding="utf-8"))
        self.segments = data.get("segments", [])
        self.effects = data.get("effects", [])
        self.text_accents = data.get('text_accents', True) is not False
        self.accent_sounds = data.get('accent_sounds', False) is True
        try:self.accent_rate=max(0,min(4,int(data.get('accent_rate',2))))
        except (TypeError,ValueError):self.accent_rate=2
        from accents.appearances import clean_overrides
        self.accent_overrides=clean_overrides(data.get('accent_overrides'))
        self.story_context = data.get("story_context", {})
        for i, segment in enumerate(self.segments):
            segment["index"] = i
        return True

    def all_footage_assigned(self) -> bool:
        return bool(self.segments) and all(s.get("footage_path") for s in self.segments)
