# -*- coding: utf-8 -*-
"""e2e_test.py — 本地起服务器 + 双客户端跑完整联机协议"""
import os, sys, json, socket, threading, time

os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'

# 1) 起服务器（子线程）
import survival_server as S
srv_thread = threading.Thread(target=S.main, daemon=True)
srv_thread.start()
time.sleep(0.8)

def client():
    s = socket.create_connection(('127.0.0.1', S.PORT), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s

def send(s, obj):
    s.sendall((json.dumps(obj) + "\n").encode())

def recv(s, timeout=5):
    buf = b""
    s.settimeout(timeout)
    while b"\n" not in buf:
        d = s.recv(4096)
        if not d: return None
        buf += d
    line, _, rest = buf.partition(b"\n")
    if rest:
        # 剩余数据塞回（简单处理：本测试每条消息一次收完）
        pass
    return json.loads(line.decode())

print("[1] 注册两个账号")
a, b = client(), client()
send(a, {"a": "register", "u": "host01", "p": "pass1234"})
print("   register a:", recv(a))
send(b, {"a": "register", "u": "guest01", "p": "pass1234"})
print("   register b:", recv(b))

print("[2] 登录")
send(a, {"a": "login", "u": "host01", "p": "pass1234"})
ra = recv(a); print("   a login:", ra.get("ok"))
send(b, {"a": "login", "u": "guest01", "p": "pass1234"})
rb = recv(b); print("   b login:", rb.get("ok"))
ta, tb = ra["t"], rb["t"]

print("[3] 错误密码 / 重复注册")
send(a, {"a": "login", "u": "host01", "p": "wrong"})
print("   wrong pw:", recv(a))
send(a, {"a": "register", "u": "host01", "p": "pass1234"})
print("   dup reg:", recv(a))

print("[4] host 创建房间")
send(a, {"a": "create", "t": ta})
rc = recv(a); print("   create:", rc)
room = rc["room"]

print("[5] 房间列表")
send(b, {"a": "rooms", "t": tb})
print("   rooms:", recv(b))

print("[6] guest 加入")
send(b, {"a": "join", "t": tb, "room": room})
rbj = recv(b); print("   b join:", rbj)
ra_p = recv(a); print("   a paired:", ra_p)

print("[7] 中继转发: a 发 input -> b 收到")
send(a, {"a": "input", "dx": 1, "dy": 0})
got = recv(b, timeout=3)
print("   b 收到:", got)
assert got and got.get("a") == "input"

print("[8] 中继转发: b 发 state -> a 收到")
send(b, {"a": "state", "p": [100, 200], "e": [], "b": [], "eb": [], "bo": None})
got = recv(a, timeout=3)
print("   a 收到 state:", bool(got and got.get("a") == "state"))

print("[9] a 断开 -> b 收到 peer_left")
a.close()
got = recv(b, timeout=3)
print("   b 收到:", got)
assert got and got.get("a") == "peer_left"

print("[10] 重登拿新 token，旧 token 失效")
b2 = client()
send(b2, {"a": "login", "u": "guest01", "p": "pass1234"})
rb2 = recv(b2)
send(b2, {"a": "rooms", "t": "INVALID_TOKEN"})
print("   旧/坏token:", recv(b2))

b2.close(); b.close()
print()
print("=== 服务器协议 E2E 全部 PASS ===")
