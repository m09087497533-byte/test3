"""A continuous frame clock for source photos, footage and complete document cards."""
from .models import frame_count


def camera_filter(scene, size, *, alpha=False):
    w,h=size
    progress=f'(0.5-0.5*cos(PI*min(on,{max(1,frame_count(scene)-1)})/{max(1,frame_count(scene)-1)}))'
    amount=scene.camera_amount
    style=scene.camera_style
    if style in ('hold','inherit') or not amount:
        return 'format=rgba' if alpha else 'setsar=1'
    zoom=f'1+{amount}*{progress}'
    if style=='pull':zoom=f'1+{amount}*(1-{progress})'
    elif style in ('left','right'):zoom=f'1+{amount}'
    elif style=='detail':
        beat=scene.camera_beat if scene.camera_beat is not None else scene.duration*.50
        local=f'min(1,max(0,(on/30-{max(0,beat-.45)})/.9))'
        zoom=f'1+{amount*.35}*{progress}+{amount*.65}*(.5-.5*cos(PI*{local}))'
    x='(iw-iw/zoom)/2'
    if style in ('left','right'):
        x=f'(iw-iw/zoom)*{progress}' if style=='left' else f'(iw-iw/zoom)*(1-{progress})'
    pixel='format=rgba' if alpha else 'setsar=1'
    return (f"scale={w*2}:{h*2}:flags=lanczos,zoompan=z='{zoom}':x='{x}':"
            f"y='(ih-ih/zoom)/2':d=1:s={w}x{h}:fps=30,{pixel}")
