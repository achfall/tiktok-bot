"""HTTP lokal dengan static assets, API berversi dan scheduler ringan."""
import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, parse_qs
import sys

CODE_ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(CODE_ROOT/'scripts'))
from project_paths import PROJECT_ROOT
from botapp.service import ControlService

ASSETS=CODE_ROOT/'web'
MAX_JSON=64*1024
MAX_VIDEO=2*1024*1024*1024


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'

    def log_message(self,format,*args):
        pass  # Payload, token, API key dan query tidak masuk log HTTP.

    def send_payload(self,status,payload,content_type='application/json; charset=utf-8'):
        body=json.dumps(payload,ensure_ascii=False).encode() if not isinstance(payload,bytes) else payload
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers();self.wfile.write(body)

    def allowed_host(self):
        return self.headers.get('Host') in self.server.allowed_hosts

    def require_write(self):
        host=self.headers.get('Host')
        origin=self.headers.get('Origin')
        if not self.allowed_host() or (origin and origin!=f'http://{host}'):
            raise PermissionError('Origin tidak diizinkan.')
        if not secrets.compare_digest(self.headers.get('X-Panel-Token',''),self.server.token):
            raise PermissionError('Sesi panel tidak valid. Muat ulang halaman.')
        if self.headers.get('Transfer-Encoding'):raise ValueError('Gunakan request dengan Content-Length.')

    def do_GET(self):
        if not self.allowed_host():self.send_payload(403,{'error':'Host tidak diizinkan.'});return
        parsed=urlparse(self.path)
        try:
            if parsed.path=='/api/v1/state':
                result=self.server.service.state();result['token']=self.server.token
                self.send_payload(200,result)
            elif parsed.path=='/api/v1/logs':
                rid=parse_qs(parsed.query).get('id',[''])[0]
                self.send_payload(200,{'text':self.server.service.redact(self.server.service.jobs.logs(rid))})
            elif parsed.path=='/api/v1/health':self.send_payload(200,{'status':'ok','api_version':1})
            else:
                files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/styles.css':('styles.css','text/css; charset=utf-8'),'/favicon.svg':('favicon.svg','image/svg+xml')}
                if parsed.path not in files:self.send_payload(404,{'error':'Halaman tidak ditemukan.'});return
                name,mime=files[parsed.path];self.send_payload(200,(ASSETS/name).read_bytes(),mime)
        except (ValueError,OSError) as error:self.send_payload(400,{'error':self.server.service.redact(str(error))})
        except Exception:self.send_payload(500,{'error':'Panel gagal membaca data. Periksa database dan versi aplikasi.'})

    def upload(self):
        query=parse_qs(urlparse(self.path).query)
        product_id=int(query.get('product',['0'])[0])
        filename=unquote(self.headers.get('X-File-Name',''))
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=MAX_VIDEO:raise ValueError('Ukuran video harus 1 byte–2 GB.')
        product,target=self.server.service.upload_target(product_id,filename)
        fd,name=tempfile.mkstemp(prefix='.upload-',suffix='.part',dir=target.parent)
        try:
            remaining=length
            with os.fdopen(fd,'wb') as stream:
                while remaining:
                    chunk=self.rfile.read(min(1024*1024,remaining))
                    if not chunk:raise ValueError('Unggahan terputus; file belum masuk antrean.')
                    stream.write(chunk);remaining-=len(chunk)
                stream.flush();os.fsync(stream.fileno())
            try:os.link(name,target)
            except FileExistsError:raise ValueError('Nama file sudah ada. Tidak ada file yang ditimpa.')
        finally:
            if os.path.exists(name):os.unlink(name)
        try:self.server.service.scan({'account':product['slug']})
        except ValueError:
            return {'message':'Video tersimpan. Scan antrean belum selesai; tekan Scan atau mulai proses untuk mencoba lagi.'}
        return {'message':f'{filename} tersimpan dan dipindai ke antrean.'}

    def do_POST(self):
        try:
            self.require_write()
            parsed=urlparse(self.path)
            if parsed.path=='/api/v1/upload':
                self.send_payload(200,self.upload());return
            if not parsed.path.startswith('/api/v1/'):
                self.send_payload(404,{'error':'Endpoint tidak ditemukan.'});return
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=MAX_JSON:raise ValueError('Ukuran request tidak valid.')
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Gunakan JSON untuk aksi ini.')
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise ValueError('Payload harus object JSON.')
            result=self.server.service.dispatch(parsed.path[len('/api/v1/'):],data)
            self.send_payload(200,result if isinstance(result,dict) else {'message':result})
        except PermissionError as error:
            self.close_connection=True;self.send_payload(403,{'error':str(error)})
        except (ValueError,KeyError,TypeError,OSError) as error:
            self.close_connection=True;self.send_payload(400,{'error':self.server.service.redact(str(error))})
        except Exception:
            self.close_connection=True;self.send_payload(500,{'error':'Operasi gagal. Muat ulang untuk memeriksa keadaan terakhir sebelum mencoba lagi.'})


class PanelServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,port,service):
        self.service=service;self.token=secrets.token_urlsafe(32)
        self.allowed_hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        super().__init__(('127.0.0.1',port),Handler)


def main():
    parser=argparse.ArgumentParser(description='Panel bot lokal, hanya menerima koneksi loopback.')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('Port harus 1024–65535.')
    service=ControlService(PROJECT_ROOT)
    try:server=PanelServer(args.port,service)
    except OSError as error:raise SystemExit(f'Panel belum bisa membuka port lokal: {error}')
    stopped=threading.Event()
    def scheduler():
        while not stopped.wait(5):
            try:service.schedule_tick()
            except (ValueError,OSError):pass
    thread=threading.Thread(target=scheduler,daemon=True);thread.start()
    print(f'Panel lokal: http://127.0.0.1:{args.port}',flush=True)
    print('Jadwal hanya aktif selama panel berjalan. Ctrl+C untuk menutup panel.',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:stopped.set();server.server_close()


if __name__=='__main__':main()
