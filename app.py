# app.py
import secrets, hashlib, hmac, json, time, threading
import urllib.request, urllib.parse, urllib.error
from functools import wraps
from datetime import datetime
import requests
from flask import (Flask, request, session, redirect, url_for,
                   render_template, flash, g, abort, jsonify)
from config import Config
from database import init_db, get_conn

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=Config.SESSION_LIFETIME,
)

_login_attempts = {}

# ============ DB access per request ============
def db():
    d = getattr(g, "_db", None)
    if d is None:
        d = g._db = get_conn()
    return d

@app.teardown_appcontext
def close_db(e):
    d = getattr(g, "_db", None)
    if d is not None: d.close()

# ============ Helpers ============
def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()
def gen_ref(): return "RBH" + secrets.token_hex(3).upper()

def get_settings():
    return db().execute("SELECT * FROM settings LIMIT 1").fetchone()

def get_server_list():
    s = get_settings()
    try: raw = (s["api_servers"] or "").strip()
    except: raw = ""
    if not raw: raw = Config.DEFAULT_SERVERS
    return [x.strip().upper() for x in raw.split(",") if x.strip()]

def get_active_packages():
    return db().execute("SELECT * FROM packages WHERE is_active=1 ORDER BY sort_order ASC, price ASC").fetchall()

def get_all_packages():
    return db().execute("SELECT * FROM packages ORDER BY sort_order ASC, id ASC").fetchall()

def get_package(pid):
    return db().execute("SELECT * FROM packages WHERE id=? AND is_active=1", (pid,)).fetchone()

def get_instant_config():
    s = get_settings()
    try:
        cost = float(s["instant_like_cost"] or Config.DEFAULT_INSTANT_LIKE_COST)
        count = int(s["instant_like_count"] or Config.DEFAULT_INSTANT_LIKE_COUNT)
    except:
        cost, count = Config.DEFAULT_INSTANT_LIKE_COST, Config.DEFAULT_INSTANT_LIKE_COUNT
    return cost, count

def get_user_by_username(u): return db().execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
def get_user_by_id(uid): return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
def get_user_by_ref(code): return db().execute("SELECT * FROM users WHERE referral_code=?", (code,)).fetchone()

def create_user(u, p, ref_by=None):
    d = db()
    cur = d.execute("INSERT INTO users (username,password,referral_code,referred_by) VALUES (?,?,?,?)",
                    (u, hash_pw(p), gen_ref(), ref_by))
    d.commit(); return cur.lastrowid

def get_orders_by_user(uid):
    return db().execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()

def get_all_users(): return db().execute("SELECT * FROM users ORDER BY id").fetchall()
def get_all_orders():
    return db().execute("""SELECT orders.*, users.username FROM orders
        JOIN users ON orders.user_id=users.id ORDER BY orders.created_at DESC""").fetchall()
def get_all_deposits():
    return db().execute("""SELECT deposits.*, users.username FROM deposits
        JOIN users ON deposits.user_id=users.id ORDER BY deposits.created_at DESC""").fetchall()

def place_order(user_id, uid, plan, price, otype="plan", srv=""):
    d = db()
    cur = d.execute("INSERT INTO orders (user_id,uid,plan,price,order_type,server_name) VALUES (?,?,?,?,?,?)",
                    (user_id, uid, plan, price, otype, srv))
    d.commit(); return cur.lastrowid

def save_api_result(oid, st, body, url="", la=0, lb=0, lc=0, player="", sc=""):
    d = db()
    d.execute("""UPDATE orders SET api_status=?,api_response=?,request_url=?,
        likes_added=?,likes_before=?,likes_after=?,player_name=?,status_code=? WHERE id=?""",
        (st, body[:4000], url, la, lb, lc, player, str(sc), oid))
    d.commit()

def update_order_status(oid, st):
    d = db(); d.execute("UPDATE orders SET status=? WHERE id=?", (st, oid)); d.commit()

def add_deposit(user_id, amount, method="FamPay", ref=""):
    d = db()
    d.execute("INSERT INTO deposits (user_id,amount,method,reference) VALUES (?,?,?,?)",
              (user_id, amount, method, ref))
    d.commit()

def approve_deposit(did):
    d = db()
    dep = d.execute("SELECT * FROM deposits WHERE id=?", (did,)).fetchone()
    if dep and dep["status"] == "pending":
        d.execute("UPDATE deposits SET status='approved' WHERE id=?", (did,))
        d.execute("UPDATE users SET balance=balance+? WHERE id=?", (dep["amount"], dep["user_id"]))
        d.commit(); return True
    return False

def reject_deposit(did):
    d = db(); d.execute("UPDATE deposits SET status='rejected' WHERE id=?", (did,)); d.commit()

def update_password(uid, new):
    d = db(); d.execute("UPDATE users SET password=? WHERE id=?", (hash_pw(new), uid)); d.commit()

def transfer_funds(fid, to_u, amt):
    d = db()
    fu = get_user_by_id(fid); tu = get_user_by_username(to_u)
    if not tu: return False, "Recipient not found"
    if tu["id"] == fid: return False, "Cannot transfer to yourself"
    if amt < Config.MIN_TRANSFER: return False, f"Minimum transfer is {Config.CURRENCY_SYMBOL}{Config.MIN_TRANSFER:.2f}"
    if fu["balance"] < amt: return False, "Insufficient balance"
    d.execute("UPDATE users SET balance=balance-? WHERE id=?", (amt, fid))
    d.execute("UPDATE users SET balance=balance+? WHERE id=?", (amt, tu["id"]))
    d.execute("INSERT INTO transfers (from_user,to_user,amount) VALUES (?,?,?)", (fid, tu["id"], amt))
    d.commit(); return True, f"Sent {Config.CURRENCY_SYMBOL}{amt:.2f} to {to_u}"

def admin_add_coins(tid, amt):
    d = db(); d.execute("UPDATE users SET balance=balance+? WHERE id=?", (amt, tid)); d.commit()
def admin_remove_coins(tid, amt):
    d = db(); d.execute("UPDATE users SET balance=balance-? WHERE id=?", (amt, tid)); d.commit()

def admin_toggle_ban(uid):
    d = db(); u = get_user_by_id(uid)
    if not u: return None
    n = 0 if u["is_banned"] else 1
    d.execute("UPDATE users SET is_banned=? WHERE id=?", (n, uid)); d.commit()
    return n

def get_referrals(uid):
    return db().execute("SELECT id,username,created_at FROM users WHERE referred_by=?", (uid,)).fetchall()

def get_user_transfers(uid):
    return db().execute("""SELECT t.*,uf.username as from_name,ut.username as to_name
        FROM transfers t JOIN users uf ON t.from_user=uf.id JOIN users ut ON t.to_user=ut.id
        WHERE t.from_user=? OR t.to_user=? ORDER BY t.created_at DESC""", (uid, uid)).fetchall()

def update_settings(d):
    db_ = db()
    db_.execute("""UPDATE settings SET
        api_url=?,api_key=?,api_method=?,api_param_name=?,api_key_param=?,
        api_extra_params=?,api_server_param=?,api_servers=?,api_success_mode=?,
        api_success_keyword=?,api_status_field=?,api_status_value=?,api_max_field=?,api_max_value=?,
        api_likes_field=?,api_total_field=?,api_player_field=?,api_before_field=?,api_region_field=?,
        instant_like_cost=?,instant_like_count=? WHERE id=1""",
        (d.get("api_url",""), d.get("api_key",""), d.get("api_method","GET"),
         d.get("api_param_name","uid"), d.get("api_key_param","key"), d.get("api_extra_params",""),
         d.get("api_server_param","server_name"), d.get("api_servers",""), d.get("api_success_mode","json"),
         d.get("api_success_keyword","success"), d.get("api_status_field","status"),
         d.get("api_status_value","1"), d.get("api_max_field","status"), d.get("api_max_value","2"),
         d.get("api_likes_field","LikesGivenByAPI"), d.get("api_total_field","LikesafterCommand"),
         d.get("api_player_field","PlayerNickname"), d.get("api_before_field","LikesbeforeCommand"),
         d.get("api_region_field","Region"),
         float(d.get("instant_like_cost", 30)), int(d.get("instant_like_count", 1000))))
    db_.commit()

# ============ Decorators ============
def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "user_id" not in session: return redirect(url_for("login_page"))
        u = get_user_by_id(session["user_id"])
        if not u: session.clear(); return redirect(url_for("login_page"))
        if u["is_banned"]: session.clear(); flash("Account suspended.", "error"); return redirect(url_for("login_page"))
        return f(*a, **k)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("admin_logged"): return redirect(url_for("admin_panel"))
        return f(*a, **k)
    return w

# ============ CSRF ============
def gen_csrf():
    if "csrf" not in session: session["csrf"] = secrets.token_hex(32)
    return session["csrf"]

def check_csrf(tok):
    return tok and session.get("csrf") and hmac.compare_digest(tok, session["csrf"])

@app.before_request
def _guard():
    if request.path.startswith("/admin") and not session.get("admin_logged"):
        if request.path not in ("/admin", "/admin/login"): return redirect(url_for("admin_panel"))
    if request.method == "POST" and request.path not in ("/login", "/register", "/admin/login"):
        tok = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not tok:
            try:
                j = request.get_json(silent=True)
                if j: tok = j.get("csrf_token")
            except: pass
        if not check_csrf(tok): abort(403, description="Invalid CSRF token.")

# ============ Like API ============
def call_like_api(uid, plan_likes, server_name=""):
    s = get_settings()
    def _r(k, d):
        try: v = s[k]
        except: return d
        if v is None: return d
        v = str(v).strip()
        return v if v else d

    url_base = _r("api_url", "")
    if not url_base: return _err("API URL not configured")
    method = _r("api_method", "GET").upper()
    if method not in ("GET","POST"): method = "GET"
    pn = _r("api_param_name","uid"); kp = _r("api_key_param","key"); sp = _r("api_server_param","server_name")
    try: ak = str(s["api_key"] or "").strip()
    except: ak = ""

    params = {pn: str(uid)}
    if ak: params[kp] = ak
    if server_name: params[sp] = str(server_name).strip().upper()
    extra = _r("api_extra_params","")
    if extra:
        for pr in extra.split("&"):
            pr = pr.strip()
            if pr and "=" in pr:
                k, v = pr.split("=",1); params[k.strip()] = v.strip()

    qs = urllib.parse.urlencode(params)
    sep = "&" if "?" in url_base else "?"
    full_url = f"{url_base}{sep}{qs}"

    try:
        if method == "POST":
            data = urllib.parse.urlencode(params).encode()
            req = urllib.request.Request(url_base, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        else:
            req = urllib.request.Request(full_url, method="GET")
        req.add_header("User-Agent", "Mozilla/5.0 FFBoostHub/1.0")
        req.add_header("Accept", "application/json, text/plain, */*")
        with urllib.request.urlopen(req, timeout=Config.API_TIMEOUT) as r:
            code = r.getcode(); body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try: body = e.read().decode("utf-8", errors="replace")
        except: body = ""
        return _err(f"HTTP {e.code}", body=body, url=full_url, code=e.code)
    except Exception as e:
        return _err(str(e), url=full_url)

    mode = _r("api_success_mode","json").lower()
    ok=False; la=0; lb=0; lc=0; pl=""; rg=""; sc=""; err=""
    if mode == "json":
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                sf = _r("api_status_field","status"); sv = _r("api_status_value","1")
                mf = _r("api_max_field", sf); mv = _r("api_max_value","")
                cs = str(data.get(sf,"")); cm = str(data.get(mf,"")); sc = cs
                if cs == str(sv): ok = True
                elif mv and cm == str(mv): err = "MAX_LIMIT_REACHED"
                else: err = f"Status: {cs}"
                try: la = int(data.get(_r("api_likes_field","LikesGivenByAPI"),0) or 0)
                except: la = 0
                try: lc = int(data.get(_r("api_total_field","LikesafterCommand"),0) or 0)
                except: lc = 0
                try: lb = int(data.get(_r("api_before_field","LikesbeforeCommand"),0) or 0)
                except: lb = 0
                pl = str(data.get(_r("api_player_field","PlayerNickname"),"") or "")
                rg = str(data.get(_r("api_region_field","Region"),"") or "")
            else: err = "Invalid JSON"
        except:
            kw = _r("api_success_keyword","success").lower()
            ok = kw in body.lower()
            if not ok: err = "Invalid response"
    else:
        kw = _r("api_success_keyword","success").lower()
        ok = kw in body.lower()
        if not ok: err = "Keyword not found"

    return {"ok":ok,"http_code":code,"body":body,
            "error":"" if ok else (err or "Failed"),"url":full_url,
            "likes_added":la,"likes_before":lb,"likes_after":lc,
            "player":pl,"region":rg,"status_code":sc}

def _err(msg, body="", url="", code=0):
    return {"ok":False,"http_code":code,"body":body,"error":msg,"url":url,
            "likes_added":0,"likes_before":0,"likes_after":0,
            "player":"","region":"","status_code":""}

def call_ban_check_api(uid, region):
    try:
        params = {"uid": str(uid), "reg": str(region).strip().upper()}
        qs = urllib.parse.urlencode(params)
        full_url = f"{Config.BAN_API_URL}?{qs}"
        req = urllib.request.Request(full_url, method="GET")
        req.add_header("User-Agent", "Mozilla/5.0 FFBoostHub/1.0")
        req.add_header("Accept", "application/json, text/plain, */*")
        with urllib.request.urlopen(req, timeout=Config.API_TIMEOUT) as r:
            body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try: body = e.read().decode("utf-8", errors="replace")
        except: body = ""
        return {"ok":False,"error":f"HTTP {e.code}","raw":body}
    except Exception as e:
        return {"ok":False,"error":str(e),"raw":""}

    data = None
    try: data = json.loads(body)
    except: pass
    if not isinstance(data, dict):
        return {"ok":False,"error":"Invalid response from API","raw":body}

    return {
        "ok": True, "error": "",
        "uid": data.get("Uid") or data.get("UID") or uid,
        "nickname": data.get("Nickname") or data.get("PlayerNickname") or "",
        "region": data.get("Region") or region,
        "level": data.get("Level") or "",
        "status": data.get("Status") or "",
        "since": data.get("Since") or "",
        "unban_time": data.get("Unban Time") or "",
        "raw": body,
    }

# ============ FamGateway ============
def create_fam_order(amount, user_id):
    headers = {"X-Api-Key": Config.FAMGATEWAY_API_KEY, "Content-Type": "application/json"}
    payload = {"amount": float(amount), "customer_name": str(user_id)}
    try:
        r = requests.post(Config.FAMGATEWAY_CREATE_URL, json=payload, headers=headers, timeout=20)
        return r.json()
    except Exception as e:
        return {"status":"error","message":str(e)}

def verify_fam_order(order_id):
    url = f"{Config.FAMGATEWAY_VERIFY_URL}?api_key={Config.FAMGATEWAY_API_KEY}&order_id={order_id}"
    try:
        r = requests.get(url, timeout=15); return r.json()
    except Exception as e:
        return {"status":"error","message":str(e)}

def save_fam_order(order_id, user_id, amount, qr_url):
    d = get_conn()
    d.execute("""INSERT OR IGNORE INTO fam_orders
        (order_id,user_id,amount,qr_url,status) VALUES (?,?,?,?,'pending')""",
        (order_id, user_id, amount, qr_url))
    d.commit(); d.close()

def get_fam_order(order_id):
    d = get_conn()
    row = d.execute("SELECT * FROM fam_orders WHERE order_id=?", (order_id,)).fetchone()
    d.close(); return row

def credit_fam_order(order_id):
    d = get_conn()
    row = d.execute("SELECT * FROM fam_orders WHERE order_id=?", (order_id,)).fetchone()
    if not row: d.close(); return None
    if row["credited"] == 1: d.close(); return row
    d.execute("UPDATE users SET balance=balance+? WHERE id=?", (row["amount"], row["user_id"]))
    d.execute("UPDATE fam_orders SET status='success', credited=1 WHERE order_id=?", (order_id,))
    d.execute("INSERT INTO deposits (user_id,amount,method,reference,status) VALUES (?,?,?,?,?)",
              (row["user_id"], row["amount"], "FamPay", order_id, "approved"))
    d.commit()
    row = d.execute("SELECT * FROM fam_orders WHERE order_id=?", (order_id,)).fetchone()
    d.close(); return row

def mark_fam_expired(order_id):
    d = get_conn()
    d.execute("UPDATE fam_orders SET status='expired' WHERE order_id=? AND credited=0", (order_id,))
    d.commit(); d.close()

# ============ Template context ============
def cu():
    if "user_id" not in session: return None
    return get_user_by_id(session["user_id"])

def user_menu(active):
    return [
        {"label":"Dashboard","icon":"🏠","url":url_for("dashboard"),"active":active=="dash"},
        {"label":"Buy Likes","icon":"🚀","url":url_for("plans"),"active":active=="plans"},
        {"label":"Instant Like","icon":"⚡","url":url_for("instant_like"),"active":active=="instant"},
        {"label":"Ban Check","icon":"🚫","url":url_for("ban_check"),"active":active=="ban"},
        {"label":"My Orders","icon":"📜","url":url_for("history"),"active":active=="history"},
        {"label":"Add Funds","icon":"💳","url":url_for("add_funds"),"active":active=="funds"},
        {"label":"Transfer","icon":"💸","url":url_for("transfer"),"active":active=="transfer"},
        {"label":"Refer & Earn","icon":"🎁","url":url_for("refer"),"active":active=="refer"},
        {"label":"Profile","icon":"👤","url":url_for("profile"),"active":active=="profile"},
        {"label":"Logout","icon":"↩","url":url_for("user_logout"),"active":False},
    ]

def admin_menu(active):
    return [
        {"label":"Dashboard","icon":"📊","url":url_for("admin_panel"),"active":active=="dash"},
        {"label":"Packages","icon":"📦","url":url_for("admin_packages"),"active":active=="packages"},
        {"label":"Users","icon":"👥","url":url_for("admin_users"),"active":active=="users"},
        {"label":"Orders","icon":"🗂","url":url_for("admin_orders"),"active":active=="orders"},
        {"label":"Deposits","icon":"💰","url":url_for("admin_deposits"),"active":active=="deposits"},
        {"label":"Logout","icon":"↩","url":url_for("admin_logout"),"active":False},
    ]

@app.context_processor
def inject_globals():
    return {
        "CURRENCY": Config.CURRENCY_SYMBOL,
        "CSRF": gen_csrf(),
        "admin_logged": session.get("admin_logged", False),
    }

# ==================== AUTH ====================
@app.route("/login", methods=["GET","POST"])
def login_page():
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        ip = request.remote_addr or "unknown"
        key = f"{ip}:{u}"
        info = _login_attempts.get(key, {"count":0,"until":0})
        if info["until"] > time.time():
            flash(f"Too many attempts. Try again in {int(info['until']-time.time())}s.", "error")
            return redirect(url_for("login_page"))
        user = get_user_by_username(u)
        if not user or user["password"] != hash_pw(p):
            info["count"] = info.get("count",0)+1
            if info["count"] >= Config.MAX_LOGIN_ATTEMPTS:
                info["until"] = time.time() + Config.LOGIN_LOCKOUT
                info["count"] = 0
            _login_attempts[key] = info
            flash("Invalid username or password.", "error")
            return redirect(url_for("login_page"))
        if user["is_banned"]:
            flash("Your account has been suspended.", "error")
            return redirect(url_for("login_page"))
        _login_attempts.pop(key, None)
        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session.permanent = True
        gen_csrf()
        flash(f"Welcome back, {u} ⚡", "success")
        return redirect(url_for("dashboard"))
    return render_template("auth.html", mode="login")

@app.route("/register", methods=["GET","POST"])
def register_page():
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        ref = (request.form.get("ref") or "").strip()
        if len(u) < 3:
            flash("Username must be at least 3 characters.", "error"); return redirect(url_for("register_page"))
        if not all(c.isalnum() or c == "_" for c in u):
            flash("Username can only contain letters, numbers, and underscores.", "error"); return redirect(url_for("register_page"))
        if len(p) < 4:
            flash("Password must be at least 4 characters.", "error"); return redirect(url_for("register_page"))
        if get_user_by_username(u):
            flash("Username already exists.", "error"); return redirect(url_for("register_page"))
        ref_by = None
        if ref:
            r = get_user_by_ref(ref)
            if r: ref_by = r["id"]
        create_user(u, p, ref_by)
        if ref_by:
            d = db()
            d.execute("UPDATE users SET balance=balance+? WHERE id=?", (Config.REFERRAL_BONUS, ref_by))
            d.commit()
        flash("Account created. Please sign in.", "success")
        return redirect(url_for("login_page"))
    return render_template("auth.html", mode="register")

@app.route("/logout")
def user_logout():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("login_page"))

@app.route("/")
def index():
    if "user_id" in session: return redirect(url_for("dashboard"))
    return render_template("index.html")

# ==================== USER PAGES ====================
@app.route("/dashboard")
@login_required
def dashboard():
    u = cu()
    orders = get_orders_by_user(u["id"])
    total_o = len(orders)
    completed = sum(1 for o in orders if o["status"]=="completed")
    failed = sum(1 for o in orders if o["status"]=="failed")
    pending = sum(1 for o in orders if o["status"]=="pending")
    refs = get_referrals(u["id"])
    total_likes = sum(o["likes_added"] or 0 for o in orders)
    total_spent = sum(o["price"] for o in orders)
    success_rate = int((completed/total_o)*100) if total_o > 0 else 0
    ring_offset = 502 - int(502*success_rate/100)
    recent = orders[:5]
    return render_template("dashboard.html", u=u, menu=user_menu("dash"),
                          total_o=total_o, completed=completed, failed=failed, pending=pending,
                          refs=refs, total_likes=total_likes, total_spent=total_spent,
                          success_rate=success_rate, ring_offset=ring_offset, recent=recent)

@app.route("/plans", methods=["GET","POST"])
@login_required
def plans():
    u = cu()
    packages = get_active_packages()
    if request.method == "POST":
        uid = (request.form.get("uid") or "").strip()
        pid = request.form.get("plan") or ""
        server = (request.form.get("server") or "").strip().upper()
        pkg = get_package(pid)
        servers = get_server_list()
        if not uid or not uid.isdigit() or len(uid)<8 or len(uid)>12:
            flash("Invalid UID. Must be 8-12 digits.", "error"); return redirect(url_for("plans"))
        if not pkg:
            flash("Invalid package.", "error"); return redirect(url_for("plans"))
        if not server or server not in servers:
            flash("Please select a valid server.", "error"); return redirect(url_for("plans"))
        if u["balance"] < pkg["price"]:
            flash(f"Insufficient balance. You need {Config.CURRENCY_SYMBOL}{pkg['price']:.2f}.", "error")
            return redirect(url_for("add_funds"))
        d = db()
        d.execute("UPDATE users SET balance=balance-? WHERE id=?", (pkg["price"], u["id"]))
        d.commit()
        oid = place_order(u["id"], uid, f"{pkg['likes']} Likes", pkg["price"], "plan", server)
        result = call_like_api(uid, pkg["likes"], server)
        if result["ok"]:
            save_api_result(oid, "ok", result["body"], url=result["url"],
                la=result["likes_added"], lb=result["likes_before"], lc=result["likes_after"],
                player=result["player"], sc=result["status_code"])
            update_order_status(oid, "completed")
            session["result_modal"] = {"order_id": oid, "player": result["player"],
                "region": result.get("region") or server,
                "likes_before": result["likes_before"], "likes_after": result["likes_after"],
                "likes_added": result["likes_added"], "status_code": result["status_code"] or "1",
                "state":"ok", "plan_name": f"{pkg['likes']} Likes"}
            flash("Order placed successfully!", "success")
        elif result["error"] == "MAX_LIMIT_REACHED":
            save_api_result(oid, "error", result["body"], url=result["url"],
                la=result["likes_added"], lb=result["likes_before"], lc=result["likes_after"],
                player=result["player"], sc=result["status_code"])
            update_order_status(oid, "failed")
            d.execute("UPDATE users SET balance=balance+? WHERE id=?", (pkg["price"], u["id"]))
            d.commit()
            session["result_modal"] = {"order_id": oid, "player": result["player"],
                "region": result.get("region") or server,
                "likes_before": result["likes_before"], "likes_after": result["likes_after"],
                "likes_added": result["likes_added"], "status_code": result["status_code"] or "2",
                "state":"max", "plan_name": f"{pkg['likes']} Likes"}
            flash("Max limit reached. Balance refunded.", "warn")
        else:
            save_api_result(oid, "error", result["body"], url=result["url"],
                la=result["likes_added"], lb=result["likes_before"], lc=result["likes_after"],
                player=result["player"], sc=result["status_code"])
            update_order_status(oid, "failed")
            d.execute("UPDATE users SET balance=balance+? WHERE id=?", (pkg["price"], u["id"]))
            d.commit()
            flash(f"API error: {result['error']}. Balance refunded.", "error")
        return redirect(url_for("history"))
    return render_template("plans.html", u=u, menu=user_menu("plans"),
                          packages=packages,
                          servers=get_server_list())

@app.route("/instant-like", methods=["GET","POST"])
@login_required
def instant_like():
    u = cu()
    cost, count = get_instant_config()
    if request.method == "POST":
        uid = (request.form.get("uid") or "").strip()
        server = (request.form.get("server") or "").strip().upper()
        servers = get_server_list()
        if not uid or not uid.isdigit() or len(uid)<8 or len(uid)>12:
            flash("Invalid UID. Must be 8-12 digits.", "error"); return redirect(url_for("instant_like"))
        if not server or server not in servers:
            flash("Please select a valid server.", "error"); return redirect(url_for("instant_like"))
        if u["balance"] < cost:
            flash(f"Insufficient balance. You need {Config.CURRENCY_SYMBOL}{cost:.2f}.", "error")
            return redirect(url_for("add_funds"))
        d = db()
        d.execute("UPDATE users SET balance=balance-? WHERE id=?", (cost, u["id"]))
        d.commit()
        oid = place_order(u["id"], uid, f"Instant {count} Likes", cost, "instant", server)
        result = call_like_api(uid, count, server)
        if result["ok"]:
            save_api_result(oid, "ok", result["body"], url=result["url"],
                la=result["likes_added"], lb=result["likes_before"], lc=result["likes_after"],
                player=result["player"], sc=result["status_code"])
            update_order_status(oid, "completed")
            session["result_modal"] = {"order_id": oid, "player": result["player"],
                "region": result.get("region") or server,
                "likes_before": result["likes_before"], "likes_after": result["likes_after"],
                "likes_added": result["likes_added"], "status_code": result["status_code"] or "1",
                "state":"ok", "plan_name": f"Instant {count} Likes"}
            flash("Instant likes sent successfully!", "success")
        elif result["error"] == "MAX_LIMIT_REACHED":
            save_api_result(oid, "error", result["body"], url=result["url"],
                la=result["likes_added"], lb=result["likes_before"], lc=result["likes_after"],
                player=result["player"], sc=result["status_code"])
            update_order_status(oid, "failed")
            d.execute("UPDATE users SET balance=balance+? WHERE id=?", (cost, u["id"]))
            d.commit()
            session["result_modal"] = {"order_id": oid, "player": result["player"],
                "region": result.get("region") or server,
                "likes_before": result["likes_before"], "likes_after": result["likes_after"],
                "likes_added": result["likes_added"], "status_code": result["status_code"] or "2",
                "state":"max", "plan_name": f"Instant {count} Likes"}
            flash("Max limit reached. Balance refunded.", "warn")
        else:
            save_api_result(oid, "error", result["body"], url=result["url"],
                la=result["likes_added"], lb=result["likes_before"], lc=result["likes_after"],
                player=result["player"], sc=result["status_code"])
            update_order_status(oid, "failed")
            d.execute("UPDATE users SET balance=balance+? WHERE id=?", (cost, u["id"]))
            d.commit()
            flash(f"API error: {result['error']}. Balance refunded.", "error")
        return redirect(url_for("history"))
    return render_template("instant_like.html", u=u, menu=user_menu("instant"),
                          cost=cost, count=count, servers=get_server_list())

@app.route("/ban-check", methods=["GET","POST"])
@login_required
def ban_check():
    u = cu()
    result = None; uid_input = ""; region_input = ""
    if request.method == "POST":
        uid_input = (request.form.get("uid") or "").strip()
        region_input = (request.form.get("region") or "").strip().upper()
        servers = get_server_list()
        if not uid_input or not uid_input.isdigit() or len(uid_input)<8 or len(uid_input)>12:
            flash("Invalid UID. Must be 8-12 digits.", "error")
        elif not region_input or region_input not in servers:
            flash("Please select a valid region.", "error")
        else:
            result = call_ban_check_api(uid_input, region_input)
            if not result.get("ok"):
                flash(f"API error: {result.get('error','Unknown')}", "error")
    return render_template("ban_check.html", u=u, menu=user_menu("ban"),
                          result=result, uid_input=uid_input, region_input=region_input,
                          servers=get_server_list())

@app.route("/history")
@login_required
def history():
    u = cu()
    orders = get_orders_by_user(u["id"])
    deposits = db().execute("SELECT * FROM deposits WHERE user_id=? ORDER BY created_at DESC", (u["id"],)).fetchall()
    modal_data = session.pop("result_modal", None)
    return render_template("history.html", u=u, menu=user_menu("history"),
                          orders=orders, deposits=deposits, modal_data=modal_data)

@app.route("/add-funds")
@login_required
def add_funds():
    u = cu()
    deposits = db().execute(
        "SELECT * FROM deposits WHERE user_id=? ORDER BY created_at DESC LIMIT 10",
        (u["id"],)).fetchall()
    return render_template("add_funds.html", u=u, menu=user_menu("funds"),
                          deposits=deposits)

@app.route("/create-fam-order", methods=["POST"])
@login_required
def create_fam_order_route():
    u = cu()
    try:
        data = request.get_json() or {}
        amt = float(data.get("amount") or 0)
    except: amt = 0
    if amt < Config.MIN_DEPOSIT:
        return jsonify({"ok":False,"error":f"Minimum amount is {Config.CURRENCY_SYMBOL}{Config.MIN_DEPOSIT:.2f}"}), 400
    resp = create_fam_order(amt, u["id"])
    inner = resp.get("data") or {}
    order_id = resp.get("order_id") or inner.get("order_id")
    qr_url = resp.get("qr_url") or inner.get("qr_url") or resp.get("qr_code") or inner.get("qr_code")
    if not order_id or not qr_url:
        return jsonify({"ok":False,"error":resp.get("message") or "Invalid response from gateway"}), 500
    save_fam_order(order_id, u["id"], amt, qr_url)
    return jsonify({"ok":True,"order_id":order_id,"qr_url":qr_url})

@app.route("/check-payment/<order_id>")
@login_required
def check_payment(order_id):
    u = cu()
    row = get_fam_order(order_id)
    if not row or row["user_id"] != u["id"]:
        return jsonify({"status":"error","message":"Order not found"})
    if row["credited"] == 1:
        return jsonify({"status":"success","amount":row["amount"]})
    resp = verify_fam_order(order_id)
    gw_status = (resp.get("status") or "").lower()
    inner = resp.get("data") or {}
    gw_status = gw_status or (inner.get("status") or "").lower()
    if gw_status == "success":
        updated = credit_fam_order(order_id)
        amt = updated["amount"] if updated else row["amount"]
        return jsonify({"status":"success","amount":amt})
    if gw_status == "expired":
        mark_fam_expired(order_id)
        return jsonify({"status":"expired"})
    return jsonify({"status":"pending"})

@app.route("/change-password", methods=["GET","POST"])
@login_required
def change_password():
    u = cu()
    if request.method == "POST":
        old = request.form.get("old") or ""
        new = request.form.get("new") or ""
        conf = request.form.get("confirm") or ""
        if u["password"] != hash_pw(old): flash("Current password is incorrect.", "error")
        elif len(new) < 4: flash("New password must be at least 4 characters.", "error")
        elif new != conf: flash("Passwords do not match.", "error")
        else:
            update_password(u["id"], new); flash("Password updated.", "success")
            return redirect(url_for("dashboard"))
    return render_template("profile.html", u=u, menu=user_menu("password"), mode="password")

@app.route("/profile")
@login_required
def profile():
    u = cu()
    orders = get_orders_by_user(u["id"])
    total_spent = sum(o["price"] for o in orders)
    total_likes = sum(o["likes_added"] or 0 for o in orders)
    refs = get_referrals(u["id"])
    return render_template("profile.html", u=u, menu=user_menu("profile"),
                          mode="profile", orders=orders, total_spent=total_spent,
                          total_likes=total_likes, refs=refs)

@app.route("/transfer", methods=["GET","POST"])
@login_required
def transfer():
    u = cu()
    if request.method == "POST":
        to_u = (request.form.get("to_user") or "").strip()
        try: amt = float(request.form.get("amount") or 0)
        except: amt = 0
        ok, msg = transfer_funds(u["id"], to_u, amt)
        flash(msg, "success" if ok else "error")
        return redirect(url_for("transfer"))
    transfers = get_user_transfers(u["id"])
    return render_template("transfer.html", u=u, menu=user_menu("transfer"), transfers=transfers)

@app.route("/refer")
@login_required
def refer():
    u = cu()
    refs = get_referrals(u["id"])
    ref_link = f"{request.host_url.rstrip('/')}/register?ref={u['referral_code']}"
    return render_template("refer.html", u=u, menu=user_menu("refer"),
                          refs=refs, ref_link=ref_link)

# ==================== ADMIN ====================
@app.route("/admin")
def admin_panel():
    if not session.get("admin_logged"):
        return render_template("admin/login.html")
    d = db()
    users_raw = get_all_users()
    total_revenue = d.execute("SELECT COALESCE(SUM(amount),0) FROM deposits WHERE status='approved'").fetchone()[0]
    stats = {
        "users": len(users_raw),
        "orders": d.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
        "pending_dep": d.execute("SELECT COUNT(*) FROM deposits WHERE status='pending'").fetchone()[0],
        "revenue": total_revenue,
        "balance": d.execute("SELECT COALESCE(SUM(balance),0) FROM users").fetchone()[0],
        "active": d.execute("SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0],
        "packages": d.execute("SELECT COUNT(*) FROM packages").fetchone()[0],
    }
    s = get_settings()
    return render_template("admin/dashboard.html", menu=admin_menu("dash"), stats=stats, s=s)

@app.route("/admin/login", methods=["POST"])
def admin_login():
    u = request.form.get("username") or ""
    p = request.form.get("password") or ""
    ip = request.remote_addr or "unknown"
    key = f"admin:{ip}"
    info = _login_attempts.get(key, {"count":0,"until":0})
    if info["until"] > time.time():
        flash(f"Too many attempts. Try again in {int(info['until']-time.time())}s.", "error")
        return redirect(url_for("admin_panel"))
    if u == Config.ADMIN_USER and p == Config.ADMIN_PASS:
        _login_attempts.pop(key, None)
        session.clear()
        session["admin_logged"] = True
        session.permanent = True
        gen_csrf()
        flash("Signed in as admin.", "success")
    else:
        info["count"] = info.get("count",0)+1
        if info["count"] >= Config.MAX_LOGIN_ATTEMPTS:
            info["until"] = time.time() + Config.LOGIN_LOCKOUT
            info["count"] = 0
        _login_attempts[key] = info
        flash("Invalid credentials.", "error")
    return redirect(url_for("admin_panel"))

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/update-settings", methods=["POST"])
@admin_required
def admin_update_settings():
    update_settings({
        "api_url": (request.form.get("api_url") or "").strip(),
        "api_key": (request.form.get("api_key") or "").strip(),
        "api_method": (request.form.get("api_method") or "GET").strip().upper(),
        "api_param_name": (request.form.get("api_param_name") or "uid").strip(),
        "api_key_param": (request.form.get("api_key_param") or "key").strip(),
        "api_server_param": (request.form.get("api_server_param") or "server_name").strip(),
        "api_servers": (request.form.get("api_servers") or "").strip(),
        "api_extra_params": (request.form.get("api_extra_params") or "").strip(),
        "api_success_mode": (request.form.get("api_success_mode") or "json").strip().lower(),
        "api_success_keyword": (request.form.get("api_success_keyword") or "success").strip(),
        "api_status_field": (request.form.get("api_status_field") or "status").strip(),
        "api_status_value": (request.form.get("api_status_value") or "1").strip(),
        "api_max_field": (request.form.get("api_max_field") or "status").strip(),
        "api_max_value": (request.form.get("api_max_value") or "2").strip(),
        "api_likes_field": (request.form.get("api_likes_field") or "LikesGivenByAPI").strip(),
        "api_total_field": (request.form.get("api_total_field") or "LikesafterCommand").strip(),
        "api_player_field": (request.form.get("api_player_field") or "PlayerNickname").strip(),
        "api_before_field": (request.form.get("api_before_field") or "LikesbeforeCommand").strip(),
        "api_region_field": (request.form.get("api_region_field") or "Region").strip(),
        "instant_like_cost": request.form.get("instant_like_cost") or 30,
        "instant_like_count": request.form.get("instant_like_count") or 1000,
    })
    flash("Settings saved.", "success")
    return redirect(url_for("admin_panel"))

# ---- PACKAGES CRUD ----
@app.route("/admin/packages")
@admin_required
def admin_packages():
    packages = get_all_packages()
    return render_template("admin/packages.html", menu=admin_menu("packages"), packages=packages)

@app.route("/admin/packages/add", methods=["POST"])
@admin_required
def admin_package_add():
    try:
        name = (request.form.get("name") or "").strip()
        likes = int(request.form.get("likes") or 0)
        price = float(request.form.get("price") or 0)
        badge = (request.form.get("badge") or "").strip().upper()
        desc = (request.form.get("description") or "").strip()
        sort = int(request.form.get("sort_order") or 0)
        if not name or likes <= 0 or price <= 0:
            flash("Name, likes, price required.", "error"); return redirect(url_for("admin_packages"))
        d = db()
        d.execute("""INSERT INTO packages (name,likes,price,badge,description,sort_order,is_active)
                     VALUES (?,?,?,?,?,?,1)""", (name, likes, price, badge, desc, sort))
        d.commit()
        flash(f"Package '{name}' added.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for("admin_packages"))

@app.route("/admin/packages/<int:pid>/update", methods=["POST"])
@admin_required
def admin_package_update(pid):
    try:
        name = (request.form.get("name") or "").strip()
        likes = int(request.form.get("likes") or 0)
        price = float(request.form.get("price") or 0)
        badge = (request.form.get("badge") or "").strip().upper()
        desc = (request.form.get("description") or "").strip()
        sort = int(request.form.get("sort_order") or 0)
        active = 1 if request.form.get("is_active") else 0
        d = db()
        d.execute("""UPDATE packages SET name=?,likes=?,price=?,badge=?,description=?,
                     sort_order=?,is_active=? WHERE id=?""",
                  (name, likes, price, badge, desc, sort, active, pid))
        d.commit()
        flash("Package updated.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for("admin_packages"))

@app.route("/admin/packages/<int:pid>/delete", methods=["POST"])
@admin_required
def admin_package_delete(pid):
    d = db()
    d.execute("DELETE FROM packages WHERE id=?", (pid,))
    d.commit()
    flash(f"Package #{pid} deleted.", "info")
    return redirect(url_for("admin_packages"))

@app.route("/admin/packages/<int:pid>/toggle", methods=["POST"])
@admin_required
def admin_package_toggle(pid):
    d = db()
    p = d.execute("SELECT * FROM packages WHERE id=?", (pid,)).fetchone()
    if p:
        d.execute("UPDATE packages SET is_active=? WHERE id=?", (0 if p["is_active"] else 1, pid))
        d.commit()
        flash(f"Package #{pid} {'disabled' if p['is_active'] else 'enabled'}.", "info")
    return redirect(url_for("admin_packages"))

# ---- USERS / ORDERS / DEPOSITS ----
@app.route("/admin/users")
@admin_required
def admin_users():
    users = get_all_users()
    return render_template("admin/users.html", menu=admin_menu("users"), users=users)

@app.route("/admin/coins", methods=["GET","POST"])
@admin_required
def admin_coins():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            tid = int(request.form.get("user_id") or 0)
            amt = float(request.form.get("amount") or 0) if action != "ban" else 0
        except: tid, amt = 0, 0
        target = get_user_by_id(tid)
        if not target: flash("User not found.", "error"); return redirect(url_for("admin_users"))
        if action == "add":
            if amt <= 0: flash("Amount must be positive.", "error")
            else: admin_add_coins(tid, amt); flash(f"Added {Config.CURRENCY_SYMBOL}{amt:.2f} to {target['username']}.", "success")
        elif action == "remove":
            if amt <= 0: flash("Amount must be positive.", "error")
            else: admin_remove_coins(tid, amt); flash(f"Removed {Config.CURRENCY_SYMBOL}{amt:.2f} from {target['username']}.", "success")
        elif action == "ban":
            n = admin_toggle_ban(tid)
            flash(f"{target['username']} is now {'banned' if n else 'active'}.", "success" if n else "info")
        return redirect(url_for("admin_users"))
    return redirect(url_for("admin_users"))

@app.route("/admin/orders")
@admin_required
def admin_orders():
    orders = get_all_orders()
    return render_template("admin/orders.html", menu=admin_menu("orders"), orders=orders)

@app.route("/admin/order/<int:oid>/<status>")
@admin_required
def admin_update_order(oid, status):
    if status in ("pending","completed","failed"):
        update_order_status(oid, status)
        flash(f"Order #{oid} → {status}.", "success")
    return redirect(request.referrer or url_for("admin_orders"))

@app.route("/admin/deposits")
@admin_required
def admin_deposits():
    deposits = get_all_deposits()
    return render_template("admin/deposits.html", menu=admin_menu("deposits"), deposits=deposits)

@app.route("/admin/deposit/<int:did>/approve")
@admin_required
def admin_approve_deposit(did):
    if approve_deposit(did): flash(f"Deposit #{did} approved.", "success")
    return redirect(url_for("admin_deposits"))

@app.route("/admin/deposit/<int:did>/reject")
@admin_required
def admin_reject_deposit(did):
    reject_deposit(did); flash(f"Deposit #{did} rejected.", "error")
    return redirect(url_for("admin_deposits"))

# ==================== ERRORS ====================
@app.errorhandler(403)
def err_403(e):
    return render_template("error.html", code=403, title="ACCESS DENIED",
                          message="Your session may have expired."), 403

@app.errorhandler(404)
def err_404(e):
    return render_template("error.html", code=404, title="PAGE NOT FOUND",
                          message="The page you're looking for doesn't exist."), 404

# ==================== MAIN ====================
if __name__ == "__main__":
    init_db()
    print("=" * 62)
    print("  ⚡ FF BOOST HUB — Royal Amethyst + INR + FamGateway")
    print("=" * 62)
    print(f"  User  : http://127.0.0.1:500004/")
    print(f"  Admin : http://127.0.0.1:500004/admin")
    print(f"  Login : {Config.ADMIN_USER} / {Config.ADMIN_PASS}")
    print(f"  Currency: {Config.CURRENCY_CODE} ({Config.CURRENCY_SYMBOL})")
    print("=" * 62)
    app.run(host="0.0.0.0", port=5099, debug=True)