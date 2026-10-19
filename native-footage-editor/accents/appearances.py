"""Appearance presets shared by the editor, project storage and export."""
import hashlib

# key, name, layout, motion, treatment, decoration, entrance duration
APPEARANCES = [
    ('auto', 'Автоматически', None, None, None, None, None),
    ('fade', 'Мягкое проявление', 'center', 'soft_fade', 'classic', 'none', .8),
    ('typewriter', 'Печатная машинка', 'center', 'typewriter', 'classic', 'none', 1.0),
    ('from_left', 'Выезд слева', 'center', 'from_left', 'classic', 'none', .7),
    ('from_right', 'Выезд справа', 'center', 'from_right', 'classic', 'none', .7),
    ('from_bottom', 'Подъём снизу', 'center', 'from_bottom', 'classic', 'none', .7),
    ('from_top', 'Спуск сверху', 'center', 'from_top', 'classic', 'none', .7),
    ('zoom_in', 'Приближение текста', 'center', 'text_zoom_in', 'classic', 'none', .8),
    ('zoom_out', 'Из крупного плана', 'center', 'text_zoom_out', 'classic', 'none', .8),
    ('pop', 'Пружина', 'center', 'pop', 'classic', 'none', .8),
    ('bounce', 'Подскок снизу', 'center', 'bounce', 'classic', 'none', .9),
    ('focus', 'Из размытия в фокус', 'center', 'focus', 'classic', 'none', .9),
    ('perspective', 'Перспектива', 'center', 'perspective', 'classic', 'none', .8),
    ('reveal_left', 'Раскрытие слева', 'center', 'reveal', 'classic', 'none', .8),
    ('reveal_right', 'Раскрытие справа', 'center', 'reveal_right', 'classic', 'none', .8),
    ('split', 'Раскрытие из центра', 'center', 'split', 'classic', 'none', .8),
    ('stamp', 'Штамп', 'center', 'stamp', 'classic', 'none', .55),
    ('tilt', 'С поворотом', 'center', 'tilt', 'classic', 'none', .8),
    ('stretch', 'Вертикальное раскрытие', 'center', 'stretch', 'classic', 'none', .7),
    ('caption', 'Нижняя подпись', 'label', 'typewriter', 'classic', 'none', .65),
    ('card', 'Карточка с выездом', 'card', 'from_bottom', 'classic', 'none', .8),
    ('underline', 'Текст с подчёркиванием', 'center', 'reveal', 'classic', 'underline', .8),
    ('ring', 'Текст с обводкой', 'center', 'reveal', 'classic', 'ring', .8),
    ('red_ink', 'Красный акцент с обводкой', 'center', 'reveal', 'red_ink', 'ring', .85),
    ('neon', 'Неоновое свечение', 'center', 'reveal', 'glow', 'none', .85),
    ('chromatic', 'Цветная тень', 'center', 'stamp', 'color_shadow', 'none', .8),
    ('marquee', 'Бегущие строки на сетке', 'grid', 'marquee', 'classic', 'none', .6),
]
APPEARANCE_BY_KEY = {item[0]: item for item in APPEARANCES}
APPEARANCE_LABELS = {item[0]: item[1] for item in APPEARANCES}
APPEARANCE_KEYS = list(APPEARANCE_BY_KEY)


def accent_id(event, fps=25):
    """Stable across reopening, rate changes and changes to the footage."""
    source = f"{round(event['start'] * fps)}:{' '.join(event['text'].casefold().split())}"
    return 'accent_' + hashlib.sha256(source.encode('utf-8')).hexdigest()[:20]


def clean_overrides(value):
    if not isinstance(value, dict):
        return {}
    return {key: {'appearance': item['appearance']}
            for key, item in value.items()
            if isinstance(key, str) and key.startswith('accent_')
            and isinstance(item, dict) and isinstance(item.get('appearance'), str)
            and item['appearance'] in APPEARANCE_BY_KEY
            and item['appearance'] != 'auto'}


def apply_appearance(event, key):
    result = dict(event)
    if key not in APPEARANCE_BY_KEY:
        key = 'auto'
    result['appearance'] = key
    if key == 'auto':
        return result
    _, label, layout, effect, treatment, decoration, duration = APPEARANCE_BY_KEY[key]
    result.update(layout=layout, effect=effect, treatment=treatment,
                  decoration=decoration, duration=duration, style_label=label)
    return result
