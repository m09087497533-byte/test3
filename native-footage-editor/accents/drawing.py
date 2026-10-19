"""Shared Kadr typography, bundled for offline use."""
from pathlib import Path
from functools import lru_cache
from PIL import ImageFont
FONTS=Path(__file__).resolve().parent/"fonts"

@lru_cache(maxsize=256)
def font(size: int, serif=False,condensed=False,heavy=False):
    name='DejaVuSerif-Bold.ttf' if serif and heavy else 'DejaVuSerif.ttf' if serif else 'DejaVuSansCondensed-Bold.ttf' if condensed else 'DejaVuSans-Bold.ttf'
    return ImageFont.truetype(str(FONTS/name),size)


def wrapped(text: str, face, width: int) -> str:
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split():
            candidate = f'{line} {word}'.strip()
            if face.getlength(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = ''
            for char in word:
                if face.getlength(line + char) > width and line:
                    lines.append(line)
                    line = ''
                line += char
        lines.append(line)
    return '\n'.join(lines)


def text_block(draw, text, box, size, fill, serif=False, center=False,condensed=False,heavy=False):
    if not text.strip():
        return
    x, y, width, height = box
    for current in range(max(1, size), 0, -1):
        face = font(current, serif,condensed,heavy)
        value = wrapped(text, face, width)
        spacing = max(4, current // 5)
        bounds = draw.multiline_textbbox((0, 0), value, font=face, spacing=spacing)
        if bounds[3] - bounds[1] <= height:
            break
    if center:
        x += (width - (bounds[2] - bounds[0])) / 2
        y += (height - (bounds[3] - bounds[1])) / 2
    draw.multiline_text((x, y - bounds[1]), value, font=face, fill=fill,
                        spacing=spacing, align='center' if center else 'left')
