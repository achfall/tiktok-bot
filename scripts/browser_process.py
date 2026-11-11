"""Identitas proses browser milik bot; tidak memakai killall atau Quit per app ID."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request
from urllib.parse import urlparse
import websocket


def process_token(pid):
    """Token PID + waktu mulai + executable mencegah salah sasaran setelah PID reuse."""
    try:
        result=subprocess.run(['ps','-p',str(pid),'-o','lstart=,comm='],capture_output=True,text=True,timeout=3)
    except (OSError,subprocess.TimeoutExpired):
        return None
    text=result.stdout.strip()
    if result.returncode and not text:
        return '' if result.returncode==1 else None
    return text or None


def capture_process(port):
    """Dipanggil hanya setelah launcher bot membuka instance baru."""
    ws=None
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version',timeout=3) as response:
            endpoint=json.load(response)['webSocketDebuggerUrl']
        parsed=urlparse(endpoint)
        if parsed.scheme!='ws' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.port!=port:
            return None
        ws=websocket.create_connection(endpoint,timeout=3,origin=f'http://127.0.0.1:{port}')
        ws.send(json.dumps({'id':1,'method':'SystemInfo.getProcessInfo'}))
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            message=json.loads(ws.recv())
            if message.get('id')!=1:
                continue
            browsers=[p for p in message.get('result',{}).get('processInfo',[]) if p.get('type')=='browser']
            if len(browsers)!=1:
                return None
            pid=int(browsers[0]['id'])
            if pid<=1:
                return None
            token=process_token(pid)
            parts=token.split(None,5) if token else []
            if len(parts)!=6 or Path(parts[5]).name.lower() not in ('google chrome','google-chrome','google-chrome-stable','chrome','chromium','chromium-browser'):
                return None
            return {'pid':pid,'token':token,'endpoint':endpoint}
    except (OSError,ValueError,KeyError,websocket.WebSocketException):
        return None
    finally:
        if ws is not None:
            try:ws.close()
            except Exception:pass


def wait_for_quit(port, identity, ready):
    """Browser.close dahulu; SIGTERM hanya ke PID Chrome milik bot yang masih sama."""
    if isinstance(identity.get('pid'),bool) or not isinstance(identity.get('pid'),int) or identity['pid']<=1 or not identity.get('token'):
        return False
    deadline=time.monotonic()+12
    terminated=False
    for attempt in range(48):
        token=process_token(identity['pid'])
        if token is not None and token!=identity['token'] and not ready(port):
            return True
        if time.monotonic()>=deadline:
            break
        if attempt>=20 and not terminated and token==identity['token']:
            # Verifikasi tepat sebelum signal. Tidak ada SIGKILL / killall.
            if process_token(identity['pid'])!=identity['token']:
                continue
            try:os.kill(identity['pid'],signal.SIGTERM)
            except ProcessLookupError:pass
            except PermissionError:return False
            terminated=True
        time.sleep(.25)
    return False
