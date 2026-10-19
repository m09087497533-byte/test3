"""Readable multi-line text with a small drift over a stationary grid."""
from functools import lru_cache
from PIL import Image, ImageDraw
from .drawing import font


def _balanced_rows(words, face, rows, max_width):
    """Break at word boundaries and balance the rendered line widths."""
    space = face.getlength(' ')
    prefix = [0.0]
    for word in words:
        prefix.append(prefix[-1] + face.getlength(word) + space)
    target = (prefix[-1] - rows * space) / rows
    costs = {(0, 0): (0.0, None)}
    for row in range(1, rows + 1):
        for end in range(row, len(words) + 1):
            for start in range(row - 1, end):
                previous = costs.get((row - 1, start))
                width = prefix[end] - prefix[start] - space
                if previous is None or width > max_width:
                    continue
                cost = previous[0] + (width - target) ** 2
                if (row, end) not in costs or cost < costs[row, end][0]:
                    costs[row, end] = (cost, start)
    if (rows, len(words)) not in costs:
        return None
    result = []
    end = len(words)
    for row in range(rows, 0, -1):
        start = costs[row, end][1]
        result.append(' '.join(words[start:end]))
        end = start
    return tuple(reversed(result))


@lru_cache(maxsize=128)
def marquee_layout(text, size):
    words = text.upper().split()
    if not words:
        return (), 1, 0
    w, h = size
    rows = min(len(words), 4 if len(text) > 70 else 3 if len(text) > 32 else 2)
    draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    for point_size in range(max(1, round(88 * w / 1280)), 0, -1):
        face = font(point_size, condensed=True)
        lines = _balanced_rows(words, face, rows, w * .80)
        if lines is None:
            continue
        spacing = max(2, round(point_size * .20))
        bounds = draw.multiline_textbbox((0, 0), '\n'.join(lines), font=face, spacing=spacing, align='center')
        if bounds[2]-bounds[0] <= w*.80 and bounds[3]-bounds[1] <= h*.62:
            return lines, point_size, spacing
    # Extremely long unbroken tokens still retain all their characters.
    return (' '.join(words),), 1, 0


@lru_cache(maxsize=16)
def _glyphs(text, size):
    layer = Image.new('RGBA', size)
    lines, point_size, spacing = marquee_layout(text, size)
    if not lines:
        return layer
    draw = ImageDraw.Draw(layer)
    face = font(point_size, condensed=True)
    value = '\n'.join(lines)
    bounds = draw.multiline_textbbox((0, 0), value, font=face, spacing=spacing, align='center')
    x = (size[0] - (bounds[2]-bounds[0]))/2 - bounds[0]
    y = (size[1] - (bounds[3]-bounds[1]))/2 - bounds[1]
    draw.multiline_text((x, y), value, font=face, spacing=spacing, align='center', fill='#e0ed13')
    return layer


def marquee_frame(text, size, elapsed, span):
    # The complete phrase stays within the picture for the entire event.
    # Total travel is 2.4% of the frame, independent of the phrase's length.
    progress = min(1, max(0, elapsed / max(.1, span)))
    shift = round(size[0] * .012 * (1 - 2 * progress))
    layer = Image.new('RGBA', size)
    layer.paste(_glyphs(text, size), (shift, 0))
    return layer
