"""
Эффекты для сегментов видео: затемнение, Кен Бёрнс, приближение/отдаление,
панорама, растворение между клипами.

В UI это чекбоксы (можно выбрать несколько сразу) — выбранные эффекты
раздаются по сегментам по кругу (ротация), как в слайдшоу-приложении.
"""

# (ключ, подпись, цвет для чекбокса) — порядок важен для ротации
EFFECTS = [
    ("none", "Без эффекта", "#6c7086"),
    ("fade", "Затемнение", "#89b4fa"),
    ("ken_burns_ur", "Кен Бёрнс ↗", "#cba6f7"),
    ("ken_burns_dl", "Кен Бёрнс ↙", "#f5c2e7"),
    ("dissolve", "Растворение", "#a6e3a1"),
    ("zoom_in", "Приближение", "#fab387"),
    ("zoom_out", "Отдаление", "#f9e2af"),
    ("pan_right", "Панорама →", "#94e2d5"),
    ("pan_left", "Панорама ←", "#89dceb"),
    ("pan_down", "Панорама ↓", "#eba0ac"),
    ("pan_up", "Панорама ↑", "#b4befe"),
    ("light_leak", "Тёплая засветка", "#ffbd87"),
    ("color_wash", "Цветной перелив", "#f5a4e0"),
    ("transition_zoom", "Зум-переход", "#d7b5ff"),
    ("transition_slide", "Плавный сдвиг", "#94e2d5"),
    ("transition_blur", "Через размытие", "#b4befe"),
    ("transition_wipe", "Шторка", "#89dceb"),
    ("transition_flash", "Световая вспышка", "#f9e2af"),
]

EFFECT_LABELS = {key: label for key, label, _ in EFFECTS}
EFFECT_KEYS = [key for key, _, _ in EFFECTS]

# Like the existing dissolve, these effects are incoming segment transitions.
# Camera effects stay inside their segment and keep their original behavior.
TRANSITION_STYLES = {
    'dissolve':'fade', 'light_leak':'custom', 'color_wash':'custom',
    'transition_zoom':'zoomin', 'transition_slide':'smoothleft',
    'transition_blur':'hblur', 'transition_wipe':'wipeleft',
    'transition_flash':'fadewhite',
}


def transition_filter(effect, duration, offset):
    base=f'xfade=transition={TRANSITION_STYLES.get(effect,"fade")}:duration={duration:.8f}:offset={offset:.8f}'
    if effect not in ('light_leak','color_wash'):return base
    pulse='16*P*P*(1-P)*(1-P)'
    if effect=='light_leak':
        prefix=f'st(0,{pulse});st(1,(X/W-(1-P))/.40);st(2,ld(0)/(1+ld(1)*ld(1)*ld(1)*ld(1)));'
        extra='if(eq(PLANE,0),160,if(eq(PLANE,1),-50,80))*ld(2)'
    else:
        prefix=(f'st(0,{pulse});st(1,(X/W-(1-P))/.32);st(2,(X/W-P)/.40);'
                'st(3,ld(0)/(1+ld(1)*ld(1)*ld(1)*ld(1)));st(4,ld(0)/(1+ld(2)*ld(2)*ld(2)*ld(2)));')
        extra='if(eq(PLANE,0),70*(ld(3)+ld(4)),if(eq(PLANE,1),85*ld(4)-35*ld(3),70*ld(3)-50*ld(4)))'
    return base+f":expr='{prefix}clip(A*P+B*(1-P)+{extra},0,255)'"


def assign_effects(num_segments: int, selected: list[str]) -> list[str]:
    """
    Раздаёт выбранные эффекты по сегментам по кругу.
    Ничего не выбрано -> у всех 'none'.
    Выбрано 2 -> чередуются через один. Выбраны все -> идут по кругу.
    """
    selected = [e for e in (selected or []) if e in EFFECT_LABELS]
    if not selected:
        return ["none"] * num_segments
    return [selected[i % len(selected)] for i in range(num_segments)]
