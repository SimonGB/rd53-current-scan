"""Settings-based estimate refined by observed point-to-point durations."""
import math
from statistics import median


def duration(seconds):
    seconds=max(0,int(math.ceil(seconds)))
    hours,rest=divmod(seconds,3600);minutes,seconds=divmod(rest,60)
    if hours:return f'{hours}h {minutes:02d}m'
    if minutes:return f'{minutes}m {seconds:02d}s'
    return f'{seconds}s'


class ProgressEstimate:
    def __init__(self,total,per_point,startup=0,finalization=2):
        self.total=total;self.per_point=per_point;self.startup=startup;self.finalization=finalization
        self.started=None;self.last_point=None;self.completed=0;self.intervals=[]
    def start(self,now):self.started=now;self.last_point=now
    def point(self,now):
        if self.completed:self.intervals.append(max(0,now-self.last_point))
        self.last_point=now;self.completed+=1
    def remaining(self,now):
        if self.started is None:return self.startup+self.total*self.per_point+self.finalization
        if self.completed>=self.total:return None
        if not self.completed:return max(0,self.startup+self.total*self.per_point-(now-self.started))+self.finalization
        rate=median(self.intervals[-5:]) if self.intervals else self.per_point
        return max(0,(self.total-self.completed)*rate-(now-self.last_point))+self.finalization
