# -*- coding: utf-8 -*-
"""Servidor de Facturas San Martin: una base de datos compartida para varias personas.
Solo usa la libreria estandar de Python. Datos en la carpeta 'datos'."""
import http.server, socketserver, sqlite3, json, os, sys, socket, shutil, time, base64, mimetypes, urllib.parse, glob

ROOT = os.path.dirname(os.path.abspath(__file__))
DATOS = os.environ.get("DATOS") or os.path.join(ROOT, "datos"); DB = os.path.join(DATOS, "facturas.db")
PUERTO = int(os.environ.get("PORT") or os.environ.get("PUERTO") or "8765")
NUBE = os.environ.get("NUBE") == "1"   # en la nube la clave es obligatoria
os.makedirs(DATOS, exist_ok=True)
PRIVADOS = {"servidor.py", "clave.txt", "datos"}
mimetypes.add_type("application/wasm", ".wasm")

def clave():
    if os.environ.get("CLAVE"): return os.environ["CLAVE"].strip()
    try: return open(os.path.join(ROOT, "clave.txt"), encoding="utf-8").read().strip()
    except OSError: return ""

ESQUEMA = """CREATE TABLE IF NOT EXISTS facturas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, consecutivo TEXT UNIQUE NOT NULL, sucursal TEXT NOT NULL,
  documento TEXT, cliente TEXT, fecha TEXT, tipo TEXT, subtotal REAL DEFAULT 0, descuento REAL DEFAULT 0,
  pct REAL DEFAULT 0, total REAL DEFAULT 0, obs TEXT, img TEXT, lineas TEXT, ingresada TEXT, extra TEXT,
  created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_sucursal ON facturas(sucursal);"""

def conectar(ruta=DB):
    c = sqlite3.connect(ruta, timeout=30); c.row_factory = sqlite3.Row
    c.executescript(ESQUEMA)
    cols = [r[1] for r in c.execute("PRAGMA table_info(facturas)")]
    for nombre, tipo in (("extra", "TEXT"), ("updated_at", "TEXT"), ("created_at", "TEXT")):
        if nombre not in cols: c.execute("ALTER TABLE facturas ADD COLUMN %s %s" % (nombre, tipo))
    return c

UPSERT = """INSERT INTO facturas (consecutivo,sucursal,documento,cliente,fecha,tipo,subtotal,descuento,pct,total,obs,img,lineas,ingresada,extra,updated_at)
VALUES (:consecutivo,:sucursal,:documento,:cliente,:fecha,:tipo,:subtotal,:descuento,:pct,:total,:obs,:img,:lineas,:ingresada,:extra,datetime('now'))
ON CONFLICT(consecutivo) DO UPDATE SET sucursal=excluded.sucursal, documento=excluded.documento, cliente=excluded.cliente,
 fecha=excluded.fecha, tipo=excluded.tipo, subtotal=excluded.subtotal, descuento=excluded.descuento, pct=excluded.pct,
 total=excluded.total, obs=excluded.obs, img=COALESCE(excluded.img, facturas.img), lineas=COALESCE(excluded.lineas, facturas.lineas),
 ingresada=COALESCE(excluded.ingresada, facturas.ingresada), extra=COALESCE(excluded.extra, facturas.extra), updated_at=datetime('now')"""
CAMPOS = ["consecutivo","sucursal","documento","cliente","fecha","tipo","subtotal","descuento","pct","total","obs","img","lineas","ingresada","extra"]

def respaldo_diario():
    carpeta = os.path.join(DATOS, "respaldos"); os.makedirs(carpeta, exist_ok=True)
    destino = os.path.join(carpeta, "facturas-%s.db" % time.strftime("%Y-%m-%d"))
    if os.path.exists(DB) and not os.path.exists(destino):
        o = sqlite3.connect(DB); d = sqlite3.connect(destino); o.backup(d); d.close(); o.close()
        for viejo in sorted(glob.glob(os.path.join(carpeta, "*.db")))[:-14]: os.remove(viejo)

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def enviar(self, codigo, cuerpo=b"", tipo="application/json", extra=None):
        if not isinstance(cuerpo, bytes): cuerpo = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo); self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo))); self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(cuerpo)
    def autorizado(self):
        k = clave()
        if not k: return not NUBE
        a = self.headers.get("Authorization", "")
        if a.startswith("Basic "):
            try: return base64.b64decode(a[6:]).decode("utf-8").split(":", 1)[1] == k
            except Exception: return False
        time.sleep(0.5)   # frena adivinar claves
        return False
    def cuerpo(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))
    def despachar(self):
        if not self.autorizado():
            return self.enviar(401, {"error": "clave"}, extra={"WWW-Authenticate": 'Basic realm="Facturas San Martin"'})
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query); ruta = u.path
        try:
            if ruta.startswith("/api/"): return self.api(ruta, q)
            return self.estatico(ruta)
        except Exception as e:
            return self.enviar(500, {"error": str(e)})
    do_GET = do_POST = do_DELETE = despachar
    def api(self, ruta, q):
        m = self.command
        if ruta == "/api/ping": return self.enviar(200, {"ok": True})
        if ruta == "/api/facturas" and m == "GET":
            c = conectar(); filas = [dict(r) for r in c.execute(
                "SELECT * FROM facturas WHERE sucursal=? ORDER BY fecha DESC, consecutivo DESC", (q.get("sucursal", [""])[0],))]
            c.close(); return self.enviar(200, filas)
        if ruta == "/api/factura" and m == "POST":
            d = json.loads(self.cuerpo().decode("utf-8")); p = {k: d.get(k) for k in CAMPOS}
            if not p["consecutivo"] or not p["sucursal"]: return self.enviar(400, {"error": "faltan datos"})
            c = conectar(); c.execute(UPSERT, p); c.commit(); c.close(); return self.enviar(200, {"ok": True})
        if ruta == "/api/factura" and m == "DELETE":
            c = conectar(); c.execute("DELETE FROM facturas WHERE consecutivo=?", (q.get("cons", [""])[0],)); c.commit(); c.close()
            return self.enviar(200, {"ok": True})
        if ruta == "/api/conteos":
            c = conectar(); r = {x[0]: x[1] for x in c.execute("SELECT sucursal, COUNT(*) FROM facturas GROUP BY sucursal")}
            c.close(); return self.enviar(200, r)
        if ruta == "/api/export":
            tmp = os.path.join(DATOS, "_export.db")
            if os.path.exists(tmp): os.remove(tmp)
            o = conectar(); d = sqlite3.connect(tmp); o.backup(d); d.close(); o.close()
            b = open(tmp, "rb").read(); os.remove(tmp)
            return self.enviar(200, b, "application/octet-stream",
                {"Content-Disposition": 'attachment; filename="facturas-san-martin-%s.db"' % time.strftime("%Y-%m-%d")})
        if ruta == "/api/import" and m == "POST":
            tmp = os.path.join(DATOS, "_import.db"); open(tmp, "wb").write(self.cuerpo())
            try:
                t = conectar(tmp); n = t.execute("SELECT COUNT(*) FROM facturas").fetchone()[0]; t.close()
            except Exception:
                os.remove(tmp); return self.enviar(400, {"error": "El archivo no es una base de datos de facturas."})
            if os.path.exists(DB): shutil.copy(DB, os.path.join(DATOS, "antes_de_importar-%s.db" % time.strftime("%Y%m%d-%H%M%S")))
            os.replace(tmp, DB); return self.enviar(200, {"ok": True, "facturas": n})
        return self.enviar(404, {"error": "no existe"})
    def estatico(self, ruta):
        p = urllib.parse.unquote(ruta).lstrip("/") or "index.html"
        f = os.path.normpath(os.path.join(ROOT, p))
        if not f.startswith(ROOT) or p.split("/")[0] in PRIVADOS or not os.path.isfile(f):
            return self.enviar(404, b"no existe", "text/plain")
        tipo = mimetypes.guess_type(f)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo.endswith("javascript"): tipo += "; charset=utf-8"
        return self.enviar(200, open(f, "rb").read(), tipo)

class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True; allow_reuse_address = True

def ips():
    r = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); r.add(s.getsockname()[0]); s.close()
    except Exception: pass
    try: r.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception: pass
    return sorted(i for i in r if not i.startswith("127."))

if __name__ == "__main__":
    if NUBE and not clave():
        print("Falta la variable CLAVE: en la nube la clave es obligatoria."); sys.exit(1)
    conectar().close(); respaldo_diario()
    try: srv = S(("0.0.0.0", PUERTO), H)
    except OSError:
        print("\n  El puerto %d ya esta en uso. Cierre el otro servidor e intente de nuevo." % PUERTO); sys.exit(1)
    print("\n  ==== FACTURAS SAN MARTIN - SERVIDOR ====\n")
    print("  En ESTA computadora:   http://127.0.0.1:%d" % PUERTO)
    for i in ips(): print("  Para las demas PC:     http://%s:%d" % (i, PUERTO))
    print("  Clave: " + ("activada (clave.txt)" if clave() else "sin clave (cree clave.txt para pedirla)"))
    print("\n  No cierre esta ventana mientras trabajen. Datos en: " + DATOS + "\n")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
