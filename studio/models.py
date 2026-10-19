from typing import Literal

from pydantic import BaseModel, Field, model_validator
from .limits import MAX_PROJECT_SECONDS, MAX_SCENES, MAX_SUBTITLES, DURATION_LIMIT_MESSAGE


class TextEvent(BaseModel):
    text: str = Field(min_length=1,max_length=160)
    start: float = Field(ge=0,le=30)
    end: float = Field(gt=0,le=30)
    duration: float = Field(default=.45,ge=.033333,le=3)
    effect: Literal['fade','scroll','perspective','reveal','stamp','typewriter'] = 'fade'
    layout: Literal['center','number','grid','label','counter'] = 'center'
    decoration: Literal['none','ring','underline'] = 'none'
    elapsed: float = Field(default=0,ge=0,le=10)
    treatment: Literal['classic','neon','chromatic','italic'] = 'classic'

    @model_validator(mode='after')
    def ordered(self):
        if self.end<=self.start:raise ValueError('Конец акцента должен быть после начала')
        return self


class Scene(BaseModel):
    template: Literal['cinema', 'statement', 'archive', 'quote', 'thesis'] = 'cinema'
    title: str = Field(default='', max_length=160)
    caption: str = Field(default='', max_length=240)
    media_reason: str = Field(default='', max_length=400)
    media_query: str = Field(default='', max_length=200)
    duration: float = Field(default=4, ge=1, le=30)
    asset_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    offset: float = Field(default=0, ge=0, le=36000)
    automatic_text: bool = False
    picture_effect: Literal['plain', 'inset', 'split'] = 'plain'
    document_title: str = Field(default='',max_length=100)
    document_caption: str = Field(default='',max_length=160)
    text_events: list[TextEvent] = Field(default_factory=list,max_length=12)
    title_layout: Literal['caption', 'center', 'number', 'grid', 'label', 'folio', 'counter'] = 'caption'
    title_end: float | None = Field(default=None, gt=0, le=30)
    motion: bool = True
    motion_style: Literal['push', 'pull', 'left', 'right'] = 'push'
    transition_out: Literal['inherit', 'cut', 'dissolve', 'slide', 'flash', 'blur', 'wipe', 'light_leak', 'color_wash', 'zoom'] = 'inherit'


    title_effect: Literal['inherit', 'static', 'fade', 'rise', 'slide', 'scroll', 'perspective', 'reveal', 'stamp', 'typewriter'] = 'inherit'
    title_delay: float = Field(default=0, ge=0, le=29)
    title_duration: float = Field(default=.3, ge=.033333, le=3)
    title_decoration: Literal['none', 'ring', 'underline'] = 'none'
    title_exit: bool = False
    title_sound: Literal['none', 'click', 'impact', 'swish', 'paper', 'sweep', 'chime', 'stamp', 'spark'] = 'none'

    @model_validator(mode='after')
    def title_timing(self):
        if (self.title or self.caption) and round(self.title_delay*30) >= round(self.duration*30):
            raise ValueError('Появление заголовка должно быть раньше конца сцены')
        if self.title_end is not None and not self.title_delay < self.title_end <= self.duration:
            raise ValueError('Конец показа заголовка должен быть после появления и до конца сцены')
        if any(e.end>self.duration+.001 for e in self.text_events):raise ValueError('Текстовый акцент выходит за сцену')
        return self


class Subtitle(BaseModel):
    start: float = Field(ge=0, le=MAX_PROJECT_SECONDS)
    end: float = Field(gt=0, le=MAX_PROJECT_SECONDS)
    text: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('Конец субтитра должен быть позже начала')
        return self


class Project(BaseModel):
    version: Literal[1] = 1
    name: str = Field(default='Новая история', min_length=1, max_length=100)
    library_since: float = Field(default=0, ge=0)
    scenes: list[Scene] = Field(min_length=1, max_length=MAX_SCENES)
    auto_media_mode: Literal['mixed', 'photos', 'videos'] = 'mixed'
    auto_media_after_subtitles: bool = True
    semantic_media: bool = False
    effects_version: int = Field(default=0, ge=0, le=18)
    director_version: int = Field(default=0, ge=0, le=12)
    show_subtitles: bool = True
    photo_motion: Literal['static', 'smooth'] = 'static'
    resolution: Literal['720p', '1080p'] = '720p'
    transition: Literal['cut', 'fade', 'mixed', 'dissolve', 'slide', 'flash', 'blur', 'wipe', 'light_leak', 'color_wash', 'zoom'] = 'fade'
    # Old JSON/API projects retain manual behavior; the UI starts new projects in auto.
    sound_mode: Literal['auto', 'manual', 'off'] = 'manual'
    sounds_per_minute: float = Field(default=4, ge=0, le=12)
    transition_sounds: bool = False
    sfx_volume: float = Field(default=0.35, ge=0, le=1)
    animate_titles: bool = False
    voice_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    music_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    auto_music: bool = False
    music_volume: float = Field(default=0.15, ge=0, le=1)
    subtitles: list[Subtitle] = Field(default_factory=list, max_length=MAX_SUBTITLES)

    @model_validator(mode='after')
    def limit_duration(self):
        if sum(round(s.duration*30) for s in self.scenes) > MAX_PROJECT_SECONDS*30:
            raise ValueError(DURATION_LIMIT_MESSAGE)
        duration = sum(round(s.duration * 30) for s in self.scenes) / 30
        if any(s.end > duration + .04 for s in self.subtitles):
            raise ValueError('Субтитры выходят за длительность сцен. Увеличьте длительность монтажа.')
        return self


def frame_count(scene: Scene) -> int:
    return round(scene.duration * 30)


def total_duration(project: Project) -> float:
    return sum(frame_count(s) for s in project.scenes) / 30
