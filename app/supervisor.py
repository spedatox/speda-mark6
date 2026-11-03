"""Managed backend launcher. Only an explicit restart exit respawns the child."""
import os
import signal
import subprocess
import sys


def supervise(popen=subprocess.Popen):
    stopping=False; child=None
    def stop(signum,frame):
        nonlocal stopping
        stopping=True
        if child and child.poll() is None:
            if os.name=='nt': child.send_signal(signal.CTRL_BREAK_EVENT)
            else: child.send_signal(signal.SIGTERM)
    signals=[signal.SIGINT,signal.SIGTERM]+([signal.SIGBREAK] if os.name=='nt' else [])
    originals={sig:signal.signal(sig,stop) for sig in signals}
    try:
        while not stopping:
            kwargs={'env':{**os.environ,'SPEDA_MANAGED_PARENT':'1'}}
            if os.name=='nt': kwargs['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP
            child=popen([sys.executable,'-m','app.server'],**kwargs)
            while True:
                try:
                    code=child.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    if stopping:
                        try: return child.wait(timeout=35)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            return child.wait()
            if stopping or code!=75: return code
        return 0
    finally:
        if child and child.poll() is None:
            stop(signal.SIGTERM,None)
            try: child.wait(timeout=35)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        for sig,handler in originals.items(): signal.signal(sig,handler)


if __name__=='__main__': raise SystemExit(supervise())
