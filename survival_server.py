# -*- coding: utf-8 -*-
"""
survival_server.py — 生存代码 联机服务器
==========================================
纯标准库（适配 64M 内存小机器），TCP + 行级 JSON 协议。

职责：
  1. 账号系统：注册 / 登录（sha256+盐 哈希存储，token 会话）
  2. 房间系统：创建房间 / 加入房间，配对成功后转为纯转发中继
  3. 游戏中继：host 与 guest 之间的消息互相转发（host 权威模拟）

协议（每条消息一行 JSON）：
  -> {"a":"register","u":"用户名","p":"密码"}          -> {"ok":true} / {"ok":false,"e":"..."}
  -> {"a":"login","u":"...","p":"..."}                 -> {"ok":true,"t":"token"}
  -> {"a":"create","t":"token"}                        -> {"ok":true,"room":"xxxx"}
  -> {"a":"join","t":"token","room":"xxxx"}            -> {"ok":true} / {"ok":false,"e":"..."}
  -> {"a":"rooms","t":"token"}                         -> {"ok":true,"rooms":["ab12",...]}
  -> 配对完成后双方任意消息被原样转发给对方（含 {"a":"state",...}/{"a":"input",...}）
  -> {"a":"quit"}                                      断开

端口：8765
"""
import json
import os
import socket
import socketserver
import hashlib
import secrets
import string
import threading
import time

PORT = 8765
BASE = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE, "accounts.json")
LOCK = threading.Lock()          # accounts.json 读写锁
ROOM_LOCK = threading.Lock()     # 房间表锁

accounts = {}                    # user -> {salt, hash, token}
rooms = {}                       # room_id -> {host, guest, created, host_user, guest_user}
conn_tokens = {}                 # conn -> token（用于断开清理）

# ── 持久化 ──
def load_accounts():
    global accounts
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            accounts = json.load(f)
    except Exception:
        accounts = {}

def dump_accounts():
    tmp = ACCOUNTS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(accounts, f)
        os.replace(tmp, ACCOUNTS_FILE)
    except Exception as e:
        print("[warn] save accounts failed:", e)

# ── 工具 ──
def hash_pw(salt, pw):
    return hashlib.sha256((salt + pw).encode("utf-8")).hexdigest()

def gen_id(n=6):
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))

def valid_user(u):
    return isinstance(u, str) and 2 <= len(u) <= 20 and all(
        c.isalnum() or c in "_-" for c in u)

def valid_pw(p):
    return isinstance(p, str) and 4 <= len(p) <= 64

class Handler(socketserver.BaseRequestHandler):
    def setup(self):
        self.buf = b""
        self.user = None
        self.room = None       # 所在房间 id
        self.paired = False    # 配对完成进入中继模式
        self.alive = True

    # ---- 收发 ----
    def send(self, obj):
        try:
            data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
            self.request.sendall(data)
            return True
        except Exception:
            return False

    def readline(self):
        while b"\n" not in self.buf:
            if len(self.buf) > 2 * 1024 * 1024:
                return None     # 防超大包
            try:
                chunk = self.request.recv(8192)
            except Exception:
                return None
            if not chunk:
                return None
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line

    # ---- 中继 ----
    def peer(self):
        """返回同房间的另一条连接"""
        with ROOM_LOCK:
            r = rooms.get(self.room)
        if not r:
            return None
        other = r.get("guest") if r.get("host") is self else r.get("host")
        return other if other is not self else None

    # ---- 主处理 ----
    def handle(self):
        try:
            self.request.settimeout(300)   # 5 分钟无数据踢掉
        except Exception:
            pass
        while self.alive:
            line = self.readline()
            if line is None:
                break
            # 配对后：纯中继
            if self.paired:
                peer = self.peer()
                if peer is not None and peer.alive:
                    try:
                        peer.request.sendall(line + b"\n")
                    except Exception:
                        pass
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except Exception:
                self.send({"ok": False, "e": "bad json"})
                continue
            if not self.dispatch(msg):
                break
        self.cleanup()

    def dispatch(self, msg):
        a = msg.get("a")
        if a == "ping":
            # 心跳：回 pong，同时证明连接活着（防 NAT/网关掐空闲）
            self.send({"a": "pong"})
            return True
        if a == "register":
            return self.do_register(msg)
        if a == "login":
            return self.do_login(msg)
        if a == "create":
            return self.do_create(msg)
        if a == "join":
            return self.do_join(msg)
        if a == "rooms":
            return self.do_rooms(msg)
        if a == "quit":
            return False
        self.send({"ok": False, "e": "unknown action"})
        return True

    # ---- 账号 ----
    def do_register(self, msg):
        u, p = msg.get("u", ""), msg.get("p", "")
        if not valid_user(u):
            self.send({"ok": False, "e": "用户名需 2-20 位字母数字或 _-"})
            return True
        if not valid_pw(p):
            self.send({"ok": False, "e": "密码需 4-64 位"})
            return True
        with LOCK:
            if u in accounts:
                self.send({"ok": False, "e": "用户名已存在"})
                return True
            salt = gen_id(8)
            accounts[u] = {"salt": salt, "hash": hash_pw(salt, p),
                           "created": int(time.time())}
            dump_accounts()
        self.send({"ok": True, "msg": "注册成功，请登录"})
        return True

    def do_login(self, msg):
        u, p = msg.get("u", ""), msg.get("p", "")
        with LOCK:
            rec = accounts.get(u)
            if not rec:
                self.send({"ok": False, "e": "用户不存在"})
                return True
            if hash_pw(rec["salt"], p) != rec["hash"]:
                self.send({"ok": False, "e": "密码错误"})
                return True
            token = gen_id(24)
            rec["token"] = token
            dump_accounts()
        self.user = u
        conn_tokens[self] = token
        self.send({"ok": True, "t": token})
        return True

    def auth(self, msg):
        """校验 token，返回是否有效"""
        t = msg.get("t", "")
        with LOCK:
            for u, rec in accounts.items():
                if rec.get("token") == t and t:
                    self.user = u
                    conn_tokens[self] = t
                    return True
        return False

    # ---- 房间 ----
    def do_create(self, msg):
        if not self.auth(msg):
            self.send({"ok": False, "e": "请先登录"})
            return True
        rid = gen_id(6)
        with ROOM_LOCK:
            while rid in rooms:
                rid = gen_id(6)
            rooms[rid] = {"host": self, "guest": None, "created": time.time(),
                          "host_user": self.user, "guest_user": None}
        self.room = rid
        self.send({"ok": True, "room": rid})
        return True

    def do_join(self, msg):
        if not self.auth(msg):
            self.send({"ok": False, "e": "请先登录"})
            return True
        rid = msg.get("room", "").strip().lower()
        with ROOM_LOCK:
            r = rooms.get(rid)
            if not r:
                self.send({"ok": False, "e": "房间不存在"})
                return True
            if r.get("guest") is not None:
                self.send({"ok": False, "e": "房间已满"})
                return True
            if r.get("host") is self:
                self.send({"ok": False, "e": "不能加入自己的房间"})
                return True
            r["guest"] = self
            r["guest_user"] = self.user
            host = r["host"]
        self.room = rid
        # 双方进入中继模式
        self.paired = True
        host.paired = True
        ok1 = self.send({"ok": True, "room": rid, "paired": True,
                         "you": "guest", "mate": host.user})
        ok2 = host.send({"ok": True, "room": rid, "paired": True,
                         "you": "host", "mate": self.user})
        if not (ok1 and ok2):
            return False
        return True

    def do_rooms(self, msg):
        if not self.auth(msg):
            self.send({"ok": False, "e": "请先登录"})
            return True
        now = time.time()
        with ROOM_LOCK:
            # 清理超过 10 分钟没人加入的等待房
            for k in [k for k, r in rooms.items()
                      if r["guest"] is None and now - r["created"] > 600]:
                rooms.pop(k, None)
            open_rooms = [{"room": k, "host": r["host_user"]}
                          for k, r in rooms.items() if r["guest"] is None]
        self.send({"ok": True, "rooms": open_rooms})
        return True

    # ---- 断开清理 ----
    def cleanup(self):
        self.alive = False
        conn_tokens.pop(self, None)
        if self.room:
            with ROOM_LOCK:
                r = rooms.get(self.room)
                if r:
                    peer = r.get("guest") if r.get("host") is self else r.get("host")
                    rooms.pop(self.room, None)
                    if peer is not None and peer.alive:
                        try:
                            peer.send({"a": "peer_left"})
                            peer.paired = False
                            peer.room = None
                        except Exception:
                            pass

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

def main():
    load_accounts()
    print(f"[生存代码服务器] 端口 {PORT}，已加载 {len(accounts)} 个账号")
    with Server(("0.0.0.0", PORT), Handler) as srv:
        srv.serve_forever()

if __name__ == "__main__":
    main()
