"""Transparent animated titles, independently composited over the scene."""
import math
import subprocess
from PIL import Image, ImageDraw, ImageFilter

ADVANCED = {'scroll', 'perspective', 'reveal'}


def perspective_coefficients(destination, source):
    rows = []
    for (x,y),(u,v) in zip(destination,source):
        rows.extend([[x,y,1,0,0,0,-u*x,-u*y,u], [0,0,0,x,y,1,-v*x,-v*y,v]])
    for col in range(8):
        pivot = max(range(col,8),key=lambda row:abs(rows[row][col]))
        rows[col],rows[pivot] = rows[pivot],rows[col]
        factor = rows[col][col]
        if abs(factor)<1e-10:
            raise ValueError('Degenerate title perspective')
        rows[col] = [v/factor for v in rows[col]]
        for row in range(8):
            if row!=col:
                factor = rows[row][col]
                rows[row] = [a-factor*b for a,b in zip(rows[row],rows[col])]
    return tuple(row[-1] for row in rows)


def title_frame(layer, scene, effect, elapsed):
    w,h=layer.size
    duration=max(1,round(scene.title_duration*30))/30
    p=max(0,min(1,elapsed/duration))
    ease=1-(1-p)**3
    box=layer.getbbox()
    if not box:
        return layer.copy()
    x0,y0,x1,y1=box
    result=Image.new('RGBA',layer.size)
    if effect=='perspective' and p<1:
        # A foreshortened plane rises from below and straightens to the final title.
        center=(x0+x1)/2
        top_width=(x1-x0)*(0.68+0.32*ease)
        bottom_width=(x1-x0)*(1.25-.25*ease)
        top=y0+(1-ease)*h*.24
        bottom=top+(y1-y0)*(.20+.80*ease)
        dst=[(center-top_width/2,top),(center+top_width/2,top),
             (center+bottom_width/2,bottom),(center-bottom_width/2,bottom)]
        src=[(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
        result=layer.transform(layer.size,Image.Transform.PERSPECTIVE,perspective_coefficients(dst,src),Image.Resampling.BICUBIC)
    elif effect=='reveal':
        # Clip left to right, preserving glyph positions and line wrapping.
        right=round(x0+(x1-x0)*ease)
        if right>x0: result.paste(layer.crop((0,0,right,h)),(0,0))
    else:
        dx=round(w*(1-ease)) if effect=='scroll' else -round(w*(1-ease)) if effect=='slide' else 0
        dy=round(h*.16*(1-ease)) if effect=='rise' else 0
        result.paste(layer,(dx,dy))
    if effect not in ('static','reveal') and p<1:
        if effect in ('scroll','perspective'):
            result=result.filter(ImageFilter.GaussianBlur((1-ease)*w/500))
        result.putalpha(result.getchannel('A').point(lambda v:round(v*min(1,p*5))))
    # Draw one continuous, slightly irregular stroke around the settled title.
    if scene.title_decoration!='none':
        q=max(0,min(1,(elapsed-duration*.65)/.65))
        if q:
            d=ImageDraw.Draw(result)
            if scene.title_decoration=='underline':
                d.line((x0,y1+h*.018,x0+(x1-x0)*q,y1+h*.018),fill='#ef9e64',width=max(2,round(w/400)))
            else:
                cx,cy=(x0+x1)/2,(y0+y1)/2
                rx,ry=(x1-x0)/2+w*.018,(y1-y0)/2+h*.035
                points=[]
                for i in range(max(2,round(180*q))):
                    angle=-.35+2*math.pi*1.06*i/179
                    points.append((cx+rx*math.cos(angle),cy+ry*math.sin(angle)+h*.005*math.sin(angle*3)))
                d.line(points,fill='#ef9e64',width=max(2,round(w/550)),joint='curve')
    return result


def encode_title(layer, scene, effect, output):
    """Only encode entrance frames; FFmpeg holds the final frame for the scene."""
    duration=max(1,round(scene.title_duration*30))/30
    end=duration if scene.title_decoration=='none' else max(duration,duration*.65+.65)
    frames=math.ceil(end*30)+1
    w,h=layer.size
    command=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgba','-s',f'{w}x{h}',
             '-r','30','-i','pipe:0','-an','-c:v','qtrle','-threads','2',str(output)]
    with subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE) as process:
        try:
            for frame in range(frames):
                process.stdin.write(title_frame(layer,scene,effect,frame/30).tobytes())
            process.stdin.close()
            errors=process.stderr.read()
            if process.wait(timeout=120):
                raise RuntimeError(errors.decode(errors='replace'))
        except BrokenPipeError:
            try:process.stdin.close()
            except BrokenPipeError:pass
            errors=process.stderr.read().decode(errors='replace')
            code=process.wait(timeout=30)
            raise subprocess.CalledProcessError(code or 1,command,stderr=errors) from None
        except BaseException:
            process.kill()
            process.wait()
            raise
