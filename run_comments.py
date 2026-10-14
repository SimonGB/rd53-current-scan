"""Thread-safe live run notes with durable timestamped revision history."""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading


class RunComments:
    def __init__(self, text=''):
        self.lock=threading.RLock();self.text=text;self.directory=None
        self.events=[];self.last_completed_point=None;self.metadata=None
        self._record()

    def _record(self):
        self.events.append(dict(revision=len(self.events),timestamp_UTC=datetime.now(timezone.utc).isoformat(),
            last_completed_point=self.last_completed_point,comments=self.text))

    def bind(self,directory):
        with self.lock:
            self.directory=Path(directory)
            self._write()

    def _write(self):
        if self.directory is None:return
        files=[('comments.txt',self.text),('comments_history.json',json.dumps(self.events,indent=2)+'\n')]
        if self.metadata is not None:
            self.metadata.update(comments=self.text,comments_revision=self.events[-1]['revision'])
            files.append(('metadata.json',json.dumps(self.metadata,indent=2)+'\n'))
        for name,text in files:
            target=self.directory/name;temporary=target.with_name(name+'.tmp')
            temporary.write_text(text,encoding='utf-8');temporary.replace(target)

    def update(self,text):
        with self.lock:
            if text==self.text:return
            self.text=text;self._record();self._write()

    def snapshot(self):
        with self.lock:return self.text,self.events[-1]['revision']

    def finalize(self,metadata):
        with self.lock:
            self.metadata=dict(metadata);self._write()

    def point_completed(self,index):
        with self.lock:self.last_completed_point=index
