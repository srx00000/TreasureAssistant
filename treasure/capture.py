"""Capture tickets and rectangle placement; all Tk operations stay on its UI thread."""
from dataclasses import dataclass, field
import threading
import time


def overlaps(a,b):
    return (a['left'] < b['left']+b['width'] and b['left'] < a['left']+a['width'] and
            a['top'] < b['top']+b['height'] and b['top'] < a['top']+a['height'])


def safe_position(region,width,height,screen,margin=14):
    left,top=screen['left'],screen['top']
    right,bottom=left+screen['width'],top+screen['height']
    y=max(top,min(region['top'],bottom-height))
    x=max(left,min(region['left'],right-width))
    candidates=[(region['left']-width-margin,y),
                (region['left']+region['width']+margin,y),
                (x,region['top']-height-margin),
                (x,region['top']+region['height']+margin)]
    for x,y in candidates:
        rect={'left':x,'top':y,'width':width,'height':height}
        if x>=left and y>=top and x+width<=right and y+height<=bottom and not overlaps(rect,region):
            return x,y
    return None


@dataclass(eq=False)
class CaptureTicket:
    region:dict
    done:threading.Event=field(default_factory=threading.Event)
    image:object=None
    error:Exception|None=None
    cancelled:bool=False

    def finish(self,image=None,error=None):
        self.image=image;self.error=error;self.done.set()


def request_frame(events,region,cancel,timeout=10):
    ticket=CaptureTicket(dict(region));events.put(('capture',ticket))
    deadline=time.monotonic()+timeout
    while not ticket.done.wait(.05):
        if cancel.is_set() or time.monotonic()>deadline:
            ticket.cancelled=True
            raise RuntimeError('Capture cancelled' if cancel.is_set() else 'Capture timed out')
    if ticket.error:raise ticket.error
    if ticket.cancelled or cancel.is_set():raise RuntimeError('Capture cancelled')
    return ticket.image
