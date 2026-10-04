import socket
import threading
import tkinter as tk
from tkinter import messagebox, filedialog
import random
import time
import json
import sqlite3
import hashlib
import re
import sys
import traceback
import base64
import os
from io import BytesIO

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("Pillow не установлен. Картинки не будут показываться как превью.")
    print("Установи: pip install pillow")

DEFAULT_PORT = 5555
DEFAULT_HOST = '127.0.0.1'
DB_FILE = "matrix_users.db"
SALT = b"matrix_salt_v2_change_me_in_prod"
STICKERS_DIR = "stickers"
DOWNLOADS_DIR = "downloads"
MAX_FILE_SIZE = 10 * 1024 * 1024

STICKERS = {
    "neo":       "🕶️",
    "pill_red":  "💊",
    "pill_blue": "🔵",
    "agent":     "🕴️",
    "code":      "💚",
    "cat":       "🐱",
    "skull":     "💀",
    "ok":        "🆗",
    "fire":      "🔥",
    "100":       "💯",
    "matrix":    "🧮",
    "phone":     "📞",
}

BG_COLOR     = "#000000"
GREEN        = "#00FF41"
GREEN_DIM    = "#00AA2B"
GREEN_DARK   = "#0A2A0A"
GREEN_BRIGHT = "#39FF14"
INPUT_FG     = "#E8FFE8"
INPUT_BG     = "#0A2A0A"
CURSOR       = "#7FFF7F"
GOLD         = "#FFD700"
RED          = "#FF3333"
PINK         = "#FF69B4"
ORANGE       = "#FFA500"
LINK_COLOR   = "#7FD4FF"

FONT       = ("Consolas", 11)
FONT_BOLD  = ("Consolas", 11, "bold")
FONT_SMALL = ("Consolas", 9)
FONT_TITLE = ("Consolas", 14, "bold")


def log_error(exc_type, exc_value, exc_tb):
    with open("error.log", "a", encoding="utf-8") as f:
        f.write("=" * 50 + "\n")
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
        traceback.print_exception(exc_type, exc_value, exc_tb, file=f)


sys.excepthook = log_error


class UserDB:
    def __init__(self, path=DB_FILE):
        self.path = path
        self.lock = threading.Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, nickname TEXT NOT NULL, created_at REAL NOT NULL)")
            cur.execute("CREATE TABLE IF NOT EXISTS friendships (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, friend_id INTEGER NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(user_id, friend_id))")
            conn.commit()

    @staticmethod
    def _hash_password(password):
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), SALT, 100_000).hex()

    def register(self, email, password, nickname):
        email = email.strip().lower()
        nickname = nickname.strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return False, "Некорректный email"
        if len(password) < 4:
            return False, "Пароль слишком короткий (мин. 4)"
        if not nickname:
            return False, "Введите ник"
        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE email=?", (email,))
            if cur.fetchone():
                return False, "Email уже зарегистрирован"
            cur.execute("INSERT INTO users(email,password_hash,nickname,created_at) VALUES(?,?,?,?)",
                        (email, self._hash_password(password), nickname, time.time()))
            conn.commit()
        return True, "Регистрация успешна"

    def login(self, email, password):
        email = email.strip().lower()
        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE email=?", (email,))
            row = cur.fetchone()
        if not row:
            return None
        if row['password_hash'] != self._hash_password(password):
            return None
        return {"id": row["id"], "email": row["email"], "nickname": row["nickname"]}

    def find_user_by_email(self, email):
        email = email.strip().lower()
        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id,email,nickname FROM users WHERE email=?", (email,))
            row = cur.fetchone()
        return dict(row) if row else None

    def send_friend_request(self, from_id, to_email):
        target = self.find_user_by_email(to_email)
        if not target:
            return False, "Пользователь с таким email не найден"
        if target["id"] == from_id:
            return False, "Нельзя добавить себя"
        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM friendships WHERE (user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)",
                        (from_id, target["id"], target["id"], from_id))
            existing = cur.fetchone()
            if existing:
                if existing["status"] == "accepted":
                    return False, "Вы уже друзья"
                if existing["user_id"] == target["id"] and existing["status"] == "pending":
                    cur.execute("UPDATE friendships SET status='accepted' WHERE id=?", (existing["id"],))
                    conn.commit()
                    return True, f"{target['nickname']} теперь ваш друг!"
                return False, "Заявка уже отправлена"
            cur.execute("INSERT INTO friendships(user_id,friend_id,status,created_at) VALUES(?,?,?,?)",
                        (from_id, target["id"], "pending", time.time()))
            conn.commit()
        return True, f"Заявка отправлена {target['nickname']}"

    def get_friends(self, user_id):
        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT u.id,u.email,u.nickname FROM friendships f JOIN users u ON u.id = CASE WHEN f.user_id = ? THEN f.friend_id ELSE f.user_id END WHERE (f.user_id = ? OR f.friend_id = ?) AND f.status = 'accepted'",
                        (user_id, user_id, user_id))
            return [dict(r) for r in cur.fetchall()]

    def get_pending_requests(self, user_id):
        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT f.id as fid, u.id,u.email,u.nickname FROM friendships f JOIN users u ON u.id = f.user_id WHERE f.friend_id = ? AND f.status = 'pending'",
                        (user_id,))
            return [dict(r) for r in cur.fetchall()]

    def accept_request(self, fid_id, user_id):
        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE friendships SET status='accepted' WHERE id=? AND friend_id=?", (fid_id, user_id))
            conn.commit()
            return cur.rowcount > 0

    def reject_request(self, fid_id, user_id):
        with self.lock, self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM friendships WHERE id=? AND friend_id=?", (fid_id, user_id))
            conn.commit()
            return cur.rowcount > 0
class MatrixServer:
    def __init__(self, host, port, db, log_callback=None):
        self.host = host
        self.port = port
        self.db = db
        self.log = log_callback or (lambda m: None)
        self.clients = {}
        self.lock = threading.Lock()
        self.running = False
        self.server_socket = None

    def start(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen()
        self.running = True
        self.log(f"*** Сервер запущен на {self.host}:{self.port} ***")
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while self.running:
            try:
                cs, addr = self.server_socket.accept()
                threading.Thread(target=self._handle_client, args=(cs, addr), daemon=True).start()
            except OSError:
                break
            except Exception as e:
                self.log(f"*** accept error: {e} ***")

    def _send(self, sock, obj):
        try:
            data = (json.dumps(obj, ensure_ascii=False) + "\n").encode('utf-8')
            sock.sendall(data)
        except Exception:
            pass

    def _recv(self, sock):
        buf = b""
        while True:
            try:
                chunk = sock.recv(1)
            except Exception:
                return None
            if not chunk:
                return None
            if chunk == b"\n":
                try:
                    return json.loads(buf.decode('utf-8'))
                except Exception:
                    return None
            buf += chunk

    def _push_to_user(self, user_id, obj):
        with self.lock:
            sock = self.clients.get(user_id)
        if sock:
            self._send(sock, obj)

    def _send_to_self(self, user_id, obj):
        self._push_to_user(user_id, obj)

    def _handle_client(self, sock, addr):
        user = None
        try:
            packet = self._recv(sock)
            if not packet:
                return
            action = packet.get("action")
            email = packet.get("email", "")
            password = packet.get("password", "")
            if action == "register":
                nickname = packet.get("nickname", "").strip()
                ok, msg = self.db.register(email, password, nickname)
                if not ok:
                    self._send(sock, {"type": "auth_result", "ok": False, "msg": msg})
                    return
                user = self.db.login(email, password)
                self._send(sock, {"type": "auth_result", "ok": True, "msg": "Регистрация успешна", "user": user})
            elif action == "login":
                user = self.db.login(email, password)
                if not user:
                    self._send(sock, {"type": "auth_result", "ok": False, "msg": "Неверный email или пароль"})
                    return
                self._send(sock, {"type": "auth_result", "ok": True, "msg": "OK", "user": user})
            else:
                return
            if not user:
                return
            with self.lock:
                if user["id"] in self.clients:
                    try:
                        self.clients[user["id"]].close()
                    except Exception:
                        pass
                self.clients[user["id"]] = sock
            self.log(f"*** {user['nickname']} ({user['email']}) онлайн ***")
            self._send(sock, {"type": "welcome", "user": user,
                              "friends": self.db.get_friends(user["id"]),
                              "pending": self.db.get_pending_requests(user["id"])})
            while self.running:
                msg = self._recv(sock)
                if not msg:
                    break
                self._process_message(user, msg)
        except Exception as e:
            self.log(f"*** client {addr} error: {e} ***")
        finally:
            with self.lock:
                if user and self.clients.get(user["id"]) == sock:
                    self.clients.pop(user["id"], None)
            if user:
                self.log(f"*** {user['nickname']} отключился ***")
            try:
                sock.close()
            except Exception:
                pass

    def _process_message(self, user, msg):
        mtype = msg.get("type")

        if mtype == "message":
            text = msg.get("text", "")
            payload = {"type": "message", "from": user["nickname"], "email": user["email"],
                       "text": text, "ts": time.time()}
            with self.lock:
                recipients = list(self.clients.keys())
            for uid in recipients:
                self._push_to_user(uid, payload)

        elif mtype == "file":
            to_email = msg.get("to", "").strip().lower()
            file_name = msg.get("name", "file")
            file_data = msg.get("data", "")
            file_size = msg.get("size", 0)
            file_mime = msg.get("mime", "application/octet-stream")

            if file_size > MAX_FILE_SIZE:
                self._send_to_self(user["id"], {
                    "type": "system",
                    "text": f"*** Файл слишком большой (макс. {MAX_FILE_SIZE // (1024*1024)} MB) ***"
                })
                return

            payload = {
                "type": "file",
                "from": user["nickname"],
                "from_email": user["email"],
                "to_email": to_email,
                "name": file_name,
                "data": file_data,
                "size": file_size,
                "mime": file_mime,
                "ts": time.time()
            }

            if to_email:
                target = self.db.find_user_by_email(to_email)
                if not target:
                    self._send_to_self(user["id"], {"type": "system", "text": f"*** {to_email} не найден ***"})
                    return
                friends = {f["email"] for f in self.db.get_friends(user["id"])}
                if to_email not in friends:
                    self._send_to_self(user["id"], {"type": "system",
                                                    "text": f"*** {target['nickname']} не в ваших друзьях ***"})
                    return
                payload["to"] = target["nickname"]
                self._push_to_user(user["id"], payload)
                self._push_to_user(target["id"], payload)
            else:
                payload["to"] = None
                with self.lock:
                    recipients = list(self.clients.keys())
                for uid in recipients:
                    self._push_to_user(uid, payload)

        elif mtype == "sticker":
            to_email = msg.get("to", "").strip().lower()
            sticker = msg.get("sticker", "")
            payload = {
                "type": "sticker",
                "from": user["nickname"],
                "from_email": user["email"],
                "to_email": to_email,
                "sticker": sticker,
                "ts": time.time()
            }
            if to_email:
                target = self.db.find_user_by_email(to_email)
                if not target:
                    return
                friends = {f["email"] for f in self.db.get_friends(user["id"])}
                if to_email not in friends:
                    return
                payload["to"] = target["nickname"]
                self._push_to_user(user["id"], payload)
                self._push_to_user(target["id"], payload)
            else:
                payload["to"] = None
                with self.lock:
                    recipients = list(self.clients.keys())
                for uid in recipients:
                    self._push_to_user(uid, payload)

        elif mtype == "pm":
            to_email = msg.get("to", "").strip().lower()
            text = msg.get("text", "")
            target = self.db.find_user_by_email(to_email)
            if not target:
                self._send_to_self(user["id"], {"type": "system", "text": f"*** {to_email} не найден ***"})
                return
            friends = {f["email"] for f in self.db.get_friends(user["id"])}
            if to_email not in friends:
                self._send_to_self(user["id"], {"type": "system",
                                                "text": f"*** {target['nickname']} не в ваших друзьях ***"})
                return
            payload = {"type": "pm", "from": user["nickname"], "from_email": user["email"],
                       "to": target["nickname"], "to_email": target["email"],
                       "text": text, "ts": time.time()}
            self._push_to_user(user["id"], payload)
            self._push_to_user(target["id"], payload)

        elif mtype == "add_friend":
            email = msg.get("email", "").strip().lower()
            ok, text = self.db.send_friend_request(user["id"], email)
            self._send_to_self(user["id"], {"type": "system", "text": f"*** {text} ***"})
            self._send_to_self(user["id"], {"type": "friends_update",
                                            "friends": self.db.get_friends(user["id"]),
                                            "pending": self.db.get_pending_requests(user["id"])})
            if ok:
                target = self.db.find_user_by_email(email)
                if target:
                    self._push_to_user(target["id"], {"type": "friends_update",
                                                      "friends": self.db.get_friends(target["id"]),
                                                      "pending": self.db.get_pending_requests(target["id"])})
                    self._push_to_user(target["id"], {"type": "system",
                                                      "text": f"*** {user['nickname']} добавил вас в друзья ***"})

        elif mtype == "accept_friend":
            fid = msg.get("fid")
            if self.db.accept_request(fid, user["id"]):
                self._send_to_self(user["id"], {"type": "friends_update",
                                                "friends": self.db.get_friends(user["id"]),
                                                "pending": self.db.get_pending_requests(user["id"])})
                self._send_to_self(user["id"], {"type": "system", "text": "*** Заявка принята ***"})

        elif mtype == "reject_friend":
            fid = msg.get("fid")
            if self.db.reject_request(fid, user["id"]):
                self._send_to_self(user["id"], {"type": "friends_update",
                                                "friends": self.db.get_friends(user["id"]),
                                                "pending": self.db.get_pending_requests(user["id"])})
                self._send_to_self(user["id"], {"type": "system", "text": "*** Заявка отклонена ***"})

        elif mtype == "get_friends":
            self._send_to_self(user["id"], {"type": "friends_update",
                                            "friends": self.db.get_friends(user["id"]),
                                            "pending": self.db.get_pending_requests(user["id"])})

    def stop(self):
        self.running = False
        try:
            if self.server_socket:
                self.server_socket.close()
        except Exception:
            pass
        with self.lock:
            for s in self.clients.values():
                try:
                    s.close()
                except Exception:
                    pass
            self.clients.clear()
class MatrixMessenger:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("MATRIX MESSENGER")
        self.root.geometry("1000x700")
        self.root.configure(bg=BG_COLOR)
        self.root.minsize(800, 550)
        self.sock = None
        self.user = None
        self.connected = False
        self.server = None
        self.db = None
        self.friends = []
        self.pending = []
        self.send_lock = threading.Lock()
        self.current_recipient = None
        self.rain_canvas = None
        self.rain_items = []
        self.rain_active = False
        self._image_refs = []
        self._build_auth_ui()
        self._animate_title()

    def _build_auth_ui(self):
        self.auth_frame = tk.Frame(self.root, bg=BG_COLOR)
        self.auth_frame.pack(expand=True, fill='both')
        self.rain_canvas = tk.Canvas(self.auth_frame, bg=BG_COLOR, height=150,
                                     highlightthickness=0, bd=0)
        self.rain_canvas.pack(fill='x')
        self.rain_canvas.create_text(500, 50, text="MATRIX MESSENGER",
                                     font=("Consolas", 24, "bold"), fill=GREEN_BRIGHT, tags="title")
        self.rain_canvas.create_text(500, 100, text="Wake up, Neo...",
                                     font=("Consolas", 13, "italic"), fill=GREEN_DIM, tags="subtitle")
        self._start_rain()

        self.tab = tk.StringVar(value="login")
        tab_frame = tk.Frame(self.auth_frame, bg=BG_COLOR)
        tab_frame.pack(pady=10)

        self.login_tab_btn = tk.Button(tab_frame, text="ВХОД", font=FONT_BOLD,
                                       bg=GREEN_DARK, fg=GREEN_BRIGHT,
                                       activebackground=GREEN, activeforeground=BG_COLOR,
                                       relief='flat', bd=0, padx=20, pady=8, cursor='hand2',
                                       command=lambda: self._switch_tab("login"))
        self.login_tab_btn.pack(side='left', padx=5)

        self.reg_tab_btn = tk.Button(tab_frame, text="РЕГИСТРАЦИЯ", font=FONT_BOLD,
                                     bg=BG_COLOR, fg=GREEN_DIM,
                                     activebackground=GREEN, activeforeground=BG_COLOR,
                                     relief='flat', bd=0, padx=20, pady=8, cursor='hand2',
                                     command=lambda: self._switch_tab("register"))
        self.reg_tab_btn.pack(side='left', padx=5)

        self.form = tk.Frame(self.auth_frame, bg=BG_COLOR)
        self.form.pack(pady=10)

        tk.Label(self.form, text="> Email:", font=FONT, fg=GREEN, bg=BG_COLOR).grid(row=0, column=0, sticky='w', pady=(5, 2))
        self.email_entry = tk.Entry(self.form, font=FONT_BOLD, bg=INPUT_BG, fg=INPUT_FG,
                                    insertbackground=CURSOR, relief='flat',
                                    width=36, justify='center')
        self.email_entry.grid(row=1, column=0, ipady=6, pady=(0, 10))

        tk.Label(self.form, text="> Пароль:", font=FONT, fg=GREEN, bg=BG_COLOR).grid(row=2, column=0, sticky='w', pady=(5, 2))
        self.pwd_entry = tk.Entry(self.form, font=FONT_BOLD, bg=INPUT_BG, fg=INPUT_FG,
                                  insertbackground=CURSOR, relief='flat',
                                  width=36, justify='center', show='*')
        self.pwd_entry.grid(row=3, column=0, ipady=6, pady=(0, 10))
        self.pwd_entry.bind('<Return>', lambda e: self._do_auth())

        self.nick_label = tk.Label(self.form, text="> Ник:", font=FONT, fg=GREEN, bg=BG_COLOR)
        self.nick_entry = tk.Entry(self.form, font=FONT_BOLD, bg=INPUT_BG, fg=INPUT_FG,
                                   insertbackground=CURSOR, relief='flat',
                                   width=36, justify='center')
        self.nick_entry.bind('<Return>', lambda e: self._do_auth())

        net_frame = tk.Frame(self.auth_frame, bg=BG_COLOR)
        net_frame.pack(pady=5)
        tk.Label(net_frame, text="IP:", font=FONT, fg=GREEN, bg=BG_COLOR).grid(row=0, column=0, padx=4)
        self.ip_entry = tk.Entry(net_frame, font=FONT, bg=INPUT_BG, fg=INPUT_FG,
                                 insertbackground=CURSOR, relief='flat', width=14, justify='center')
        self.ip_entry.grid(row=0, column=1, ipady=4)
        self.ip_entry.insert(0, DEFAULT_HOST)
        tk.Label(net_frame, text="Порт:", font=FONT, fg=GREEN, bg=BG_COLOR).grid(row=0, column=2, padx=4)
        self.port_entry = tk.Entry(net_frame, font=FONT, bg=INPUT_BG, fg=INPUT_FG,
                                   insertbackground=CURSOR, relief='flat', width=8, justify='center')
        self.port_entry.grid(row=0, column=3, ipady=4)
        self.port_entry.insert(0, str(DEFAULT_PORT))

        btn_frame = tk.Frame(self.auth_frame, bg=BG_COLOR)
        btn_frame.pack(pady=20)
        self.auth_btn = tk.Button(btn_frame, text="[ ВОЙТИ ]", font=FONT_BOLD,
                                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                                  activebackground=GREEN, activeforeground=BG_COLOR,
                                  relief='flat', bd=0, padx=22, pady=10, cursor='hand2',
                                  command=self._do_auth)
        self.auth_btn.grid(row=0, column=0, padx=5)
        tk.Button(btn_frame, text="[ СЕРВЕР ]", font=FONT_BOLD,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  activebackground=GREEN, activeforeground=BG_COLOR,
                  relief='flat', bd=0, padx=22, pady=10, cursor='hand2',
                  command=self._start_server_only).grid(row=0, column=1, padx=5)
        tk.Button(btn_frame, text="[ СЕРВЕР + ВОЙТИ ]", font=FONT_BOLD,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  activebackground=GREEN, activeforeground=BG_COLOR,
                  relief='flat', bd=0, padx=22, pady=10, cursor='hand2',
                  command=self._start_both).grid(row=0, column=2, padx=5)

        self.status_label = tk.Label(self.auth_frame, text="[ ожидание ]", font=FONT,
                                     fg=GREEN_DIM, bg=BG_COLOR)
        self.status_label.pack(pady=5)

    def _switch_tab(self, mode):
        self.tab.set(mode)
        if mode == "login":
            self.login_tab_btn.config(bg=GREEN_DARK, fg=GREEN_BRIGHT)
            self.reg_tab_btn.config(bg=BG_COLOR, fg=GREEN_DIM)
            self.nick_label.grid_forget()
            self.nick_entry.grid_forget()
            self.auth_btn.config(text="[ ВОЙТИ ]")
        else:
            self.login_tab_btn.config(bg=BG_COLOR, fg=GREEN_DIM)
            self.reg_tab_btn.config(bg=GREEN_DARK, fg=GREEN_BRIGHT)
            self.nick_label.grid(row=4, column=0, sticky='w', pady=(5, 2))
            self.nick_entry.grid(row=5, column=0, ipady=6, pady=(0, 10))
            self.auth_btn.config(text="[ ЗАРЕГИСТРИРОВАТЬСЯ ]")
    def _build_chat_ui(self):
        self._stop_rain()
        self.auth_frame.destroy()

        top = tk.Frame(self.root, bg=GREEN_DARK, height=44)
        top.pack(fill='x', side='top')
        top.pack_propagate(False)
        tk.Label(top, text=f"MATRIX // {self.user['nickname']} ({self.user['email']})",
                 font=FONT_TITLE, fg=GREEN_BRIGHT, bg=GREEN_DARK).pack(side='left', padx=15)
        tk.Button(top, text="EXIT", font=FONT_BOLD,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  activebackground="#8B0000", activeforeground=GREEN_BRIGHT,
                  relief='flat', bd=0, padx=10, cursor='hand2',
                  command=self.on_close).pack(side='right', padx=5, pady=6)

        main = tk.Frame(self.root, bg=BG_COLOR)
        main.pack(fill='both', expand=True)

        sidebar = tk.Frame(main, bg=BG_COLOR, width=260)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="ДРУЗЬЯ", font=FONT_BOLD,
                 fg=GREEN_BRIGHT, bg=BG_COLOR).pack(pady=(10, 5))

        add_frame = tk.Frame(sidebar, bg=BG_COLOR)
        add_frame.pack(fill='x', padx=6, pady=4)
        self.friend_entry = tk.Entry(add_frame, font=FONT_SMALL, bg=INPUT_BG, fg=INPUT_FG,
                                     insertbackground=CURSOR, relief='flat')
        self.friend_entry.pack(side='left', fill='x', expand=True, ipady=4)
        self.friend_entry.bind('<Return>', lambda e: self.add_friend())
        tk.Button(add_frame, text="+", font=FONT_BOLD,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  activebackground=GREEN, activeforeground=BG_COLOR,
                  relief='flat', bd=0, padx=8, cursor='hand2',
                  command=self.add_friend).pack(side='right', padx=(4, 0))
        tk.Label(sidebar, text="email друга + Enter", font=FONT_SMALL,
                 fg=GREEN_DIM, bg=BG_COLOR).pack()

        self.friends_box = tk.Listbox(sidebar, bg=BG_COLOR, fg=GREEN, font=FONT,
                                      selectbackground=GREEN_DARK, selectforeground=GREEN_BRIGHT,
                                      relief='flat', bd=0, highlightthickness=0, activestyle='none')
        self.friends_box.pack(fill='both', expand=True, padx=6, pady=6)
        self.friends_box.bind('<Double-Button-1>', self._on_friend_double_click)

        tk.Button(sidebar, text="ОБЩИЙ ЧАТ", font=FONT_BOLD,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  activebackground=GREEN, activeforeground=BG_COLOR,
                  relief='flat', bd=0, cursor='hand2',
                  command=self.switch_to_public).pack(fill='x', padx=6, pady=(0, 6))

        tk.Label(sidebar, text="ЗАЯВКИ", font=FONT_BOLD,
                 fg=GOLD, bg=BG_COLOR).pack(pady=(5, 3))
        self.pending_box = tk.Listbox(sidebar, bg=BG_COLOR, fg=GOLD, font=FONT_SMALL,
                                      selectbackground=GREEN_DARK, selectforeground=GOLD,
                                      relief='flat', bd=0, highlightthickness=0, height=6,
                                      activestyle='none')
        self.pending_box.pack(fill='x', padx=6, pady=(0, 4))
        pend_btns = tk.Frame(sidebar, bg=BG_COLOR)
        pend_btns.pack(fill='x', padx=6, pady=(0, 8))
        tk.Button(pend_btns, text="ПРИНЯТЬ", font=FONT_SMALL,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  activebackground=GREEN, activeforeground=BG_COLOR,
                  relief='flat', bd=0, cursor='hand2',
                  command=self.accept_selected).pack(side='left', expand=True, fill='x', padx=(0, 2))
        tk.Button(pend_btns, text="ОТКЛОНИТЬ", font=FONT_SMALL,
                  bg=GREEN_DARK, fg=RED,
                  activebackground=RED, activeforeground=BG_COLOR,
                  relief='flat', bd=0, cursor='hand2',
                  command=self.reject_selected).pack(side='right', expand=True, fill='x', padx=(2, 0))

        tk.Frame(main, bg=GREEN_DARK, width=2).pack(side='left', fill='y')

        chat_frame = tk.Frame(main, bg=BG_COLOR)
        chat_frame.pack(side='left', fill='both', expand=True)
        self.chat_title = tk.Label(chat_frame, text="ОБЩИЙ ЧАТ", font=FONT_BOLD,
                                   fg=GREEN_BRIGHT, bg=GREEN_DARK, anchor='w', padx=10, pady=6)
        self.chat_title.pack(fill='x')

        txt_frame = tk.Frame(chat_frame, bg=BG_COLOR)
        txt_frame.pack(fill='both', expand=True)
        scrollbar = tk.Scrollbar(txt_frame, bg=GREEN_DARK, troughcolor=BG_COLOR,
                                 activebackground=GREEN, relief='flat')
        scrollbar.pack(side='right', fill='y')
        self.chat_area = tk.Text(txt_frame, bg=BG_COLOR, fg=GREEN, font=FONT, wrap='word',
                                 insertbackground=CURSOR, relief='flat', bd=0,
                                 yscrollcommand=scrollbar.set, state='disabled', padx=10, pady=10)
        self.chat_area.pack(fill='both', expand=True)
        scrollbar.config(command=self.chat_area.yview)

        self.chat_area.tag_config("system", foreground=GREEN_DIM, font=("Consolas", 10, "italic"))
        self.chat_area.tag_config("me", foreground=GREEN_BRIGHT, font=FONT_BOLD)
        self.chat_area.tag_config("other", foreground=GREEN)
        self.chat_area.tag_config("nick", foreground=GOLD, font=FONT_BOLD)
        self.chat_area.tag_config("pm_in", foreground=PINK, font=FONT_BOLD)
        self.chat_area.tag_config("pm_out", foreground=ORANGE, font=FONT_BOLD)
        self.chat_area.tag_config("sticker_big", foreground=GREEN_BRIGHT,
                                  font=("Segoe UI Emoji", 24))
        self.chat_area.tag_config("file_link", foreground=LINK_COLOR,
                                  font=("Consolas", 10, "underline"))

        bottom = tk.Frame(chat_frame, bg=BG_COLOR)
        bottom.pack(fill='x', side='bottom', padx=10, pady=10)

        self.recipient_label = tk.Label(bottom, text="-> ВСЕМ", font=FONT_SMALL,
                                        fg=GOLD, bg=BG_COLOR)
        self.recipient_label.pack(anchor='w')

        attach_row = tk.Frame(bottom, bg=BG_COLOR)
        attach_row.pack(fill='x', pady=(0, 4))

        tk.Button(attach_row, text="[ КАРТИНКА ]", font=FONT_SMALL,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  activebackground=GREEN, activeforeground=BG_COLOR,
                  relief='flat', bd=0, padx=8, pady=3, cursor='hand2',
                  command=self.pick_image).pack(side='left', padx=(0, 4))

        tk.Button(attach_row, text="[ ФАЙЛ ]", font=FONT_SMALL,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  activebackground=GREEN, activeforeground=BG_COLOR,
                  relief='flat', bd=0, padx=8, pady=3, cursor='hand2',
                  command=self.pick_file).pack(side='left', padx=(0, 4))

        tk.Button(attach_row, text="[ СТИКЕР ]", font=FONT_SMALL,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  activebackground=GREEN, activeforeground=BG_COLOR,
                  relief='flat', bd=0, padx=8, pady=3, cursor='hand2',
                  command=self.open_sticker_panel).pack(side='left')

        entry_row = tk.Frame(bottom, bg=BG_COLOR)
        entry_row.pack(fill='x')
        tk.Label(entry_row, text=">", font=FONT_BOLD,
                 fg=GREEN_BRIGHT, bg=BG_COLOR).pack(side='left', padx=(0, 5))
        self.msg_entry = tk.Entry(entry_row, font=FONT, bg=INPUT_BG, fg=INPUT_FG,
                                  insertbackground=CURSOR, relief='flat')
        self.msg_entry.pack(side='left', fill='x', expand=True, ipady=6)
        self.msg_entry.bind('<Return>', lambda e: self.send_message())
        self.msg_entry.focus()
        tk.Button(entry_row, text="ОТПРАВИТЬ", font=FONT_BOLD,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  activebackground=GREEN, activeforeground=BG_COLOR,
                  relief='flat', bd=0, padx=15, cursor='hand2',
                  command=self.send_message).pack(side='right', padx=(10, 0))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _start_server_only(self):
        try:
            port = int(self.port_entry.get().strip() or DEFAULT_PORT)
        except ValueError:
            messagebox.showerror("Ошибка", "Порт должен быть числом")
            return
        if self.server is None:
            if self.db is None:
                self.db = UserDB()
            self.server = MatrixServer('0.0.0.0', port, self.db, log_callback=self._server_log)
            try:
                self.server.start()
                self.status_label.config(text=f"[ сервер слушает порт {port} ]", fg=GREEN_BRIGHT)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось запустить сервер:\n{e}")
                self.server = None

    def _start_both(self):
        self._start_server_only()
        self.root.after(400, self._do_auth)

    def _do_auth(self):
        email = self.email_entry.get().strip()
        password = self.pwd_entry.get().strip()
        if not email or not password:
            messagebox.showwarning("Ошибка", "Введите email и пароль")
            return
        try:
            port = int(self.port_entry.get().strip() or DEFAULT_PORT)
            host = self.ip_entry.get().strip() or DEFAULT_HOST
        except ValueError:
            messagebox.showerror("Ошибка", "Порт должен быть числом")
            return
        action = "login" if self.tab.get() == "login" else "register"
        packet = {"action": action, "email": email, "password": password}
        if action == "register":
            nickname = self.nick_entry.get().strip()
            if not nickname:
                messagebox.showwarning("Ошибка", "Введите ник")
                return
            packet["nickname"] = nickname
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((host, port))
            self._send_packet(packet)
            resp = self._recv_packet()
            if not resp:
                messagebox.showerror("Ошибка", "Сервер не отвечает")
                self.sock.close()
                self.sock = None
                return
            if not resp.get("ok"):
                messagebox.showerror("Ошибка", resp.get("msg", "Ошибка"))
                self.sock.close()
                self.sock = None
                return
        except Exception as e:
            messagebox.showerror("Ошибка подключения", f"Не удалось подключиться:\n{e}")
            self.sock = None
            return

        self.user = resp["user"]
        self.connected = True
        self._build_chat_ui()
        self.add_system(f"*** Подключено к {host}:{port} ***")
        self.add_system(f"*** Добро пожаловать, {self.user['nickname']} ***")
        threading.Thread(target=self._receive_loop, daemon=True).start()

    def _send_packet(self, obj):
        with self.send_lock:
            try:
                data = (json.dumps(obj, ensure_ascii=False) + "\n").encode('utf-8')
                self.sock.sendall(data)
            except Exception as e:
                self.add_system(f"*** Ошибка отправки: {e} ***")

    def _recv_packet(self):
        buf = b""
        while True:
            try:
                chunk = self.sock.recv(1)
            except Exception:
                return None
            if not chunk:
                return None
            if chunk == b"\n":
                try:
                    return json.loads(buf.decode('utf-8'))
                except Exception:
                    return None
            buf += chunk

    def _receive_loop(self):
        while self.connected:
            packet = self._recv_packet()
            if not packet:
                break
            self.root.after(0, self._handle_packet, packet)
        if self.connected:
            self.root.after(0, self.add_system, "*** Соединение потеряно ***")
            self.connected = False

    def _handle_packet(self, p):
        t = p.get("type")
        if t == "message":
            if p["email"] == self.user["email"]:
                self.add_message(p["from"], p["text"], tag="me")
            else:
                self.add_message(p["from"], p["text"], tag="other")
        elif t == "pm":
            if p["from_email"] == self.user["email"]:
                self.add_message(f"Я -> {p['to']}", p["text"], tag="pm_out")
            else:
                self.add_message(f"{p['from']} -> мне", p["text"], tag="pm_in")
        elif t == "file":
            is_mine = p["from_email"] == self.user["email"]
            tag = "me" if is_mine else "other"
            header = f"Я -> {p.get('to') or 'всем'}" if is_mine else p["from"]
            self.add_file_message(header, p, tag)
        elif t == "sticker":
            is_mine = p["from_email"] == self.user["email"]
            tag = "me" if is_mine else "other"
            header = f"Я -> {p.get('to') or 'всем'}" if is_mine else p["from"]
            self.add_sticker_message(header, p["sticker"], tag)
        elif t == "system":
            self.add_system(p["text"])
        elif t == "welcome":
            self.user = p["user"]
            self._update_friends(p.get("friends", []), p.get("pending", []))
        elif t == "friends_update":
            self._update_friends(p.get("friends", []), p.get("pending", []))
    def send_message(self):
        text = self.msg_entry.get().strip()
        if not text or not self.connected:
            return
        if self.current_recipient:
            self._send_packet({"type": "pm", "to": self.current_recipient["email"], "text": text})
        else:
            self._send_packet({"type": "message", "text": text})
        self.msg_entry.delete(0, 'end')

    def add_friend(self):
        email = self.friend_entry.get().strip()
        if not email or not self.connected:
            return
        self._send_packet({"type": "add_friend", "email": email})
        self.friend_entry.delete(0, 'end')

    def accept_selected(self):
        sel = self.pending_box.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.pending):
            fid = self.pending[idx]["fid"]
            self._send_packet({"type": "accept_friend", "fid": fid})

    def reject_selected(self):
        sel = self.pending_box.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.pending):
            fid = self.pending[idx]["fid"]
            self._send_packet({"type": "reject_friend", "fid": fid})

    def _on_friend_double_click(self, event):
        sel = self.friends_box.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self.friends):
            return
        friend = self.friends[idx]
        self.current_recipient = friend
        self.chat_title.config(text=f"ЛИЧНЫЙ ЧАТ с {friend['nickname']}")
        self.recipient_label.config(text=f"-> {friend['nickname']} ({friend['email']})")
        self.add_system(f"*** Открыт личный чат с {friend['nickname']} ***")

    def switch_to_public(self):
        self.current_recipient = None
        self.chat_title.config(text="ОБЩИЙ ЧАТ")
        self.recipient_label.config(text="-> ВСЕМ")
        self.add_system("*** Вернулись в общий чат ***")

    def _update_friends(self, friends, pending):
        self.friends = friends
        self.pending = pending
        self.friends_box.delete(0, 'end')
        for f in friends:
            self.friends_box.insert('end', f"{f['nickname']}  <{f['email']}>")
        self.pending_box.delete(0, 'end')
        for p in pending:
            self.pending_box.insert('end', f"{p['nickname']}  <{p['email']}>")

    def add_system(self, text):
        self.chat_area.config(state='normal')
        self.chat_area.insert('end', text + "\n", "system")
        self.chat_area.see('end')
        self.chat_area.config(state='disabled')

    def add_message(self, nick, text, tag="other"):
        self.chat_area.config(state='normal')
        self.chat_area.insert('end', f"{nick}: ", "nick")
        self.chat_area.insert('end', f"{text}\n", tag)
        self.chat_area.see('end')
        self.chat_area.config(state='disabled')

    def _human_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def add_file_message(self, header, p, tag):
        self.chat_area.config(state='normal')
        self.chat_area.insert('end', f"{header}: ", "nick")
        name = p.get("name", "file")
        mime = p.get("mime", "")
        data_b64 = p.get("data", "")
        is_image = mime.startswith("image/") and PIL_AVAILABLE
        if is_image:
            try:
                img_bytes = base64.b64decode(data_b64)
                img = Image.open(BytesIO(img_bytes))
                img.thumbnail((220, 220), Image.LANCZOS)
                tkimg = ImageTk.PhotoImage(img)
                self._image_refs.append(tkimg)
                btn = tk.Button(self.chat_area, image=tkimg, bg=BG_COLOR,
                                activebackground=BG_COLOR, relief='flat', bd=0,
                                cursor='hand2',
                                command=lambda d=data_b64, n=name, m=mime: self._open_image(d, n, m))
                self.chat_area.window_create('end', window=btn)
                self.chat_area.insert('end', f"  {name}\n", tag)
            except Exception as e:
                self.chat_area.insert('end', f"[картинка: {name}, ошибка: {e}]\n", tag)
        else:
            size_str = self._human_size(p.get("size", 0))
            self.chat_area.insert('end', f"[ФАЙЛ] {name} ({size_str})\n", "file_link")
            self.chat_area.tag_bind("file_link", "<Button-1>",
                                    lambda e, d=data_b64, n=name, m=mime: self._save_file(d, n, m))
        self.chat_area.see('end')
        self.chat_area.config(state='disabled')

    def add_sticker_message(self, header, sticker, tag):
        self.chat_area.config(state='normal')
        self.chat_area.insert('end', f"{header}: ", "nick")
        if len(sticker) <= 4:
            self.chat_area.insert('end', f" {sticker} ", "sticker_big")
            self.chat_area.insert('end', "\n", tag)
        else:
            self.chat_area.insert('end', f" [стикер]\n", tag)
        self.chat_area.see('end')
        self.chat_area.config(state='disabled')

    def _open_image(self, data_b64, name, mime):
        try:
            os.makedirs(DOWNLOADS_DIR, exist_ok=True)
            path = os.path.join(DOWNLOADS_DIR, f"{int(time.time())}_{name}")
            with open(path, "wb") as f:
                f.write(base64.b64decode(data_b64))
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть картинку: {e}")

    def _save_file(self, data_b64, name, mime):
        try:
            os.makedirs(DOWNLOADS_DIR, exist_ok=True)
            path = os.path.join(DOWNLOADS_DIR, name)
            if os.path.exists(path):
                base, ext = os.path.splitext(name)
                path = os.path.join(DOWNLOADS_DIR, f"{base}_{int(time.time())}{ext}")
            with open(path, "wb") as f:
                f.write(base64.b64decode(data_b64))
            messagebox.showinfo("Файл сохранён", f"Сохранено в:\n{path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить файл: {e}")

    def pick_image(self):
        path = filedialog.askopenfilename(
            title="Выбери картинку",
            filetypes=[("Картинки", "*.png *.jpg *.jpeg *.gif *.bmp"),
                       ("Все файлы", "*.*")]
        )
        if path:
            self._send_file(path, is_image=True)

    def pick_file(self):
        path = filedialog.askopenfilename(title="Выбери файл")
        if path:
            self._send_file(path, is_image=False)

    def _send_file(self, path, is_image=False):
        if not self.connected:
            return
        try:
            size = os.path.getsize(path)
            if size > MAX_FILE_SIZE:
                messagebox.showerror("Ошибка", f"Файл больше {MAX_FILE_SIZE // (1024*1024)} MB")
                return
            with open(path, "rb") as f:
                data = f.read()
            b64 = base64.b64encode(data).decode('ascii')
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1].lower()
            mime_map = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
                ".pdf": "application/pdf", ".txt": "text/plain",
                ".zip": "application/zip", ".mp3": "audio/mpeg",
                ".mp4": "video/mp4",
            }
            mime = mime_map.get(ext, "application/octet-stream")
            packet = {
                "type": "file",
                "name": name,
                "data": b64,
                "size": size,
                "mime": mime,
                "to": self.current_recipient["email"] if self.current_recipient else ""
            }
            self._send_packet(packet)
            self.add_system(f"*** Отправляю {name} ({self._human_size(size)}) ***")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось отправить файл:\n{e}")

    def open_sticker_panel(self):
        win = tk.Toplevel(self.root)
        win.title("Стикеры")
        win.configure(bg=BG_COLOR)
        win.geometry("360x280")
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text="Выбери стикер", font=FONT_BOLD,
                 fg=GREEN_BRIGHT, bg=BG_COLOR).pack(pady=8)
        grid = tk.Frame(win, bg=BG_COLOR)
        grid.pack(padx=10, pady=10)
        keys = list(STICKERS.keys())
        for i, key in enumerate(keys):
            emoji = STICKERS[key]
            btn = tk.Button(grid, text=emoji, font=("Segoe UI Emoji", 20),
                            bg=GREEN_DARK, fg=GREEN_BRIGHT,
                            activebackground=GREEN, activeforeground=BG_COLOR,
                            relief='flat', bd=0, width=3, height=1, cursor='hand2',
                            command=lambda e=emoji: self._send_sticker(e, win))
            btn.grid(row=i // 4, column=i % 4, padx=4, pady=4)
        tk.Button(win, text="Закрыть", font=FONT_SMALL,
                  bg=GREEN_DARK, fg=GREEN_BRIGHT,
                  relief='flat', bd=0, padx=10, pady=4, cursor='hand2',
                  command=win.destroy).pack(pady=10)

    def _send_sticker(self, sticker, window):
        if not self.connected:
            window.destroy()
            return
        packet = {
            "type": "sticker",
            "sticker": sticker,
            "to": self.current_recipient["email"] if self.current_recipient else ""
        }
        self._send_packet(packet)
        window.destroy()

    def _server_log(self, msg):
        print(f"[SERVER] {msg}")

    def _start_rain(self):
        self.rain_active = True
        self.rain_items = []
        chars = "01アイウエオカキクケコサシスセソABCDEF"
        for x in range(10, 1000, 20):
            item = self.rain_canvas.create_text(
                x, random.randint(-200, 0),
                text=random.choice(chars),
                font=("Consolas", 10), fill=GREEN_DIM, tags="rain"
            )
            self.rain_items.append({
                'id': item, 'x': x,
                'y': random.randint(-200, 0),
                'speed': random.uniform(1.5, 4.0)
            })
        self.rain_canvas.tag_raise("title")
        self.rain_canvas.tag_raise("subtitle")
        self._rain_step()

    def _rain_step(self):
        if not self.rain_active or not self.rain_canvas:
            return
        chars = "01アイウエオカキクケコサシスセソABCDEF"
        try:
            for drop in self.rain_items:
                drop['y'] += drop['speed']
                if drop['y'] > 160:
                    drop['y'] = random.randint(-100, -10)
                    drop['x'] = random.randint(10, 990)
                self.rain_canvas.coords(drop['id'], drop['x'], drop['y'])
                if random.random() < 0.05:
                    self.rain_canvas.itemconfig(drop['id'], text=random.choice(chars))
            self.rain_canvas.tag_raise("title")
            self.rain_canvas.tag_raise("subtitle")
        except tk.TclError:
            return
        self.root.after(50, self._rain_step)

    def _stop_rain(self):
        self.rain_active = False

    def _animate_title(self):
        chars = "01アイウエオカキクケコ"
        try:
            self.root.title("MATRIX " + "".join(random.choice(chars) for _ in range(6)))
        except tk.TclError:
            return
        self.root.after(400, self._animate_title)

    def on_close(self):
        self.connected = False
        self.rain_active = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        if self.server:
            self.server.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    os.makedirs(STICKERS_DIR, exist_ok=True)
    print("=" * 50)
    print("   MATRIX MESSENGER v3.0")
    print("=" * 50)
    app = MatrixMessenger()
    app.run()