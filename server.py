#!/usr/bin/env python3
"""Local server for the Shrine mirror: static files + working cart backend."""
import os, re, json, uuid, posixpath, zipfile, threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from http.cookies import SimpleCookie
from urllib.parse import unquote, urlsplit, parse_qs, urlparse
from email.parser import BytesParser
from email.policy import HTTP

import cartlib as C

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")
PORT = int(os.environ.get("PORT", "8000"))

def parse_multipart(handler):
    ctype = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length") or 0)
    body = handler.rfile.read(length) if length else b""
    if ctype.startswith("application/x-www-form-urlencoded"):
        return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}, body
    if ctype.startswith("multipart/form-data"):
        msg = BytesParser(policy=HTTP).parsebytes(
            b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
        out = {}
        if msg.is_multipart():
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                payload = part.get_payload(decode=True) or b""
                out[name] = payload.decode("utf-8", "replace")
        return out, body
    return {}, body

SHIM_TAG = '<script src="/js/preview-shim.js" defer></script>'

def inject_shim(html):
    """Ajoute le correctif d'aperçu (formulaires bloqués en iframe sandbox)."""
    if "preview-shim.js" in html:
        return html
    i = html.rfind("</body>")
    if i == -1:
        return html + SHIM_TAG
    return html[:i] + SHIM_TAG + html[i:]

# ---------- archive téléchargeable (projet complet) ----------
ZIP_PATH = os.path.join("/tmp", "ecom-pro-demo.zip")
_zip_lock = threading.Lock()
ZIP_EXCLUDE_DIRS = {".cache", ".local", "__pycache__", "qa", "tools", ".git", "node_modules"}
ZIP_EXCLUDE_FILES = {".sudo_as_admin_successful", "ecom-pro-demo.zip"}

def build_zip():
    """Construit (une fois) l'archive du projet et renvoie son chemin."""
    with _zip_lock:
        if os.path.exists(ZIP_PATH) and os.path.getsize(ZIP_PATH) > 1000:
            return ZIP_PATH
        base = os.path.dirname(os.path.abspath(__file__))
        tmp = ZIP_PATH + ".part"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for root, dirs, files in os.walk(base):
                dirs[:] = sorted(d for d in dirs if d not in ZIP_EXCLUDE_DIRS)
                for f in sorted(files):
                    if f in ZIP_EXCLUDE_FILES:
                        continue
                    p = os.path.join(root, f)
                    rel = os.path.relpath(p, base)
                    z.write(p, os.path.join("ecom-pro-demo", rel))
        os.replace(tmp, ZIP_PATH)
        return ZIP_PATH

ADDON_HANDLES = {"free-shipping", "shipping-protection"}

def render_section(sid, cart, empty_fallback=None):
    if sid == "cart-drawer":
        d = C.render_drawer(cart)
        if d is None:
            d = empty_fallback or ""
        return d
    if sid == "cart-icon-bubble":
        return ('<div id="shopify-section-cart-icon-bubble" class="shopify-section">'
                + C.render_icon_bubble(cart.count) + "</div>")
    if sid == "main-cart-items":
        import re as _re
        page = C.render_cart_page(cart)
        m = _re.search(r'(<div id="main-cart-items".*?</cart-items>)', page, _re.S)
        return m.group(1) if m else ""
    if sid == "main-cart-footer":
        import re as _re
        page = C.render_cart_page(cart)
        m = _re.search(r'(<div id="main-cart-footer".*?</div>\s*</div>\s*</div>)', page, _re.S)
        return m.group(1) if m else ""
    if sid == "cart-live-region-text":
        return ('<p class="visually-hidden" id="cart-live-region-text" aria-live="polite" role="status">'
                f"Cart updated. Subtotal {C.money(cart.total)} EUR.</p>")
    return ""

class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    # ---------- utils ----------
    # Les iframes d'aperçu (sandbox="allow-scripts") bloquent les cookies et
    # localStorage : on utilise donc un panier partagé comme solution de repli.
    DEMO_SID = "d000000000000000"

    def cart(self):
        sid = None
        raw = self.headers.get("Cookie")
        if raw:
            try:
                c = SimpleCookie(); c.load(raw)
                if "cart_sid" in c:
                    sid = c["cart_sid"].value
            except Exception:
                pass
        new = False
        if not sid or not re.match(r"^[a-f0-9]{16,32}$", sid):
            sid = self.DEMO_SID
            new = False          # inutile de poser un cookie : le panier est partagé
        cart = C.Cart(sid)
        self._sid_new = new
        return cart

    def send_text(self, body, ctype="text/html; charset=utf-8", code=200, extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if getattr(self, "_sid_new", False):
            self.send_header("Set-Cookie", f"cart_sid={self._cart_sid}; Path=/; Max-Age=2592000; SameSite=Lax")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        if getattr(self, "_sid_new", False):
            self.send_header("Set-Cookie", f"cart_sid={self._cart_sid}; Path=/; Max-Age=2592000; SameSite=Lax")
        self.end_headers()

    def resolve_file(self, raw_path):
        p = unquote(urlsplit(raw_path).path)
        parts = [x for x in p.split("/") if x not in ("", ".", "..")]
        fs = os.path.join(ROOT, *parts)
        if os.path.isdir(fs):
            idx = os.path.join(fs, "index.html")
            if os.path.isfile(idx):
                return idx, p
        if os.path.isfile(fs):
            return fs, p
        if "." not in posixpath.basename(p):
            cand = os.path.join(fs, "index.html")
            if os.path.isfile(cand):
                return cand, p
        return None, p

    # ---------- GET ----------
    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        cart = self.cart()
        self._cart_sid = cart.sid

        # --- téléchargement du projet complet ---
        if path.rstrip("/") in ("/download", "/telecharger", "/download/ecom-pro-demo.zip"):
            zp = build_zip()
            size = os.path.getsize(zp)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="ecom-pro-demo.zip"')
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(zp, "rb") as fh:
                while True:
                    chunk = fh.read(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return

        # --- endpoint neutre (remplace l'appel de licence/analytics du thème) ---
        if path == "/__noop.json":
            return self.send_text("{}", "application/json")

        # --- JSON API ---
        if path == "/cart.js":
            return self.send_text(json.dumps(cart.shopify_json()), "application/json")

        # --- section rendering API (GET /cart?sections=a,b) ---
        if "sections" in qs:
            names = [x for x in qs["sections"][0].split(",") if x]
            out = {n: render_section(n, cart, self._empty_drawer_section()) for n in names}
            return self.send_text(json.dumps(out), "application/json")

        # --- sections ---
        if "section_id" in qs:
            sid = qs["section_id"][0]
            return self.send_text(render_section(sid, cart, self._empty_drawer_section()))

        # --- checkout -> friendly message (no real payment backend) ---
        if path.rstrip("/") in ("/checkout", "/cart/checkout"):
            return self.redirect("/cart")

        # --- customer account (Shopify-hosted upstream -> local themed page) ---
        if path.startswith("/customer_authentication") or path.rstrip("/") in ("/account", "/account/login", "/account/register", "/account/recover"):
            fp = os.path.join(ROOT, "account", "index.html")
            if os.path.isfile(fp):
                return self.send_text(inject_shim(C.hydrate(open(fp, encoding="utf-8", errors="replace").read(), cart)))

        # --- search results (local demo: serve the closest real snapshot) ---
        if path.rstrip("/") == "/search" and qs.get("q"):
            q = (qs["q"][0] or "").strip().lower()
            known = {"noir": "noir", "noir dusk": "noir", "perfume": "perfume", "perfumes": "perfume",
                     "crimson": "perfume", "rose": "perfume", "citron": "perfume", "venom": "perfume",
                     "luna": "perfume", "smoke": "perfume", "oil": "perfume", "scent": "perfume",
                     "scents": "perfume", "pheromone": "perfume"}
            key = known.get(q)
            if not key:
                for k, v in known.items():
                    if k in q:
                        key = v; break
            if key:
                fp = os.path.join(ROOT, "_tpl", f"search_{key}.html")
                if os.path.isfile(fp):
                    html = open(fp, encoding="utf-8", errors="replace").read()
                    return self.send_text(inject_shim(C.hydrate(html, cart)))

        # --- cart page ---
        if path.rstrip("/") in ("/cart", "/cart/index.html"):
            html = C.render_cart_page(cart)
            return self.send_text(inject_shim(C.hydrate(html, cart)))

        fs, p = self.resolve_file(self.path)
        if not fs:
            nf = os.path.join(ROOT, "404.html")
            if os.path.isfile(nf):
                return self.send_text(inject_shim(C.hydrate(open(nf, encoding="utf-8", errors="replace").read(), cart)), code=404)
            return self.send_error(404)

        if fs.endswith(".html"):
            html = open(fs, encoding="utf-8", errors="replace").read()
            return self.send_text(inject_shim(C.hydrate(html, cart)))
        return self.serve_file(fs)

    def _empty_drawer_section(self):
        fs = os.path.join(ROOT, "index.html")
        html = open(fs, encoding="utf-8", errors="replace").read()
        i = html.find("<cart-drawer")
        j = html.find("</cart-drawer>", i)
        return html[i:j + len("</cart-drawer>")] if i >= 0 and j > 0 else ""

    def serve_file(self, fs):
        import mimetypes
        ctype, _ = mimetypes.guess_type(fs)
        if fs.endswith(".m3u8"):
            ctype = "application/vnd.apple.mpegurl"
        ctype = ctype or "application/octet-stream"
        try:
            data = open(fs, "rb").read()
        except Exception:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    # ---------- OPTIONS (CORS pour les contextes sandboxés) ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- POST ----------
    def do_POST(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        cart = self.cart()
        self._cart_sid = cart.sid
        form, body = parse_multipart(self)

        is_json = "application/json" in (self.headers.get("Content-Type") or "")
        payload = {}
        if is_json and body:
            try:
                payload = json.loads(body.decode("utf-8", "replace"))
            except Exception:
                payload = {}

        # --- add ---
        if path in ("/cart/add", "/cart/add.js"):
            added = 0
            if payload:
                items = payload.get("items") or [payload]
                for it in items:
                    vid = it.get("id") or it.get("variant_id")
                    q = int(it.get("quantity", 1))
                    if vid and cart.add(vid, q):
                        added += 1
            else:
                main_id = form.get("id")
                if main_id:
                    q = int(form.get("quantity") or 1)
                    if cart.add(main_id, q):
                        added += 1
                else:
                    ids = {}
                    for k, v in form.items():
                        m = re.match(r"items\[(\d+)\]\[(id|quantity)\]", k)
                        if m:
                            ids.setdefault(m.group(1), {})[m.group(2)] = v
                    for _, d in sorted(ids.items()):
                        vid = d.get("id")
                        p = C.BY_VARIANT.get(str(vid))
                        if p and p["handle"] in ADDON_HANDLES:
                            continue
                        if vid and cart.add(vid, int(d.get("quantity", 1) or 1)):
                            added += 1
            wants_sections = bool(form.get("sections") or qs.get("sections"))
            if path.endswith(".js") or wants_sections:
                names = [x for x in (form.get("sections") or (qs.get("sections", [""])[0])).split(",") if x]
                out = {"items": cart.shopify_json()["items"], "item_count": cart.count,
                       "total_price": cart.total, "sections": {}}
                for n in names:
                    out["sections"][n] = render_section(n, cart, self._empty_drawer_section())
                return self.send_text(json.dumps(out), "application/json")
            return self.redirect("/cart")

        # --- change ---
        if path in ("/cart/change", "/cart/change.js"):
            if payload:
                key = payload.get("id") or payload.get("line") or payload.get("key")
                qty = payload.get("quantity", 0)
            else:
                key = form.get("id") or form.get("line") or (qs.get("id", [None])[0])
                qty = form.get("quantity") or (qs.get("quantity", [0])[0])
            if key:
                cart.set_qty(key, int(qty or 0))
            wants_sections = bool(payload.get("sections") if payload else False)
            if path.endswith(".js") or wants_sections:
                names = payload.get("sections") if payload else []
                if isinstance(names, str):
                    names = [x for x in names.split(",") if x]
                out = {"items": cart.shopify_json()["items"], "item_count": cart.count,
                       "total_price": cart.total, "sections": {}}
                for n in (names or []):
                    out["sections"][n] = render_section(n, cart, self._empty_drawer_section())
                return self.send_text(json.dumps(out), "application/json")
            return self.redirect("/cart")

        # --- update (cart page form: updates[]) ---
        if path in ("/cart/update", "/cart/update.js"):
            if payload:
                for u in payload.get("updates", []) or []:
                    pass
            else:
                vals = []
                for k, v in form.items():
                    m = re.match(r"updates\[\]", k)
                    if m:
                        vals.append(v)
                for it, v in zip(cart.data["items"], vals):
                    cart.set_qty(it["variant_id"], int(v or 0))
            if path.endswith(".js"):
                return self.send_text(json.dumps(cart.shopify_json()), "application/json")
            if form.get("checkout") or qs.get("checkout"):
                return self.redirect("/cart")
            return self.redirect("/cart")

        # --- clear ---
        if path in ("/cart/clear", "/cart/clear.js"):
            cart.clear()
            if path.endswith(".js"):
                return self.send_text(json.dumps(cart.shopify_json()), "application/json")
            return self.redirect("/cart")

        # --- discount code (demo) ---
        if path in ("/discount", "/cart/discount"):
            return self.redirect("/cart?discount_demo=1")

        # --- search ---
        if path in ("/search", "/search/"):
            q = (form.get("q") or "").strip()
            return self.redirect("/search" + (("?q=" + q.replace(" ", "+")) if q else ""))

        # --- newsletter / contact (demo ack) ---
        if path.endswith("/contact") or path == "/contact":
            return self.redirect("/pages/contact?sent=1")
        if "customer" in path or "newsletter" in path:
            return self.redirect("/?subscribed=1")

        # everything else: pretend OK
        return self.redirect("/")

    # ---------- headers ----------
    def send_response(self, code, message=None):
        super().send_response(code, message)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    print(f"Serving {ROOT} on http://0.0.0.0:{PORT} (cart backend enabled)")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
