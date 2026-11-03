"""Atomic, versioned owner overrides. Imported before Settings, without app startup."""
import copy
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


class ConfigConflict(ValueError): pass
class ConfigUnavailable(ValueError): pass


def default_path():
    return Path(os.environ.get('SPEDA_DATA_DIR',str(Path.home()/'.speda')))/'configuration.json'


def read_state(path=None):
    path=Path(path) if path else default_path()
    if not path.exists(): return {'revision':0,'values':{},'receipts':{},'restarts':{}}
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or type(value.get('revision')) is not int or value['revision']<0 or not isinstance(value.get('values'),dict): raise ValueError()
        if not isinstance(value.get('receipts',{}),dict) or not isinstance(value.get('restarts',{}),dict): raise ValueError()
        return value
    except (ValueError,OSError): raise ConfigUnavailable('Saved configuration is unavailable or malformed; restore its retained copy.') from None


def managed_settings():
    return read_state()['values']


class ConfigStore:
    def __init__(self,path=None): self.path=Path(path) if path else default_path()
    def read(self): return read_state(self.path)

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with open(self.path.with_suffix('.lock'),'a+b') as handle:
            handle.seek(0,2)
            if handle.tell()==0: handle.write(b'0'); handle.flush()
            handle.seek(0)
            end=time.monotonic()+3
            while True:
                try:
                    if os.name=='nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic()>=end: raise ConfigUnavailable('Configuration is busy; retry the same saved request.') from None
                    time.sleep(.02)
            try: yield
            finally:
                if os.name=='nt':
                    handle.seek(0); msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else: fcntl.flock(handle,fcntl.LOCK_UN)

    def write(self,state):
        fd,name=tempfile.mkstemp(dir=self.path.parent,prefix='.config-',suffix='.tmp')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump(state,stream,ensure_ascii=False,allow_nan=False,indent=2)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(name,self.path)
        finally: Path(name).unlink(missing_ok=True)

    def save(self,revision,values,request_id,reset=()):
        fingerprint=hashlib.sha256(json.dumps([values,sorted(reset)],sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        with self.locked():
            state=self.read(); prior=state.get('receipts',{}).get(request_id)
            if prior:
                if prior['hash']!=fingerprint: raise ConfigConflict('Save identity belongs to different configuration.')
                return copy.deepcopy(state),True
            if state['revision']!=revision: raise ConfigConflict('Configuration changed; reload before saving.')
            if self.path.exists():
                folder=self.path.parent/'configuration-history'; folder.mkdir(exist_ok=True,mode=0o700)
                backup=folder/f"revision-{revision}.json"
                if not backup.exists():
                    fd,name=tempfile.mkstemp(dir=folder,prefix='.revision-',suffix='.tmp')
                    try:
                        with os.fdopen(fd,'wb') as stream:
                            stream.write(self.path.read_bytes()); stream.flush(); os.fsync(stream.fileno())
                        os.replace(name,backup)
                    finally: Path(name).unlink(missing_ok=True)
            current=dict(state['values']); current.update(values)
            for key in reset: current.pop(key,None)
            receipts=dict(list(state.get('receipts',{}).items())[-63:])
            receipts[request_id]={'hash':fingerprint,'revision':revision+1}
            state.update(revision=revision+1,values=current,receipts=receipts)
            self.write(state)
            return copy.deepcopy(state),False

    def restart(self,revision,request_id,instance_id):
        with self.locked():
            state=self.read(); prior=state.get('restarts',{}).get(request_id)
            if prior:
                if prior['revision']!=revision: raise ConfigConflict('Restart identity belongs to another configuration revision.')
                return prior
            if state['revision']!=revision: raise ConfigConflict('Saved configuration changed; reload before restarting.')
            entry={'revision':revision,'instance_id':instance_id}
            # Restart does not advance the configuration revision. Retaining
            # every accepted identity prevents an old retry becoming executable
            # again after many restarts of the same saved revision.
            requests=dict(state.get('restarts',{})); requests[request_id]=entry
            state['restarts']=requests; self.write(state)
            return entry
