# -*- coding: utf-8 -*-
"""
生存代码 - Pygame 完整版
全部功能：9武器/22组合/天赋树/5关卡Boss/双人同屏/音效/排行榜/倍速
"""
import pygame
import math
import random
import json
import os
import sys
import socket
import threading
import queue
import time as _time

# ============ 联机配置 ============
# 容器云走 NAT 转发：外网 18765 -> 容器内 8765（TCP）
SERVER_HOST = "167.104.99.187"
SERVER_PORT = 18765
# 容器 SSH（面板登录信息）：NAT 没配时客户端自动打 SSH 隧道，玩家零操作
SSH_PORT = 63400
SSH_USER = "root"
SSH_PASS = "syc030924A"
LOCAL_TUNNEL_PORT = 18700
SERVER_CANDIDATES = [(SERVER_HOST, SERVER_PORT), ("127.0.0.1", SERVER_PORT)]

# ── 自动 SSH 隧道（NAT 未配置时的兜底）──
_tunnel_ready = [False]
_tunnel_tr = [None]
_tunnel_listener = [None]
def start_auto_tunnel():
    """本地开 127.0.0.1:LOCAL_TUNNEL_PORT，经 SSH(63400) 转发到容器内 127.0.0.1:8765。
    需要 paramiko；失败返回 False，游戏会给出明确提示。"""
    if _tunnel_ready[0]:
        tr_old = _tunnel_tr[0]
        if tr_old is not None and tr_old.is_active():
            return True
        # 旧隧道已被云网关掐断：拆掉重建
        _tunnel_ready[0] = False
        for x in (_tunnel_listener[0], _tunnel_tr[0]):
            try:
                if x: x.close()
            except Exception:
                pass
        _tunnel_tr[0] = None
        _tunnel_listener[0] = None
    try:
        import paramiko
    except ImportError:
        return False
    try:
        tr = paramiko.Transport((SERVER_HOST, SSH_PORT))
        tr.connect(username=SSH_USER, password=SSH_PASS)
        # 关键：SSH 层 30 秒 keepalive，防容器云网关掐空闲连接
        tr.set_keepalive(30)
    except Exception:
        return False

    def _serve_one(sock):
        try:
            chan = tr.open_channel("direct-tcpip", ("127.0.0.1", 8765),
                                   ("127.0.0.1", 0))
        except Exception:
            try: sock.close()
            except Exception: pass
            return
        if chan is None:
            try: sock.close()
            except Exception: pass
            return
        import select as _sel
        try:
            while True:
                r, _, _ = _sel.select([sock, chan], [], [], 60)
                if sock in r:
                    data = sock.recv(8192)
                    if not data: break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(8192)
                    if not data: break
                    sock.sendall(data)
        except Exception:
            pass
        finally:
            for x in (chan, sock):
                try: x.close()
                except Exception: pass

    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", LOCAL_TUNNEL_PORT))
        listener.listen(8)
    except Exception:
        try: tr.close()
        except Exception: pass
        return False

    def _loop():
        while True:
            try:
                cs, _ = listener.accept()
            except Exception:
                break
            threading.Thread(target=_serve_one, args=(cs,), daemon=True).start()
    threading.Thread(target=_loop, daemon=True).start()
    _tunnel_tr[0] = tr
    _tunnel_listener[0] = listener
    _tunnel_ready[0] = True
    return True

pygame.init()
try:
    pygame.mixer.init()
except pygame.error:
    pass  # 无声卡/设备被占用的机器上游戏仍可运行

WIDTH, HEIGHT = 1280, 800
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("生存代码")
# 确保窗口获得焦点
pygame.event.pump()
clock = pygame.time.Clock()
FPS = 60

# 颜色
BLACK = (5, 6, 15)
CYAN = (0, 255, 204)
RED = (255, 56, 96)
YELLOW = (255, 224, 102)
PURPLE = (123, 97, 255)
GREEN = (56, 255, 96)
WHITE = (255, 255, 255)
PINK = (255, 159, 243)
ORANGE = (255, 107, 53)
BLUE = (125, 211, 252)

# ============ 音效 ============
class DummySound:
    def play(self): pass
sfx_shoot = DummySound()
sfx_hit = DummySound()
sfx_explode = DummySound()
sfx_hurt = DummySound()
sfx_levelup = DummySound()

# ============ 联机客户端 ============
class NetClient:
    """后台线程收包 + 队列，主循环非阻塞取消息。
    配对成功后：send_game() 发的消息会被服务器转给对面。"""
    def __init__(self):
        self.sock = None
        self.inbox = queue.Queue()
        self.connected = False
        self.paired = False
        self.token = None
        self.user = None
        self.room = None
        self.mate = None
        self.is_host = False
        self._thread = None
        self._stop = False

    def connect(self, host, port):
        # 依次尝试候选地址：公网直连 -> 本地隧道（tunnel.py）
        targets = [(host, port)]
        for c in SERVER_CANDIDATES:
            if c not in targets:
                targets.append(c)
        last_err = None
        for h, p in targets:
            try:
                s = socket.create_connection((h, p), timeout=5)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.sock = s
                self.connected = True
                self._stop = False
                self._thread = threading.Thread(target=self._reader, daemon=True)
                self._thread.start()
                return True
            except Exception as e:
                last_err = e
        # 公网和现成隧道都不通：自动打一条 SSH 隧道再试一次
        if start_auto_tunnel():
            try:
                s = socket.create_connection(("127.0.0.1", LOCAL_TUNNEL_PORT), timeout=5)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.sock = s
                self.connected = True
                self._stop = False
                self._thread = threading.Thread(target=self._reader, daemon=True)
                self._thread.start()
                return True
            except Exception as e:
                last_err = e
        self.inbox.put({"ok": False, "e": f"无法连接服务器: {last_err}"})
        return False

    def _reader(self):
        buf = b""
        while not self._stop:
            try:
                data = self.sock.recv(8192)
            except Exception:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                try:
                    self.inbox.put(json.loads(line.decode("utf-8")))
                except Exception:
                    pass
        # 断线事件
        self.connected = False
        self.paired = False
        self.inbox.put({"a": "disconnected"})

    def send_raw(self, obj):
        if not self.sock or not self.connected:
            return False
        try:
            self.sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            return True
        except Exception:
            return False

    def poll(self):
        """取出一条待处理消息，没有返回 None"""
        try:
            return self.inbox.get_nowait()
        except queue.Empty:
            return None

    def heartbeat(self):
        """每帧调用：每 60 秒向服务器发一个 ping，
        防服务器 300s 空闲踢线、也防云网关掐空闲隧道。
        配对中继模式下不发（会原样转发给对面，对面不认识 ping）。"""
        if not self.connected or self.paired or not self.token:
            return
        now = _time.time()
        if now - getattr(self, "_last_ping", 0) >= 60:
            self._last_ping = now
            self.send_raw({"a": "ping", "t": self.token})

    def close(self):
        self._stop = True
        try:
            if self.sock: self.sock.close()
        except Exception:
            pass
        self.sock = None
        self.connected = False
        self.paired = False

net = NetClient()
DT_REF = 1.0 / FPS   # guest 输入在 host 端按固定步长应用（消息间隔≈帧长）

# ============ 记住密码（本地保存，base64 混淆） ============
import base64
REMEMBER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'login_remember.json')

def load_remember():
    global auth_user
    try:
        with open(REMEMBER_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
        if d.get('r'):
            auth_user = d.get('u', '')
            login_pw[0] = base64.b64decode(d.get('p', '')).decode('utf-8', 'replace')
            remember_me[0] = True
    except Exception:
        pass

def save_remember():
    try:
        if remember_me[0] and auth_user:
            d = {'r': True, 'u': auth_user,
                 'p': base64.b64encode(login_pw[0].encode('utf-8')).decode()}
            with open(REMEMBER_FILE, 'w', encoding='utf-8') as f:
                json.dump(d, f)
        else:
            if os.path.exists(REMEMBER_FILE):
                os.remove(REMEMBER_FILE)
    except Exception:
        pass

# 登录页输入状态
login_pw = ['']          # 密码输入缓冲
login_field = ['user']   # 当前输入焦点: user / pw
remember_me = [False]    # 记住账号密码勾选
REMEMBER_RECT = pygame.Rect(WIDTH//2-140, 392, 280, 30)   # 勾选框命中区（渲染每帧刷新）
LOGIN_BTN = pygame.Rect(WIDTH//2-140, 432, 280, 46)
REG_BTN = pygame.Rect(WIDTH//2-140, 492, 280, 46)
QUIT_BTN = pygame.Rect(WIDTH//2-140, 562, 280, 40)
auth_user = ''
load_remember()

def do_login():
    global lobby_status, login_msg, login_msg_color
    u, p = auth_user, ''.join(login_pw)
    if not u or not p:
        login_msg = '请输入用户名和密码'
        login_msg_color = ORANGE
        return
    if not net.connected:
        if not net.connect(SERVER_HOST, SERVER_PORT):
            return
    if not net.send_raw({"a": "login", "u": u, "p": p}):
        login_msg = '发送失败，请重试'
        login_msg_color = RED
        return
    login_msg = '登录中...'
    login_msg_color = CYAN

def _reconnect_async():
    """断线后在后台线程重连 + 重登（凭据用记住的那份）"""
    global login_msg, login_msg_color
    try:
        # 先清掉旧连接
        try:
            if net.sock: net.sock.close()
        except Exception:
            pass
        net.sock = None
        net.connected = False
        net.paired = False
        if not net.connect(SERVER_HOST, SERVER_PORT):
            return
        u, p = auth_user, login_pw[0]
        if u and p:
            net.send_raw({"a": "login", "u": u, "p": p})
    except Exception:
        pass

def do_register():
    global login_msg, login_msg_color
    u, p = auth_user, ''.join(login_pw)
    if not u or not p:
        login_msg = '请输入用户名和密码'
        login_msg_color = ORANGE
        return
    if not net.connected:
        if not net.connect(SERVER_HOST, SERVER_PORT):
            return
    # 注册成功后自动勾选记住并保存，下次打开直接填好
    remember_me[0] = True
    save_remember()
    net.send_raw({"a": "register", "u": u, "p": p})
    login_msg = '注册中...'
    login_msg_color = CYAN

def pump_net():
    """每帧处理网络消息队列"""
    global state, login_msg, login_msg_color, lobby_status, lobby_rooms
    global online_mode, net_role, create_room_id, join_room_input
    global peer_pos, peer_hp, peer_level, peer_kills, peer_weapons, peer_input
    global player2, level_clear, cards
    while True:
        m = net.poll()
        if m is None:
            break
        a = m.get("a")
        if a == "pong":
            continue        # 心跳回包，忽略
        if a == "disconnected":
            # BUG修复：原来条件带 online_mode，大厅配对前 online_mode=False，
            # 断线事件被静默吞掉 -> 用户点创建房间往死连接里写，界面永远卡"创建中"
            if state == 'lobby':
                lobby_status = '与服务器断开，正在自动重连...'
                create_room_id = ''
                # 立刻尝试重连+重登
                threading.Thread(target=_reconnect_async, daemon=True).start()
            elif state == 'playing' and online_mode:
                lobby_status = '与服务器断开'
                state = 'menu'
                online_mode = False
                player2 = None
            else:
                login_msg = '已断开服务器'
                login_msg_color = RED
            continue
        if "ok" in m and state == 'login':
            if m.get("ok"):
                if m.get("t"):        # 登录成功
                    net.token = m["t"]
                    net.user = auth_user
                    login_msg = '登录成功'
                    login_msg_color = GREEN
                    state = 'menu'
                else:                 # 注册成功
                    login_msg = m.get("msg", "注册成功，请登录")
                    login_msg_color = GREEN
            else:
                login_msg = m.get("e", "失败")
                login_msg_color = RED
            continue
        # 大厅里的自动重连登录回包（带 t 且无 room 字段才是登录结果；
        # 不能笼统用 "ok" in m，否则 create/join/rooms 回包会被吞掉）
        if state == 'lobby' and m.get("ok") and m.get("t") and not m.get("room"):
            net.token = m["t"]
            net.user = auth_user
            lobby_status = '重连成功，可以继续创建/加入房间'
            continue
        if state == 'lobby':
            if m.get("ok") and m.get("room") and not m.get("paired"):
                create_room_id = m["room"]
                net.room = m["room"]
                net_role = 'host'
                lobby_status = f'房间号 {m["room"]}，等待队友加入...'
            elif m.get("ok") and m.get("paired"):
                net.paired = True
                net.room = m.get("room")
                net_role = m.get("you", "guest")
                net.mate = m.get("mate")
                online_mode = True
                lobby_status = f'已连接到 {net.mate}！房主点关卡开始'
                state = 'level'
            elif m.get("ok") and "rooms" in m:
                lobby_rooms = m["rooms"]
            elif not m.get("ok"):
                lobby_status = m.get("e", "失败")
            continue
        # ---------- 游戏中继消息 ----------
        # 开局广播：guest 此时还停在选关页，必须在 level 状态也能处理
        if a == "start" and online_mode and net_role == 'guest' and state in ('level', 'playing'):
            reset_game(m.get("level", 0), coop=True)
            online_mode = True
            state = 'playing'
            continue
        if state == 'playing' and online_mode:
            if a == "input" and net_role == 'host':
                # 只记录，移动在 host 主循环按 sdt 权威应用
                peer_input = [m.get("dx", 0), m.get("dy", 0)]
            elif a == "levelup_offer" and net_role == 'guest':
                cards = [tuple(c) for c in m.get("cards", [])]
                state = 'levelup'
            elif a == "pick" and net_role == 'host':
                card = m.get("card")
                if card and len(card) == 4 and player2:
                    # 服务器/网络可能把内层颜色列表转成 JSON，统一转回元组
                    c = (card[0], card[1], card[2], tuple(card[3]) if isinstance(card[3], list) else card[3])
                    apply_card_to(player2, c)
                    check_p2_levelups()   # 可能连升，继续发 offer
            elif a == "state" and net_role == 'guest':
                p1pos = m.get("p")
                p2pos = m.get("p2")
                if p2pos:
                    player.x, player.y = p2pos[0], p2pos[1]   # 我的权威位置
                if player2 is None:
                    player2 = Player(is_p2=True)
                if p1pos:
                    player2.x, player2.y = p1pos[0], p1pos[1]
                    player2.alive = True
                pm = m.get("p2m")
                if pm:
                    player.hp, player.max_hp = pm[0], pm[1]
                    player.level, player.kills = pm[2], pm[3]
                pw = m.get("p2w")
                if pw:
                    player.weapons = {k: v for k, v in pw.items()}
                # 增量事件驱动：不再全量重建世界，改传增量事件列表
                _apply_net_world(m.get("evs", []), m.get("bo"), m.get("corr"))
            elif a == "end" and net_role == 'guest':
                online_mode = False
                state = 'gameover'
                level_clear = bool(m.get("win"))
                earned = m.get("earned", 0)
                if earned:
                    save['shards'] += earned
                    save_data()
            elif a == "peer_left":
                lobby_status = '队友离开'
                online_mode = False
                state = 'menu'
                player2 = None

def _apply_net_world(evs, bo, corr=None):
    """guest 端：增量应用事件 + 位置校正（事件驱动+本地视觉推测）

    改造点：
    1) 不再全量重建 enemies/bullets——收到 spawn 事件时本地生成一个敌人
    2) 收到 bullet 事件时本地射出一颗子弹（自己控制飞行）
    3) 收到 kill 事件时本地播爆炸、掉粒子
    4) corr 是 host 每 0.5 秒发来的敌人位置校正，消除本地推算漂移
    """
    global enemies, bullets, enemy_bullets, boss
    for ev in evs:
        t = ev[0]
        if t == 0:      # spawn: 生成敌人
            e = Enemy.__new__(Enemy)
            e.x, e.y, e.size, e.hp, e.max_hp = ev[1], ev[2], ev[3], ev[4], ev[4]
            e.speed = ev[5]
            e.touch_dmg = ev[6]
            e.xp = ev[7] if len(ev) > 7 else 5
            e.eid = ev[8] if len(ev) > 8 else 0
            e.freeze = 0; e.oc = 0
            e._born = _time.time()      # 用于豁免"刚生成就被校正清掉"
            enemies.append(e)
        elif t == 1:    # bullet: 发射子弹（本地控制飞行）
            b = Bullet.__new__(Bullet)
            b.x, b.y = ev[1], ev[2]
            b.vx, b.vy = ev[3], ev[4]
            b.dmg = ev[5]
            b.color = tuple(ev[6]) if len(ev) > 6 else YELLOW
            b.life = ev[7] if len(ev) > 7 else 3.0
            b.tracked = bool(ev[8]) if len(ev) > 8 else False
            b.big = bool(ev[9]) if len(ev) > 9 else False
            b.freeze = ev[10] if len(ev) > 10 else 0
            b.is_mine = False; b.from_boss = False
            bullets.append(b)
        elif t == 2:    # enemy_bullet: 敌方弹幕（本地控制飞行）
            b = Bullet.__new__(Bullet)
            b.x, b.y = ev[1], ev[2]
            b.vx, b.vy = ev[3], ev[4]
            b.dmg = ev[5] if len(ev) > 5 else 15
            b.color = PINK
            b.life = ev[6] if len(ev) > 6 else 3.0
            b.tracked = False; b.big = False; b.freeze = 0
            b.is_mine = False; b.from_boss = True
            enemy_bullets.append(b)
        elif t == 3:    # kill: 敌人死亡（本地播放特效）
            idx = ev[1]
            if idx < len(enemies) and enemies[idx].hp > 0:
                ex, ey = enemies[idx].x, enemies[idx].y
                enemies[idx].hp = 0
                # BUG修复：Particle 需要 (x, y, color) 三个参数，
                # 原来少传 color 直接抛 TypeError，导致 guest 主循环崩溃、同步率归零
                for _ in range(12):
                    particles.append(Particle(ex, ey, RED))
        elif t == 4:    # damage: 伤害数字（纯视觉，不改变 HP）
            idx = ev[1]
            dmg_v = ev[2]
            if idx < len(enemies):
                dmg_texts.append(DamageText(enemies[idx].x, enemies[idx].y - enemies[idx].size,
                                            dmg_v, ORANGE))
        elif t == 5:    # fx: 特效触发器（爆炸等）
            for _ in range(20):
                a = random.uniform(0, 6.28)
                sp = random.uniform(60, 250)
                p = Particle(ev[1], ev[2], tuple(ev[3]) if len(ev) > 3 else ORANGE)
                p.vx = math.cos(a)*sp; p.vy = math.sin(a)*sp
                p.life = random.uniform(0.2, 0.6)
                particles.append(p)
        elif t == 6:    # blackhole: 黑洞（本地生成视觉）
            bh = Blackhole(ev[1], ev[2])
            if len(ev) > 3:
                bh.radius = ev[3]
            if len(ev) > 4:
                bh.life = ev[4]
            blackholes.append(bh)
        elif t == 7:    # meteor: 陨石（本地跑下落动画）
            m = Meteor.__new__(Meteor)
            m.x, m.y, m.target_y = ev[1], ev[2], ev[3]
            m.vy = ev[4] if len(ev) > 4 else 500
            m.dmg = ev[5] if len(ev) > 5 else 80
            m.radius = ev[6] if len(ev) > 6 else 80
            meteors.append(m)
        elif t == 99:   # sync_enemies: 全量同步敌人（仅用于开局/断线重连重置世界）
            enemies.clear()
            for ed in ev[1]:
                e = Enemy.__new__(Enemy)
                e.x, e.y, e.size, e.hp, e.max_hp = ed[0], ed[1], ed[2], ed[3], ed[4]
                e.speed = ed[5]; e.freeze = ed[6]; e.oc = 0; e.xp = 0; e.touch_dmg = 0
                enemies.append(e)
            bullets.clear()
            for bd in ev[2]:
                b = Bullet.__new__(Bullet)
                b.x, b.y, b.color = bd[0], bd[1], tuple(bd[2])
                b.vx = b.vy = 0; b.dmg = 0; b.life = 1.0
                b.tracked = False; b.big = False; b.freeze = 0; b.is_mine = False; b.from_boss = False
                bullets.append(b)
    if bo:
        if boss is None:
            boss = Boss.__new__(Boss)
            boss.name = bo[3]
        boss.x, boss.y, boss.size = bo[0], bo[1], bo[2]
        boss.hp, boss.max_hp = bo[4], bo[5]
        boss.speed = 60; boss.touch_dmg = 30; boss.attack_timer = 0
        boss.freeze = 0; boss.oc = 0; boss.xp = 0
    else:
        boss = None

    # ── 位置校正：按 eid 匹配，平滑拉回正确位置（不瞬移，避免视觉跳变）──
    if corr:
        by_eid = {e.eid: e for e in enemies if getattr(e, 'eid', 0)}
        alive_ids = set()
        for row in corr:
            eid, ex, ey, ehp = row[0], row[1], row[2], row[3]
            efz = row[4] if len(row) > 4 else 0
            alive_ids.add(eid)
            e = by_eid.get(eid)
            if e is None:
                continue
            # 平滑插值 30%（避免瞬移抖动），血量直接覆盖（HP 必须准确）
            e.x += (ex - e.x) * 0.3
            e.y += (ey - e.y) * 0.3
            e.hp = ehp
            e.freeze = efz
        # BUG修复：只清理"之前已在场、且校正列表里确实消失"的敌人。
        # 刚收到 spawn 事件、host 校正还没轮到的敌人（eid 新）不能误杀，
        # 否则 guest 端敌人会被反复清空（实测同步率 8 -> 1）。
        # 用 spawn 时间戳兜底：新生成 1 秒内的敌人豁免清理。
        now_t = _time.time()
        for e in enemies:
            eid = getattr(e, 'eid', 0)
            if not eid or eid in alive_ids:
                continue
            born = getattr(e, '_born', 0)
            if now_t - born < 1.0:
                continue            # 刚生成，等下一轮校正
            if e.hp > 0:
                e.hp = 0
        enemies = [e for e in enemies if e.hp > 0]

def send_world_state():
    """host 端：增量事件推送 + 定期位置校正（事件驱动+本地视觉推测）

    不再每帧全量传输敌人/子弹（几百 KB），改为：
    1) 增量事件（几十字节）—— guest 收到后本地模拟飞行/特效
    2) 每 0.5 秒一次敌人位置校正快照 —— 防止 guest 本地推算漂移
    3) 子弹完全不同步位置 —— guest 本地跑，视觉零延迟
    """
    global last_state_send, events_buffer, last_pos_sync
    now = _time.time()
    if now - last_state_send < 0.033:
        return
    last_state_send = now

    evs = events_buffer[:24]
    events_buffer = events_buffer[24:]

    bo = None
    if boss and boss.hp > 0:
        bo = [round(boss.x), round(boss.y), boss.size, boss.name,
              round(boss.hp), round(boss.max_hp)]
    p2pos = [round(player2.x), round(player2.y)] if player2 else None
    p2m = None
    p2w = None
    if player2:
        p2m = [round(player2.hp), round(player2.max_hp), player2.level, player2.kills]
        p2w = player2.weapons

    # 每 0.5 秒发一次敌人位置校正（eid + 坐标 + 血量），量很小但能消除漂移
    corr = None
    if now - last_pos_sync >= 0.5:
        last_pos_sync = now
        corr = [[e.eid, round(e.x), round(e.y), round(e.hp), round(e.freeze, 1)]
                for e in enemies if e.hp > 0][:150]

    msg = {"a": "state", "p": [round(player.x), round(player.y)],
           "p2": p2pos, "p2m": p2m, "p2w": p2w,
           "bo": bo, "evs": evs}
    if corr is not None:
        msg["corr"] = corr
    net.send_raw(msg)

def send_guest_state():
    """guest 端：上报自己的移动输入 + 本地预测位置，并请求同步世界"""
    global last_state_send
    now = _time.time()
    if now - last_state_send < 0.033:
        return
    last_state_send = now
    net.send_raw({"a": "input", "dx": my_input[0], "dy": my_input[1],
                  "pl": [round(player.x), round(player.y)]})

# ============ 武器定义 ============
WEAPONS = {
    'pistol':  {'name': '手枪',   'cd': 0.4, 'color': YELLOW,  'dmg': 12},
    'shotgun': {'name': '霰弹枪', 'cd': 0.9, 'color': ORANGE,  'dmg': 8},
    'ring':    {'name': '环形炮',  'cd': 1.2, 'color': CYAN,    'dmg': 6},
    'laser':   {'name': '激光束', 'cd': 2.0, 'color': (240,171,252), 'dmg': 30},
    'missile': {'name': '导弹',   'cd': 2.5, 'color': ORANGE,   'dmg': 25},
    'mine':    {'name': '地雷',   'cd': 3.0, 'color': (192,132,252), 'dmg': 40},
    'frost':   {'name': '冰霜',   'cd': 0.8, 'color': BLUE,      'dmg': 5},
    'lightning':{'name':'闪电链', 'cd': 0.7, 'color': (253,224,71), 'dmg': 15},
    'spikes':  {'name': '旋转尖刺','cd': 0.5, 'color': RED,     'dmg': 8},
}

# ============ 组合技 ============
COMBOS = [
    ('twin_pistol',  ['pistol','shotgun'],    '双持手枪'),
    ('rapid_fire',   ['pistol','frost'],      '急速射击'),
    ('track_missile',['pistol','missile'],   '追踪导弹'),
    ('frost_nova',   ['frost','ring'],        '冰霜新星'),
    ('thunder_ring', ['lightning','ring'],    '雷霆之环'),
    ('tri_laser',    ['laser','spikes'],      '三重激光'),
    ('mine_field',   ['mine','frost'],        '地雷阵'),
    ('conductor',    ['lightning','missile'], '传导弹'),
    ('ice_storm',    ['frost','shotgun'],     '冰风暴'),
    ('meteor_shower',['missile','lightning'], '陨石雨'),
    ('blackhole',    ['laser','mine'],        '黑洞'),
    ('time_warp',    ['laser','lightning'],   '时间凝滞'),
    ('frost_mine',   ['frost','mine'],        '冰冻地雷'),
    ('piercing',     ['pistol','laser'],      '穿透弹'),
    ('explosive',    ['shotgun','missile'],   '爆破弹'),
    ('spin_frost',   ['spikes','frost'],      '冰霜旋转'),
    ('thunder_storm',['lightning','shotgun'], '雷霆风暴'),
    ('whirlwind',    ['ring','laser','lightning'], '风火轮'),
    ('laser_rain',   ['laser','lightning','ring'], '激光雨'),
    ('overclock',    ['pistol','shotgun','missile'], '超频秒杀'),
    ('absolute_zero',['frost','mine','spikes'], '绝对零度'),
    ('judgment',     ['lightning','missile','frost'], '天罚'),
]

def has_combo(weapons, combo_id):
    for cid, ws, _ in COMBOS:
        if cid == combo_id:
            return all(w in weapons for w in ws)
    return False

# ============ 关卡 ============
LEVELS = [
    {'name':'数据废墟', 'time':120, 'hp':1.0, 'spd':1.0, 'dmg':1.0, 'boss_hp':500,  'boss':'废墟守卫'},
    {'name':'病毒丛林', 'time':150, 'hp':1.5, 'spd':1.1, 'dmg':1.3, 'boss_hp':1200, 'boss':'丛林猎手'},
    {'name':'故障深渊', 'time':180, 'hp':2.2, 'spd':1.2, 'dmg':1.6, 'boss_hp':2500, 'boss':'深渊领主'},
    {'name':'核心风暴', 'time':210, 'hp':3.0, 'spd':1.3, 'dmg':2.0, 'boss_hp':4500, 'boss':'风暴核心'},
    {'name':'终焉之地', 'time':300, 'hp':4.0, 'spd':1.5, 'dmg':2.5, 'boss_hp':8000, 'boss':'系统毁灭者'},
]

# ============ 天赋 & 存档 ============
SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'save.json')
_SAVE_DEFAULT = {'shards':0,'hp':0,'damage':0,'speed':0,'crit':0,'atkSpeed':0,'regen':0,'unlocked':0,'scores':[]}
def load_save():
    try:
        with open(SAVE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # BUG修复：旧版/损坏存档可能缺字段，直接 save['unlocked'] 会 KeyError。
        # 与默认值合并，缺什么补什么。
        merged = dict(_SAVE_DEFAULT)
        if isinstance(data, dict):
            merged.update(data)
        if not isinstance(merged.get('scores'), list):
            merged['scores'] = []
        return merged
    except:
        return dict(_SAVE_DEFAULT)
def save_data():
    # BUG修复：存档目录不可写（如放在 Program Files）时原来直接崩溃
    try:
        with open(SAVE_FILE, 'w', encoding='utf-8') as f:
            json.dump(save, f, ensure_ascii=False)
    except Exception:
        pass

save = load_save()
talents = save  # 别名

_FONT_CACHE = {}
def get_font(size):
    # BUG修复（性能）：原来每次调用都新建 Font 对象，
    # 一帧几百个伤害飘字 = 几百次字体加载，帧率会被拖垮。加缓存。
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    # 直接加载 Windows 中文字体文件，避免 SysFont 找不到
    font_paths = [
        r'C:\Windows\Fonts\msyh.ttc',   # 微软雅黑
        r'C:\Windows\Fonts\msyhbd.ttc',
        r'C:\Windows\Fonts\simhei.ttf',   # 黑体
        r'C:\Windows\Fonts\simsun.ttc',  # 宋体
    ]
    f = None
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                f = pygame.font.Font(fp, size)
                break
            except:
                pass
    if f is None:
        # 回退到系统字体
        for name in ['microsoftyahei', 'simhei', 'arial']:
            try:
                f = pygame.font.SysFont(name, size)
                if f: break
            except: pass
    if f is None:
        f = pygame.font.Font(None, size)
    _FONT_CACHE[size] = f
    return f

# ============ 实体类 ============
class Player:
    def __init__(self, is_p2=False):
        hp_bonus = save['hp'] * 15
        self.is_p2 = is_p2
        self.x = WIDTH//2 + (60 if is_p2 else 0)
        self.y = HEIGHT//2 + (60 if is_p2 else 0)
        self.size = 16
        self.speed = 260 * (1 + save['speed']*0.04)
        self.max_hp = 100 + hp_bonus
        self.hp = self.max_hp
        self.crit_chance = 0.1 + save['crit']*0.03
        self.crit_mul = 2
        self.regen = save['regen'] * 0.5
        self.lifesteal = 0
        self.level = 1
        self.xp = 0
        self.xp_next = 5
        self.kills = 0
        # BUG修复（联机）：P2 原来初始武器为空，
        # 而 P2 卡池只在 P2 获得经验时才推送、P2 有武器才能杀敌得经验——
        # 死锁，guest 永远拿不到第一把武器。P2 同样从手枪开局。
        self.weapons = {'pistol': 1}
        self.cooldowns = {}
        self.dmg_mul = 1 + save['damage']*0.06
        self.atk_mul = 1 - save['atkSpeed']*0.05
        self.whirlwind_angle = 0
        self.whirlwind_bolt_timer = 0
        self.combo_timers = {}
        self.alive = True

class Enemy:
    _next_id = [1]      # 全局自增 ID：联机时用于 host/guest 精确匹配同一个敌人

    def __init__(self, cfg, gt):
        self.eid = Enemy._next_id[0]
        Enemy._next_id[0] += 1
        edge = random.randint(0,3)
        if edge==0:   self.x = random.randint(0,WIDTH); self.y = -30
        elif edge==1: self.x = WIDTH+30; self.y = random.randint(0,HEIGHT)
        elif edge==2: self.x = random.randint(0,WIDTH); self.y = HEIGHT+30
        else:         self.x = -30; self.y = random.randint(0,HEIGHT)
        self.size = random.randint(12, 20)
        hp = (20 + gt*1.5) * cfg['hp']
        self.hp = self.max_hp = hp
        self.speed = (70 + min(90, gt*0.9)) * cfg['spd']
        self.touch_dmg = (12 + gt*0.3) * cfg['dmg']
        self.xp = 5 + gt//20
        self.freeze = 0
        self.oc = 0

class Boss:
    def __init__(self, cfg):
        self.x, self.y = WIDTH//2, -80
        self.size = 50
        self.hp = self.max_hp = cfg['boss_hp']
        self.speed = 60
        self.touch_dmg = 30
        self.name = cfg['boss']
        self.attack_timer = 0
        self.freeze = 0
        # BUG修复：damage_enemy 会读写 e.oc / e.xp，
        # Boss 缺这两个属性时，带"超频秒杀"组合击杀 Boss 会直接 AttributeError 崩溃
        self.oc = 0
        self.xp = 60

class Bullet:
    def __init__(self, x, y, vx, vy, dmg, color, tracked=False, big=False, freeze=0, is_mine=False, from_boss=False):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.dmg = dmg
        self.color = color
        self.life = 3.0
        self.tracked = tracked
        self.big = big
        self.freeze = freeze
        self.is_mine = is_mine
        self.from_boss = from_boss

class DamageText:
    def __init__(self, x, y, text, color, big=False):
        self.x, self.y = x, y
        self.text = str(text)
        self.color = color
        self.life = 0.8
        self.big = big

class Particle:
    def __init__(self, x, y, color):
        self.x, self.y = x, y
        a = random.uniform(0, math.pi*2)
        s = random.uniform(50, 200)
        self.vx, self.vy = math.cos(a)*s, math.sin(a)*s
        self.life = random.uniform(0.3, 0.8)
        self.color = color

class Meteor:
    def __init__(self):
        self.x = random.randint(0, WIDTH)
        self.target_y = random.randint(100, HEIGHT-100)
        self.y = -50
        self.vy = 500
        self.dmg = 80
        self.radius = 80

class Blackhole:
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.radius = 150
        self.life = 3.0

# ============ 游戏状态 ============
state = 'login'  # login, menu, level, lobby, talents, help, playing, levelup, gameover
online_mode = False          # 本局是否为联机对局
net_role = 'host'            # host=权威模拟 / guest=接收渲染
my_input = [0, 0]            # 我发给对面的移动输入
peer_input = [0, 0]          # 对面发来的输入
last_state_send = 0.0        # host 发送状态的节流计时
last_pos_sync = 0.0          # host 发送敌人位置校正的节流计时（0.5 秒一次）
peer_pos = None              # guest 模式下收到的对面玩家位置（host视角）
peer_hp = 0
peer_level = 1
peer_kills = 0
peer_weapons = {}
lobby_status = ''            # 大厅提示文字
lobby_rooms = []             # 房间列表
login_msg = ''
login_msg_color = WHITE
auth_user = ''
join_room_input = ''         # 加入房间号输入框
create_room_id = ''          # 创建后显示的房间号
current_level = 0
player = None
player2 = None
enemies = []
bullets = []
enemy_bullets = []
particles = []
dmg_texts = []
boss = None
boss_spawned = False
game_time = 0
spawn_timer = 0
speed_idx = 0
game_speeds = [1, 2, 3]
shake = 0
flash = 0
level_clear = False
events_buffer = []      # 事件缓冲池：host 每帧往里面塞事件，send_world_state 取走
meteors = []
blackholes = []
time_warp_timer = 0
time_warp_active = 0

def reset_game(level_idx, coop=False):
    global player, player2, enemies, bullets, enemy_bullets, particles, dmg_texts
    global boss, boss_spawned, game_time, spawn_timer, shake, flash, level_clear
    global meteors, blackholes, time_warp_timer, time_warp_active, current_level
    global paused, speed_idx, cards
    # BUG修复：上一局暂停/倍速状态残留到新一局，
    # 暂停没解除的话新游戏直接是冻结的（看起来像卡死）
    paused = False
    speed_idx = 0
    cards = []
    current_level = level_idx
    player = Player()
    player2 = Player(is_p2=True) if coop else None
    enemies = []
    bullets = []
    enemy_bullets = []
    particles = []
    dmg_texts = []
    boss = None
    boss_spawned = False
    game_time = 0
    spawn_timer = 0
    shake = 0
    flash = 0
    level_clear = False
    events_buffer = []
    meteors = []
    blackholes = []
    time_warp_timer = 0
    time_warp_active = 0

def find_nearest_enemy(x, y):
    best, bd = None, 999999
    for e in enemies:
        d = math.hypot(e.x-x, e.y-y)
        if d < bd: bd, best = d, e
    # BUG修复：Boss 不参与索敌的话，只有闪电链/尖刺/激光类直伤武器的玩家
    # 永远打不到 Boss，Boss 关会永久卡死（软锁）
    if boss and boss.hp > 0:
        d = math.hypot(boss.x-x, boss.y-y)
        if d < bd: best = boss
    return best

def damage_enemy(e, raw_dmg, color=YELLOW):
    if e.hp <= 0: return
    dmg = raw_dmg * player.dmg_mul
    is_crit = random.random() < player.crit_chance
    if is_crit: dmg *= player.crit_mul
    e.hp -= dmg
    dmg_texts.append(DamageText(e.x, e.y-e.size, int(dmg), ORANGE if is_crit else color, is_crit))
    # 超频秒杀
    if has_combo(player.weapons, 'overclock'):
        e.oc += 1
        if e.oc >= 5:
            e.hp = 0
            dmg_texts.append(DamageText(e.x, e.y, '秒杀!', PURPLE, True))
            explode(e.x, e.y, PURPLE, 40)
    if e.hp <= 0:
        # 发送击杀事件给 guest（联机 host 模式）
        if online_mode and net_role == 'host':
            idx = next((i for i, ev in enumerate(enemies) if ev is e), -1)
            if idx >= 0:
                events_buffer.append([3, idx])  # kill 事件
                events_buffer.append([5, e.x, e.y, list(RED)])  # fx 爆炸
        player.kills += 1
        player.xp += e.xp
        explode(e.x, e.y, RED, 15)
        sfx_explode.play()
        if player.lifesteal:
            player.hp = min(player.max_hp, player.hp + player.lifesteal)
        if player.xp >= player.xp_next:
            player.xp -= player.xp_next
            player.level += 1
            player.xp_next = int(player.xp_next*1.35) + 3
            sfx_levelup.play()
            open_levelup()

def explode(x, y, color, count=15):
    for _ in range(count):
        particles.append(Particle(x, y, color))

def open_levelup():
    global state, cards
    # BUG修复：同一帧杀掉多个敌人会连续调用本函数，
    # 后一次覆盖前一次的 cards，玩家实际少拿卡。
    # 已有卡未选时不再重开（升级结算在 pick_card 里排队处理）。
    if state == 'levelup':
        return
    state = 'levelup'
    cards = build_card_pool()  # 只在打开时生成一次

def build_card_pool():
    pool = []
    for wid, wdef in WEAPONS.items():
        if wid not in player.weapons:
            pool.append(('new_weapon', wid, f"新武器: {wdef['name']}", wdef['color']))
        else:
            pool.append(('upgrade_weapon', wid, f"升级: {wdef['name']} Lv.{player.weapons[wid]+1}", wdef['color']))
    pool.append(('hp', None, '最大HP +25', GREEN))
    pool.append(('speed', None, '移速 +10%', CYAN))
    pool.append(('dmg', None, '伤害 +10%', YELLOW))
    pool.append(('crit', None, '暴击率 +5%', ORANGE))
    pool.append(('lifesteal', None, '吸血 +2', RED))
    random.shuffle(pool)
    return pool[:3]

def pick_card(card):
    global state
    kind, wid, _, _ = card
    # 联机 guest：把选择发给 host，本地不模拟
    if online_mode and net_role == 'guest':
        net.send_raw({"a": "pick", "card": [card[0], card[1], card[2], list(card[3])]})
        state = 'playing'
        return
    apply_card_to(player, card)
    # BUG修复：一次击杀获得大量经验可以连升多级，
    # 原逻辑只弹一次卡，多余的等级丢失。这里补一次连升检查。
    if player.xp >= player.xp_next:
        player.xp -= player.xp_next
        player.level += 1
        player.xp_next = int(player.xp_next*1.35) + 3
        sfx_levelup.play()
        # 必须先置回 playing 再调 open_levelup——
        # 否则 open_levelup 的"已在 levelup 就跳过"守卫会拦住重建，
        # 玩家会看到上一轮已经选过的旧卡池
        state = 'playing'
        open_levelup()      # 继续弹下一张卡
    else:
        state = 'playing'

def apply_card_to(p, card):
    """对指定玩家应用卡牌（host 用它同时给 P2 上卡）"""
    kind, wid, _, _ = card
    if kind == 'new_weapon': p.weapons[wid] = 1
    elif kind == 'upgrade_weapon': p.weapons[wid] = p.weapons.get(wid, 0) + 1
    elif kind == 'hp': p.max_hp += 25; p.hp = min(p.max_hp, p.hp+25)
    elif kind == 'speed': p.speed *= 1.1
    elif kind == 'dmg': p.dmg_mul *= 1.1
    elif kind == 'crit': p.crit_chance += 0.05
    elif kind == 'lifesteal': p.lifesteal += 2

def build_pool_for(p):
    """为指定玩家生成卡池（host 给 P2 生成时不能用 player 的武器表）"""
    pool = []
    for wid, wdef in WEAPONS.items():
        if wid not in p.weapons:
            pool.append(('new_weapon', wid, f"新武器: {wdef['name']}", wdef['color']))
        else:
            pool.append(('upgrade_weapon', wid, f"升级: {wdef['name']} Lv.{p.weapons[wid]+1}", wdef['color']))
    pool.append(('hp', None, '最大HP +25', GREEN))
    pool.append(('speed', None, '移速 +10%', CYAN))
    pool.append(('dmg', None, '伤害 +10%', YELLOW))
    pool.append(('crit', None, '暴击率 +5%', ORANGE))
    pool.append(('lifesteal', None, '吸血 +2', RED))
    random.shuffle(pool)
    return pool[:3]

def check_p2_levelups():
    """host 端：P2 经验满则升级并把卡池发给 guest"""
    global p2_pending
    if not (player2 and online_mode and net_role == 'host'):
        return
    if player2.xp >= player2.xp_next:
        player2.xp -= player2.xp_next
        player2.level += 1
        player2.xp_next = int(player2.xp_next*1.35) + 3
        cards2 = build_pool_for(player2)
        # 元组里的颜色是 tuple，json 可序列化
        net.send_raw({"a": "levelup_offer",
                      "cards": [[c[0], c[1], c[2], list(c[3])] for c in cards2]})

def damage_enemy_p2(e, raw_dmg, color=YELLOW):
    """P2 武器造成的伤害：击杀记到 P2 头上，经验给 P2"""
    if e.hp <= 0: return
    dmg = raw_dmg * player.dmg_mul
    is_crit = random.random() < player.crit_chance
    if is_crit: dmg *= player.crit_mul
    e.hp -= dmg
    dmg_texts.append(DamageText(e.x, e.y-e.size, int(dmg), ORANGE if is_crit else color, is_crit))
    if e.hp <= 0:
        player2.kills += 1
        player2.xp += e.xp
        explode(e.x, e.y, RED, 15)
        if player2.lifesteal:
            player2.hp = min(player2.max_hp, player2.hp + player2.lifesteal)
        check_p2_levelups()

# ============ 绘制工具 ============
def draw_text(text, x, y, size=20, color=WHITE, center=False):
    font = get_font(size)
    surf = font.render(str(text), True, color)
    rect = surf.get_rect()
    if center: rect.center = (x, y)
    else: rect.topleft = (x, y)
    screen.blit(surf, rect)

def button(x, y, w, h, label, hover=False):
    color = (40, 60, 100) if hover else (20, 24, 54)
    rect = pygame.Rect(x, y, w, h)
    pygame.draw.rect(screen, color, rect, border_radius=4)
    pygame.draw.rect(screen, CYAN, rect, 2, border_radius=4)
    draw_text(label, x+w//2, y+h//2, 18, CYAN, center=True)
    return rect

# ============ 武器更新 ============
def update_weapons(dt, shooter=None):
    # BUG修复：本函数会写 shake / time_warp_active 这两个全局量。
    # 不声明 global 时，"shake = max(shake, 8)" 会因先读后赋抛
    # UnboundLocalError（天罚组合一触发就崩），
    # time_warp_active 则只改了局部值，时间凝滞永远不生效。
    global shake, time_warp_active
    # BUG修复（双人）：原来武器只绑定 player，P2 武器栏永远为空，
    # 双人模式形同虚设。现在按 shooter 逐人开火（伤害加成仍共享 player 的）。
    if shooter is None:
        shooter = player
    # P2 的伤害击杀记到 P2、经验给 P2（host 权威）
    deal = damage_enemy_p2 if (shooter is not player and player2 is not None
                               and shooter is player2) else damage_enemy
    cfg = LEVELS[current_level]
    for wid, lv in list(shooter.weapons.items()):
        wdef = WEAPONS[wid]
        cd = wdef['cd'] * (1 - (lv-1)*0.1) * player.atk_mul
        shooter.cooldowns[wid] = shooter.cooldowns.get(wid, 0) - dt
        if shooter.cooldowns[wid] > 0: continue
        shooter.cooldowns[wid] = cd
        dmg = wdef['dmg'] * (1 + (lv-1)*0.35)

        if wid == 'pistol':
            t = find_nearest_enemy(shooter.x, shooter.y)
            if t:
                shots = 2 if has_combo(player.weapons, 'twin_pistol') else 1
                for i in range(shots):
                    ang = math.atan2(t.y-shooter.y, t.x-shooter.x) + random.uniform(-0.1, 0.1)
                    _b = Bullet(shooter.x, shooter.y, math.cos(ang)*500, math.sin(ang)*500, dmg, YELLOW, tracked=True)
                    bullets.append(_b)
                    if 'online_mode' in dir() and online_mode and net_role == 'host':
                        events_buffer.append([1, _b.x, _b.y, _b.vx, _b.vy, _b.dmg, list(_b.color), _b.life, 1 if _b.tracked else 0, 1 if _b.big else 0, _b.freeze])
                sfx_shoot.play()

        elif wid == 'shotgun':
            t = find_nearest_enemy(shooter.x, shooter.y)
            if t:
                base = math.atan2(t.y-shooter.y, t.x-shooter.x)
                for i in range(5+lv):
                    ang = base + (i-2.5)*0.12
                    _b = Bullet(shooter.x, shooter.y, math.cos(ang)*400, math.sin(ang)*400, dmg, ORANGE)
                    bullets.append(_b)
                    if 'online_mode' in dir() and online_mode and net_role == 'host':
                        events_buffer.append([1, _b.x, _b.y, _b.vx, _b.vy, _b.dmg, list(_b.color), _b.life, 0, 0, 0])

        elif wid == 'ring':
            for i in range(3+lv):
                ang = (i/(3+lv))*math.pi*2 + game_time*2
                _b = Bullet(shooter.x+math.cos(ang)*30, shooter.y+math.sin(ang)*30,
                                     math.cos(ang)*200, math.sin(ang)*200, dmg, CYAN)
                bullets.append(_b)
                if 'online_mode' in dir() and online_mode and net_role == 'host':
                    events_buffer.append([1, _b.x, _b.y, _b.vx, _b.vy, _b.dmg, list(_b.color), _b.life, 0, 0, 0])

        elif wid == 'laser':
            t = find_nearest_enemy(shooter.x, shooter.y)
            if t:
                ang = math.atan2(t.y-shooter.y, t.x-shooter.x)
                _b = Bullet(shooter.x, shooter.y, math.cos(ang)*800, math.sin(ang)*800, dmg, (240,171,252), big=True)
                bullets.append(_b)
                if 'online_mode' in dir() and online_mode and net_role == 'host':
                    events_buffer.append([1, _b.x, _b.y, _b.vx, _b.vy, _b.dmg, list(_b.color), _b.life, 0, 1, 0])

        elif wid == 'missile':
            t = find_nearest_enemy(shooter.x, shooter.y)
            if t:
                ang = math.atan2(t.y-shooter.y, t.x-shooter.x)
                _b = Bullet(shooter.x, shooter.y, math.cos(ang)*300, math.sin(ang)*300, dmg, ORANGE, tracked=True, big=True)
                bullets.append(_b)
                if 'online_mode' in dir() and online_mode and net_role == 'host':
                    events_buffer.append([1, _b.x, _b.y, _b.vx, _b.vy, _b.dmg, list(_b.color), _b.life, 1, 1, 0])

        elif wid == 'mine':
            m = Bullet(shooter.x, shooter.y, 0, 0, dmg, (192,132,252), is_mine=True)
            m.life = 999
            bullets.append(m)
            if 'online_mode' in dir() and online_mode and net_role == 'host':
                events_buffer.append([1, m.x, m.y, 0, 0, m.dmg, [192,132,252], 30.0, 0, 0, 0])

        elif wid == 'frost':
            t = find_nearest_enemy(shooter.x, shooter.y)
            if t:
                ang = math.atan2(t.y-shooter.y, t.x-shooter.x)
                _b = Bullet(shooter.x, shooter.y, math.cos(ang)*350, math.sin(ang)*350, dmg, BLUE, freeze=1.5)
                bullets.append(_b)
                if 'online_mode' in dir() and online_mode and net_role == 'host':
                    events_buffer.append([1, _b.x, _b.y, _b.vx, _b.vy, _b.dmg, list(_b.color), _b.life, 0, 0, _b.freeze])

        elif wid == 'lightning':
            t = find_nearest_enemy(shooter.x, shooter.y)
            if t:
                deal(t, dmg, (253,224,71))
                nearby = [e for e in enemies if e.hp>0 and e!=t and math.hypot(e.x-t.x,e.y-t.y)<100][:2]
                for n in nearby:
                    deal(n, dmg*0.5, (253,224,71))

        elif wid == 'spikes':
            # BUG修复：原来伤害是 dmg*dt（每帧量），但本分支被 cd=0.5s 冷却门控，
            # 每 0.5 秒才结算一次"一帧的伤害"，实际 DPS 只有设计值的 1/30，
            # 且 int() 后飘字永远是 0。改为每次冷却触发结算完整一击。
            for e in enemies:
                if math.hypot(e.x-shooter.x, e.y-shooter.y) < 80:
                    deal(e, dmg, RED)
            if boss and boss.hp > 0 and math.hypot(boss.x-shooter.x, boss.y-shooter.y) < 80+boss.size:
                deal(boss, dmg, RED)

    # === 组合技效果已拆到 update_combos() ===
    # BUG修复：update_weapons 现在按 shooter 每人调用一次（双人），
    # 组合技计时器写在里面会被双倍消耗。拆成独立函数，每帧只调一次。

def update_combos(dt):
    """组合技触发逻辑：每帧调用一次（不要放进按人循环的 update_weapons）"""
    global shake, time_warp_active
    # 黑洞
    if has_combo(player.weapons, 'blackhole'):
        player.combo_timers['blackhole'] = player.combo_timers.get('blackhole', 10) - dt
        if player.combo_timers['blackhole'] <= 0:
            player.combo_timers['blackhole'] = 10
            t = find_nearest_enemy(player.x, player.y)
            if t:
                blackholes.append(Blackhole(t.x, t.y))
                # 联机：通知 guest 在同样位置生成本地黑洞（视觉）
                if online_mode and net_role == 'host':
                    events_buffer.append([6, t.x, t.y, 150, 3.0])
            elif online_mode and net_role == 'host':
                # 没有敌人时也通知 guest（放在玩家身上，保持两边一致）
                events_buffer.append([6, player.x, player.y, 150, 3.0])

    # 陨石雨
    if has_combo(player.weapons, 'meteor_shower'):
        player.combo_timers['meteor'] = player.combo_timers.get('meteor', 5) - dt
        if player.combo_timers['meteor'] <= 0:
            player.combo_timers['meteor'] = 5
            for _ in range(3):
                m = Meteor()
                meteors.append(m)
                # 联机：把每颗陨石的落点和轨迹发给 guest，guest 本地跑下落动画
                if online_mode and net_role == 'host':
                    events_buffer.append([7, m.x, m.y, m.target_y, m.vy,
                                          m.dmg, m.radius])

    # 时间凝滞
    if has_combo(player.weapons, 'time_warp'):
        player.combo_timers['timewarp'] = player.combo_timers.get('timewarp', 15) - dt
        if player.combo_timers['timewarp'] <= 0:
            player.combo_timers['timewarp'] = 15
            time_warp_active = 3

    # 风火轮
    if has_combo(player.weapons, 'whirlwind'):
        player.whirlwind_angle += dt*3
        for e in enemies:
            if math.hypot(e.x-player.x, e.y-player.y) < 160:
                damage_enemy(e, 80*dt, YELLOW)

    # 天罚
    if has_combo(player.weapons, 'judgment'):
        player.combo_timers['judgment'] = player.combo_timers.get('judgment', 2.5) - dt
        if player.combo_timers['judgment'] <= 0:
            player.combo_timers['judgment'] = 2.5
            for _ in range(3):
                rx, ry = random.randint(0,WIDTH), random.randint(0,HEIGHT)
                for e in enemies:
                    if math.hypot(e.x-rx, e.y-ry) < 80:
                        damage_enemy(e, 60, WHITE)
                        e.freeze = max(e.freeze, 1.5)
                explode(rx, ry, WHITE, 20)
            shake = max(shake, 8)

# ============ 主循环 ============
running = True
paused = False
cards = []
mouse = (0, 0)

while running:
    dt = clock.tick(FPS) / 1000.0
    mouse = pygame.mouse.get_pos()
    # 网络消息泵（所有状态都要跑，登录/大厅依赖它）
    pump_net()
    # 心跳：防服务器空闲踢线 / 防云网关掐隧道
    if net.connected and state != 'playing':
        net.heartbeat()

    for event in pygame.event.get():
        if event.type == pygame.QUIT: running = False
        # ---------- 文本输入（登录页/大厅） ----------
        if event.type == pygame.TEXTINPUT:
            if state == 'login':
                if login_field[0] == 'user':
                    if len(auth_user) < 20: auth_user += event.text
                else:
                    if len(login_pw[0]) < 64: login_pw[0] += event.text
            elif state == 'lobby' and not create_room_id:
                ch = event.text.lower()
                if ch.isalnum() and len(join_room_input) < 6:
                    join_room_input += ch
            continue
        if event.type == pygame.KEYDOWN and state in ('login', 'lobby'):
            if state == 'login' and event.key == pygame.K_TAB:
                # Tab 切换用户名/密码输入框
                login_field[0] = 'pw' if login_field[0] == 'user' else 'user'
            elif event.key == pygame.K_BACKSPACE:
                if state == 'login':
                    if login_field[0] == 'user': auth_user = auth_user[:-1]
                    else: login_pw[0] = login_pw[0][:-1]
                else: join_room_input = join_room_input[:-1]
            elif event.key == pygame.K_RETURN and state == 'login':
                do_login()
            elif event.key == pygame.K_ESCAPE:
                if state == 'lobby':
                    state = 'menu'; create_room_id = ''; join_room_input = ''
                else:
                    running = False
            continue

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE and state == 'playing': paused = not paused
            if event.key == pygame.K_r and state == 'gameover': state = 'menu'
            if event.key == pygame.K_F11: pygame.display.toggle_fullscreen()
            if event.key == pygame.K_p and state == 'playing': paused = not paused
            if event.key == pygame.K_q and state == 'playing':
                speed_idx = (speed_idx+1) % 3

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if state == 'login':
                # 登录 / 注册 / 记住密码 / 退出（与渲染共用 LOGIN_BTN 等常量）
                if LOGIN_BTN.collidepoint(mx, my):
                    save_remember()   # 登录时按当前勾选状态保存/清除
                    do_login()
                elif REG_BTN.collidepoint(mx, my):
                    do_register()
                elif REMEMBER_RECT.collidepoint(mx, my):
                    remember_me[0] = not remember_me[0]
                elif QUIT_BTN.collidepoint(mx, my):
                    running = False
            elif state == 'lobby':
                # 创建房间 / 加入房间 / 刷新 / 返回
                # BUG修复：所有 send_raw 都要检查返回值——隧道被云网关掐断时
                # 写入会失败，原来不检查导致界面永远卡在"创建房间中..."
                if pygame.Rect(WIDTH//2-300, 180, 200, 50).collidepoint(mx, my):
                    if net.send_raw({"a": "create", "t": net.token}):
                        lobby_status = '创建房间中...'
                    else:
                        lobby_status = '连接已断开，正在自动重连...'
                        threading.Thread(target=_reconnect_async, daemon=True).start()
                elif pygame.Rect(WIDTH//2+100, 180, 200, 50).collidepoint(mx, my):
                    if join_room_input:
                        if net.send_raw({"a": "join", "t": net.token, "room": join_room_input}):
                            lobby_status = '加入中...'
                        else:
                            lobby_status = '连接已断开，正在自动重连...'
                            threading.Thread(target=_reconnect_async, daemon=True).start()
                elif pygame.Rect(WIDTH//2-300, 300, 140, 40).collidepoint(mx, my):
                    if not net.send_raw({"a": "rooms", "t": net.token}):
                        lobby_status = '连接已断开，正在自动重连...'
                        threading.Thread(target=_reconnect_async, daemon=True).start()
                elif pygame.Rect(WIDTH//2-140, HEIGHT-70, 280, 46).collidepoint(mx, my):
                    state = 'menu'; create_room_id = ''; join_room_input = ''
                # 点击房间列表项直接加入
                for i, rm in enumerate(lobby_rooms[:6]):
                    rect = pygame.Rect(WIDTH//2-300, 360+i*46, 600, 38)
                    if rect.collidepoint(mx, my):
                        if net.send_raw({"a": "join", "t": net.token, "room": rm['room']}):
                            lobby_status = f"加入 {rm['room']}..."
                        else:
                            lobby_status = '连接已断开，正在自动重连...'
                            threading.Thread(target=_reconnect_async, daemon=True).start()
            elif state == 'menu':
                if pygame.Rect(WIDTH//2-130, 300, 260, 50).collidepoint(mx, my): state = 'level'
                elif pygame.Rect(WIDTH//2-130, 370, 260, 50).collidepoint(mx, my): state = 'talents'
                elif pygame.Rect(WIDTH//2-130, 440, 260, 50).collidepoint(mx, my): state = 'help'
                elif pygame.Rect(WIDTH//2-130, 510, 260, 50).collidepoint(mx, my): state = 'lobby'
                elif pygame.Rect(WIDTH//2-130, 580, 260, 50).collidepoint(mx, my): running = False
            elif state == 'level':
                for i, lv in enumerate(LEVELS):
                    rect = pygame.Rect(200, 150+i*100, WIDTH-400, 80)
                    if rect.collidepoint(mx, my) and i <= save['unlocked']:
                        if online_mode and net_role == 'host':
                            # 联机：host 开双人局并通知 guest 同步开局
                            reset_game(i, coop=True)
                            net.send_raw({"a": "start", "level": i})
                        else:
                            reset_game(i, coop=False)
                        state = 'playing'
                        break
                if pygame.Rect(80, HEIGHT-70, 100, 40).collidepoint(mx, my):
                    if online_mode:
                        # 联机中退出选关 = 放弃开局，回大厅
                        state = 'lobby'
                    else:
                        state = 'menu'
            elif state == 'talents':
                talents_def = [('hp',10),('damage',10),('speed',8),('crit',5),('atkSpeed',8),('regen',5)]
                for i, (tid, max_lv) in enumerate(talents_def):
                    y = 130 + i*80
                    btn_rect = pygame.Rect(WIDTH-350, y+15, 130, 30)
                    if btn_rect.collidepoint(mx, my):
                        lv = save.get(tid, 0)
                        cost = 2 + lv*2
                        if lv < max_lv and save['shards'] >= cost:
                            save['shards'] -= cost
                            save[tid] = lv + 1
                            save_data()
                if pygame.Rect(80, HEIGHT-70, 100, 40).collidepoint(mx, my): state = 'menu'
            elif state == 'help':
                if pygame.Rect(80, HEIGHT-70, 100, 40).collidepoint(mx, my): state = 'menu'
            elif state == 'levelup':
                for i, card in enumerate(cards):
                    rect = pygame.Rect(WIDTH//2-350+i*250, 250, 220, 150)
                    if rect.collidepoint(mx, my):
                        pick_card(card)
                        # BUG修复：原来这里无条件 state='playing'，
                        # 会把 pick_card 内部连升时 open_levelup 设的 'levelup' 覆盖掉，
                        # 导致连升时后面的卡全部丢失
                        break

    # ============ 更新 ============
    if state == 'playing' and not paused and (not online_mode or net_role == 'host'):
        speed = game_speeds[speed_idx]
        game_time += dt * speed
        sdt = dt * speed
        if time_warp_active > 0: time_warp_active -= sdt

        # 玩家移动
        keys = pygame.key.get_pressed()
        dx = keys[pygame.K_d] - keys[pygame.K_a]
        dy = keys[pygame.K_s] - keys[pygame.K_w]
        if dx or dy:
            l = math.hypot(dx, dy)
            player.x += dx/l * player.speed * sdt
            player.y += dy/l * player.speed * sdt
        player.x = max(20, min(WIDTH-20, player.x))
        player.y = max(20, min(HEIGHT-20, player.y))
        if player.regen: player.hp = min(player.max_hp, player.hp + player.regen*sdt)

        # P2 移动：联机时由 guest 的输入驱动（host 权威），本地双人用方向键
        if player2 and player2.alive:
            if online_mode:
                dx2, dy2 = peer_input
            else:
                dx2 = keys[pygame.K_RIGHT] - keys[pygame.K_LEFT]
                dy2 = keys[pygame.K_DOWN] - keys[pygame.K_UP]
            if dx2 or dy2:
                l2 = math.hypot(dx2, dy2)
                player2.x += dx2/l2 * player2.speed * sdt
                player2.y += dy2/l2 * player2.speed * sdt
            # BUG修复：P2 原来没有边界钳制，可以跑出屏幕外再也回不来
            player2.x = max(20, min(WIDTH-20, player2.x))
            player2.y = max(20, min(HEIGHT-20, player2.y))

        # 刷怪
        spawn_timer -= sdt
        if spawn_timer <= 0:
            e = Enemy(LEVELS[current_level], game_time)
            enemies.append(e)
            # 联机 host：发送 spawn 事件给 guest（带 eid 便于后续校正）
            if online_mode and net_role == 'host':
                events_buffer.append([0, e.x, e.y, e.size, e.hp, e.speed,
                                      e.touch_dmg, e.xp, e.eid])
            spawn_timer = max(0.25, 1.6 - game_time*0.015)

        # Boss
        if not boss_spawned and game_time >= LEVELS[current_level]['time']:
            boss_spawned = True
            boss = Boss(LEVELS[current_level])
            flash = 0.5
        if boss and boss.hp > 0:
            # BUG修复（双人）：Boss 原来只追踪 P1，P1 死后仍追着尸体跑，
            # P2 完全不受 Boss 威胁。改为追踪最近的存活玩家。
            targets = [p for p in (player, player2) if p and p.alive and p.hp > 0]
            if targets:
                bt = min(targets, key=lambda p: math.hypot(p.x-boss.x, p.y-boss.y))
            else:
                bt = player
            ang = math.atan2(bt.y-boss.y, bt.x-boss.x)
            boss.x += math.cos(ang)*boss.speed*sdt
            boss.y += math.sin(ang)*boss.speed*sdt
            boss.attack_timer -= sdt
            if boss.attack_timer <= 0:
                boss.attack_timer = 1.2
                for i in range(8):
                    a = ang + (i-3.5)*0.2
                    _eb = Bullet(boss.x, boss.y, math.cos(a)*200, math.sin(a)*200, 15, PINK, from_boss=True)
                    enemy_bullets.append(_eb)
                    # 联机：Boss 弹幕也要通知 guest（否则 guest 看不到弹幕，会被"隐形弹"打死）
                    if online_mode and net_role == 'host':
                        events_buffer.append([2, _eb.x, _eb.y, _eb.vx, _eb.vy,
                                              15, _eb.life])
            if math.hypot(boss.x-player.x, boss.y-player.y) < boss.size+player.size:
                player.hp -= boss.touch_dmg*sdt
                sfx_hurt.play()
            if player2 and player2.alive and math.hypot(boss.x-player2.x, boss.y-player2.y) < boss.size+player2.size:
                player2.hp -= boss.touch_dmg*sdt
                if player2.hp <= 0:
                    player2.hp = 0
                    player2.alive = False
                sfx_hurt.play()

        # 敌人
        for e in enemies:
            if e.freeze > 0: e.freeze -= sdt
            spd = e.speed * (0.35 if e.freeze>0 else 1) * (0.3 if time_warp_active>0 else 1)
            # BUG修复（双人）：敌人原来只追踪 P1，P2 形同不存在。
            # 改为每个敌人追最近的存活玩家。
            _targets = [(player.x, player.y)]
            if player2 and player2.alive:
                _targets.append((player2.x, player2.y))
            _tx, _ty = min(_targets, key=lambda t: (t[0]-e.x)**2 + (t[1]-e.y)**2)
            ang = math.atan2(_ty-e.y, _tx-e.x)
            e.x += math.cos(ang)*spd*sdt
            e.y += math.sin(ang)*spd*sdt
            if math.hypot(e.x-player.x, e.y-player.y) < e.size+player.size:
                player.hp -= e.touch_dmg*sdt
                shake = max(shake, 4)
                sfx_hurt.play()
            if player2 and player2.alive and math.hypot(e.x-player2.x, e.y-player2.y) < e.size+player2.size:
                player2.hp -= e.touch_dmg*sdt
                if player2.hp <= 0: player2.alive = False

        # 黑洞
        for bh in blackholes:
            bh.life -= sdt
            for e in enemies:
                d = math.hypot(e.x-bh.x, e.y-bh.y)
                # BUG修复：d==0（敌人正好在黑洞中心）时除零崩溃
                if 0 < d < bh.radius:
                    e.x += (bh.x-e.x)/d * 200*sdt
                    e.y += (bh.y-e.y)/d * 200*sdt
                    if d < 30: damage_enemy(e, 80*sdt, PURPLE)
                elif d == 0 and bh.radius > 0:
                    damage_enemy(e, 80*sdt, PURPLE)
        blackholes = [b for b in blackholes if b.life > 0]

        # 陨石
        for m in meteors:
            m.y += m.vy*sdt
            if m.y >= m.target_y:
                explode(m.x, m.y, ORANGE, 40)
                shake = max(shake, 15)
                for e in enemies:
                    if math.hypot(e.x-m.x, e.y-m.y) < m.radius:
                        damage_enemy(e, m.dmg, ORANGE)
                        e.freeze = max(e.freeze, 1.5)
                m.y = 9999  # 标记删除
        meteors = [m for m in meteors if m.y < HEIGHT]

        # 子弹
        for b in bullets:
            if b.is_mine:
                for e in enemies:
                    if math.hypot(e.x-b.x, e.y-b.y) < 40:
                        damage_enemy(e, b.dmg, (192,132,252))
                        explode(b.x, b.y, (192,132,252), 30)
                        b.life = 0
                continue
            if b.tracked:
                t = find_nearest_enemy(b.x, b.y)
                if t:
                    desired = math.atan2(t.y-b.y, t.x-b.x)
                    cur = math.atan2(b.vy, b.vx)
                    diff = desired - cur
                    while diff > math.pi: diff -= math.pi*2
                    while diff < -math.pi: diff += math.pi*2
                    turn = max(-6*sdt, min(6*sdt, diff))
                    spd = math.hypot(b.vx, b.vy)
                    b.vx = math.cos(cur+turn)*spd
                    b.vy = math.sin(cur+turn)*spd
            b.x += b.vx*sdt
            b.y += b.vy*sdt
            b.life -= sdt
            for e in enemies:
                if e.hp > 0 and math.hypot(b.x-e.x, b.y-e.y) < e.size+(10 if b.big else 5):
                    damage_enemy(e, b.dmg, b.color)
                    if b.freeze: e.freeze = max(e.freeze, b.freeze)
                    b.life = 0
                    break
            if boss and boss.hp > 0 and math.hypot(b.x-boss.x, b.y-boss.y) < boss.size:
                damage_enemy(boss, b.dmg, WHITE)
                b.life = 0

        for b in enemy_bullets:
            b.x += b.vx*sdt; b.y += b.vy*sdt; b.life -= sdt
            if math.hypot(b.x-player.x, b.y-player.y) < player.size:
                player.hp -= 15; b.life = 0
            # BUG修复：双人模式下敌方弹幕原来只打 P1，P2 完全免疫，形同虚设
            elif player2 and player2.alive and math.hypot(b.x-player2.x, b.y-player2.y) < player2.size:
                player2.hp -= 15
                if player2.hp <= 0:
                    player2.hp = 0
                    player2.alive = False

        update_weapons(sdt, player)
        # 双人模式：P2 也开火（组合技判定仍走 player 的武器池）
        if player2 and player2.alive:
            update_weapons(sdt, player2)
        # 组合技每帧只结算一次（计时器不能被双人调用双倍消耗）
        update_combos(sdt)

        bullets = [b for b in bullets if b.life > 0]
        enemy_bullets = [b for b in enemy_bullets if b.life > 0]
        enemies = [e for e in enemies if e.hp > 0]
        particles = [p for p in particles if p.life > 0]
        dmg_texts = [d for d in dmg_texts if d.life > 0]

        for p in particles:
            p.x += p.vx*sdt; p.y += p.vy*sdt; p.life -= sdt
        for d in dmg_texts:
            d.y -= 30*sdt; d.life -= sdt

        # Boss 死亡
        if boss and boss.hp <= 0 and not level_clear:
            level_clear = True
            explode(boss.x, boss.y, YELLOW, 80)
            score = player.kills*10 + int(game_time)*5
            earned = player.kills + int(game_time//10)
            save['shards'] += earned
            save['unlocked'] = max(save['unlocked'], min(5, current_level+1))
            save['scores'].append({'level':current_level+1,'kills':player.kills,'score':score,'time':int(game_time)})
            save['scores'].sort(key=lambda x: -x['score'])
            save['scores'] = save['scores'][:20]
            save_data()
            # 联机：通知 guest 本局结束并结算
            if online_mode:
                net.send_raw({"a": "end", "win": True, "earned": earned})
            state = 'gameover'

        # 玩家死亡（联机：两人都死才判负）
        _p1_dead = player.hp <= 0
        _p2_dead = (player2 is None) or (not player2.alive) or player2.hp <= 0
        if _p1_dead and (not online_mode or _p2_dead) and state == 'playing':
            player.hp = 0
            score = player.kills*10 + int(game_time)*5
            earned = player.kills + int(game_time//10)
            save['shards'] += earned
            save['scores'].append({'level':current_level+1,'kills':player.kills,'score':score,'time':int(game_time)})
            save['scores'].sort(key=lambda x: -x['score'])
            save['scores'] = save['scores'][:20]
            save_data()
            if online_mode:
                net.send_raw({"a": "end", "win": False, "earned": earned})
            state = 'gameover'

        # host 发送世界快照
        if online_mode:
            send_world_state()

    # ---------- guest 轻量模拟：本地预测移动 + 本地视觉推测 ----------
    elif state == 'playing' and not paused and online_mode and net_role == 'guest':
        keys = pygame.key.get_pressed()
        dx = keys[pygame.K_d] - keys[pygame.K_a]
        dy = keys[pygame.K_s] - keys[pygame.K_w]
        if dx or dy:
            l = math.hypot(dx, dy)
            player.x += dx/l * player.speed * dt
            player.y += dy/l * player.speed * dt
        player.x = max(20, min(WIDTH-20, player.x))
        player.y = max(20, min(HEIGHT-20, player.y))
        my_input = [dx, dy]
        send_guest_state()

        # ── 本地视觉推测（事件驱动同步的核心）──
        # 子弹由本地推进飞行，host 不再每帧告诉每颗子弹飞到哪了
        for b in bullets:
            b.x += b.vx*dt; b.y += b.vy*dt; b.life -= dt
        bullets = [b for b in bullets if b.life > 0]
        for b in enemy_bullets:
            b.x += b.vx*dt; b.y += b.vy*dt; b.life -= dt
        enemy_bullets = [b for b in enemy_bullets if b.life > 0]

        # 敌人本地朝我移动（位置随后会被 host 快照校正，视觉上更连贯）
        for e in enemies:
            if e.hp <= 0: continue
            if e.freeze > 0: e.freeze -= dt
            spd = e.speed * (0.35 if e.freeze > 0 else 1)
            a = math.atan2(player.y - e.y, player.x - e.x)
            e.x += math.cos(a)*spd*dt
            e.y += math.sin(a)*spd*dt

        # 陨石本地下落（收到事件后自己跑动画）
        for m in meteors:
            m.y += m.vy*dt
            if m.y >= m.target_y:
                for _ in range(40):
                    a = random.uniform(0, 6.28)
                    sp = random.uniform(60, 250)
                    p = Particle(m.x, m.y, ORANGE)
                    p.vx = math.cos(a)*sp; p.vy = math.sin(a)*sp
                    particles.append(p)
                m.y = 9999
        meteors = [m for m in meteors if m.y < HEIGHT]

        # 黑洞本地倒计时 + 吸附动画
        for bh in blackholes:
            bh.life -= dt
        blackholes = [b for b in blackholes if b.life > 0]

        # 粒子/飘字本地衰减
        for p in particles:
            p.x += p.vx*dt; p.y += p.vy*dt; p.life -= dt
        for d in dmg_texts:
            d.y -= 30*dt; d.life -= dt
        particles = [p for p in particles if p.life > 0]
        dmg_texts = [d for d in dmg_texts if d.life > 0]

    # ============ 渲染 ============
    if state == 'login':
        screen.fill(BLACK)
        draw_text('生 存 代 码', WIDTH//2, 120, 64, CYAN, center=True)
        draw_text('SURVIVAL CODE · 需登录后进入', WIDTH//2, 175, 16, PURPLE, center=True)
        # 用户名输入框
        ufocus = login_field[0] == 'user'
        pygame.draw.rect(screen, (20,24,54), (WIDTH//2-140, 250, 280, 44), border_radius=4)
        pygame.draw.rect(screen, YELLOW if ufocus else CYAN, (WIDTH//2-140, 250, 280, 44),
                         3 if ufocus else 2, border_radius=4)
        draw_text('用户名', WIDTH//2-130, 256, 12, (140,150,180))
        draw_text(auth_user + ('|' if ufocus else ''), WIDTH//2-130, 272, 18, WHITE)
        # 密码输入框
        pygame.draw.rect(screen, (20,24,54), (WIDTH//2-140, 310, 280, 44), border_radius=4)
        pygame.draw.rect(screen, YELLOW if not ufocus else CYAN, (WIDTH//2-140, 310, 280, 44),
                         3 if not ufocus else 2, border_radius=4)
        draw_text('密码', WIDTH//2-130, 316, 12, (140,150,180))
        draw_text('*'*len(login_pw[0]) + ('|' if not ufocus else ''), WIDTH//2-130, 332, 18, WHITE)
        draw_text('打字输入 · Tab 切换输入框 · 退格删除 · 回车登录', WIDTH//2, 368, 13, (90,95,120), center=True)
        # 记住账号密码勾选框
        REMEMBER_RECT.x, REMEMBER_RECT.y = WIDTH//2-140, 392   # 每帧同步渲染位置
        hov = REMEMBER_RECT.collidepoint(mouse)
        pygame.draw.rect(screen, (20,24,54), REMEMBER_RECT, border_radius=4)
        box = pygame.Rect(WIDTH//2-130, 398, 18, 18)
        pygame.draw.rect(screen, YELLOW if remember_me[0] else (90,95,120), box, 2, border_radius=3)
        if remember_me[0]:
            pygame.draw.lines(screen, YELLOW, False,
                              [(box.x+3, box.y+9), (box.x+8, box.y+14), (box.x+15, box.y+4)], 2)
        draw_text('记住账号和密码', box.right + 10, 400, 15,
                  (230,230,240) if hov else (160,165,190))
        button(LOGIN_BTN.x, LOGIN_BTN.y, LOGIN_BTN.w, LOGIN_BTN.h, '登 录',
               LOGIN_BTN.collidepoint(mouse))
        button(REG_BTN.x, REG_BTN.y, REG_BTN.w, REG_BTN.h, '注 册',
               REG_BTN.collidepoint(mouse))
        button(QUIT_BTN.x, QUIT_BTN.y, QUIT_BTN.w, QUIT_BTN.h, '退出程序',
               QUIT_BTN.collidepoint(mouse))
        if login_msg:
            draw_text(login_msg, WIDTH//2, 630, 16, login_msg_color, center=True)
        draw_text(f'服务器 {SERVER_HOST}:{SERVER_PORT}', WIDTH//2, HEIGHT-30, 12, (80,85,110), center=True)

    elif state == 'lobby':
        screen.fill(BLACK)
        draw_text('联机大厅', WIDTH//2, 60, 36, CYAN, center=True)
        draw_text(f'欢迎 {net.user} · 服务器 {SERVER_HOST}:{SERVER_PORT}',
                  WIDTH//2, 105, 14, (140,150,180), center=True)
        # 创建 / 加入
        button(WIDTH//2-300, 180, 200, 50, '创建房间',
               pygame.Rect(WIDTH//2-300,180,200,50).collidepoint(mouse))
        if create_room_id:
            draw_text(f'房间号: {create_room_id}  (发给队友加入)', WIDTH//2, 260, 20, YELLOW, center=True)
        pygame.draw.rect(screen, (20,24,54), (WIDTH//2+100, 180, 200, 50), border_radius=4)
        pygame.draw.rect(screen, CYAN, (WIDTH//2+100, 180, 200, 50), 2, border_radius=4)
        draw_text('输入房间号加入', WIDTH//2+110, 188, 12, (140,150,180))
        draw_text((join_room_input or '...') + '|', WIDTH//2+110, 206, 18, WHITE)
        button(WIDTH//2-300, 300, 140, 40, '刷新列表',
               pygame.Rect(WIDTH//2-300,300,140,40).collidepoint(mouse))
        draw_text('公开房间(点击加入):', WIDTH//2-300, 332, 14, (140,150,180))
        if not lobby_rooms:
            draw_text('暂无等待中的房间', WIDTH//2-300, 366, 14, (90,95,120))
        for i, rm in enumerate(lobby_rooms[:6]):
            rect = pygame.Rect(WIDTH//2-300, 360+i*46, 600, 38)
            hov = rect.collidepoint(mouse)
            pygame.draw.rect(screen, (40,60,100) if hov else (20,24,54), rect, border_radius=4)
            pygame.draw.rect(screen, CYAN, rect, 1, border_radius=4)
            draw_text(f"房间 {rm['room']}   房主: {rm['host']}", rect.x+16, rect.y+9, 16, WHITE)
        if lobby_status:
            draw_text(lobby_status, WIDTH//2, 660, 16, YELLOW, center=True)
        button(WIDTH//2-140, HEIGHT-70, 280, 46, '返回主菜单',
               pygame.Rect(WIDTH//2-140,HEIGHT-70,280,46).collidepoint(mouse))

    elif state == 'menu':
        screen.fill(BLACK)
        draw_text('生 存 代 码', WIDTH//2, 150, 72, CYAN, center=True)
        draw_text('SURVIVAL CODE', WIDTH//2, 200, 20, PURPLE, center=True)
        draw_text(f'已登录: {net.user or "-"}', WIDTH//2, 250, 14, (140,150,180), center=True)
        button(WIDTH//2-130, 300, 260, 50, '开始战斗', pygame.Rect(WIDTH//2-130,300,260,50).collidepoint(mouse))
        button(WIDTH//2-130, 370, 260, 50, '天赋树', pygame.Rect(WIDTH//2-130,370,260,50).collidepoint(mouse))
        button(WIDTH//2-130, 440, 260, 50, '帮助', pygame.Rect(WIDTH//2-130,440,260,50).collidepoint(mouse))
        button(WIDTH//2-130, 510, 260, 50, '联机大厅', pygame.Rect(WIDTH//2-130,510,260,50).collidepoint(mouse))
        button(WIDTH//2-130, 580, 260, 50, '退出', pygame.Rect(WIDTH//2-130,580,260,50).collidepoint(mouse))

    elif state == 'level':
        screen.fill(BLACK)
        draw_text('选择关卡', WIDTH//2, 80, 36, YELLOW, center=True)
        for i, lv in enumerate(LEVELS):
            y = 150 + i*100
            rect = pygame.Rect(200, y, WIDTH-400, 80)
            unlocked = i <= save['unlocked']
            hover = rect.collidepoint(mouse) and unlocked
            color = (50, 70, 120) if hover else ((30,40,70) if unlocked else (20,20,20))
            pygame.draw.rect(screen, color, rect, border_radius=4)
            pygame.draw.rect(screen, CYAN if unlocked else (80,80,80), rect, 2, border_radius=4)
            draw_text(f"第{i+1}关 · {lv['name']}", 220, y+15, 20, WHITE if unlocked else (100,100,100))
            draw_text(f"{lv['time']//60}分钟 · 敌人x{lv['hp']} · Boss: {lv['boss']}", 220, y+45, 14, (150,150,150) if unlocked else (80,80,80))
        button(80, HEIGHT-70, 100, 40, '返回')

    elif state == 'talents':
        screen.fill(BLACK)
        draw_text(f"天赋树 (碎片: {save['shards']})", WIDTH//2, 60, 32, CYAN, center=True)
        tdef = [('hp','生命强化','每级最大HP+15',10),('damage','攻击强化','每级伤害+6%',10),
                ('speed','移动加速','每级移速+4%',8),('crit','暴击精通','每级暴击率+3%',5),
                ('atkSpeed','超频引擎','每级冷却-5%',8),('regen','生命回复','每级每秒回血+0.5',5)]
        for i, (tid, name, desc, max_lv) in enumerate(tdef):
            y = 130 + i*80
            lv = save.get(tid, 0)
            cost = 2 + lv*2
            rect = pygame.Rect(200, y, WIDTH-400, 60)
            pygame.draw.rect(screen, (30,40,70), rect, border_radius=4)
            pygame.draw.rect(screen, CYAN, rect, 1, border_radius=4)
            draw_text(f"{name} Lv.{lv}/{max_lv}", 220, y+8, 18, WHITE)
            draw_text(desc, 220, y+35, 14, (150,150,150))
            btn_rect = pygame.Rect(WIDTH-350, y+15, 130, 30)
            can_up = lv < max_lv and save['shards'] >= cost
            pygame.draw.rect(screen, (60,60,30) if can_up else (40,40,40), btn_rect, border_radius=3)
            draw_text(f"升级({cost})" if lv<max_lv else "已满级", WIDTH-285, y+30, 14, YELLOW if can_up else (100,100,100), center=True)
        button(80, HEIGHT-70, 100, 40, '返回')

    elif state == 'help':
        screen.fill(BLACK)
        draw_text('帮助', WIDTH//2, 80, 36, YELLOW, center=True)
        lines = ['WASD 移动', '武器自动攻击最近敌人', '升级三选一卡牌',
                 '集齐武器组合触发协同技', 'P 暂停', 'Q 切换倍速 (1x/2x/3x)', 'F11 全屏']
        for i, line in enumerate(lines):
            draw_text(line, WIDTH//2, 180+i*40, 20, WHITE, center=True)
        button(80, HEIGHT-70, 100, 40, '返回')

    elif state in ('playing', 'levelup', 'gameover'):
        screen.fill(BLACK)
        sx = sy = 0
        if shake > 0:
            sx = random.randint(-int(shake), int(shake))
            sy = random.randint(-int(shake), int(shake))
            shake *= 0.85

        # 时间凝滞效果
        if time_warp_active > 0:
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((125,211,252,50))
            screen.blit(overlay, (0,0))

        # 黑洞
        for bh in blackholes:
            pygame.draw.circle(screen, PURPLE, (int(bh.x+sx), int(bh.y+sy)), 30)
            pygame.draw.circle(screen, BLACK, (int(bh.x+sx), int(bh.y+sy)), 15)

        # 粒子
        for p in particles:
            pygame.draw.circle(screen, p.color, (int(p.x+sx), int(p.y+sy)), 3)

        # 敌人
        for e in enemies:
            c = BLUE if e.freeze>0 else RED
            pygame.draw.rect(screen, c, (e.x-e.size//2+sx, e.y-e.size//2+sy, e.size, e.size))

        # Boss
        if boss and boss.hp > 0:
            pygame.draw.circle(screen, PURPLE, (int(boss.x+sx), int(boss.y+sy)), boss.size)
            pygame.draw.circle(screen, WHITE, (int(boss.x+sx), int(boss.y+sy)), boss.size, 3)

        # 子弹
        for b in bullets:
            pygame.draw.circle(screen, b.color, (int(b.x+sx), int(b.y+sy)), 6 if b.big else 3)
        for b in enemy_bullets:
            pygame.draw.circle(screen, PINK, (int(b.x+sx), int(b.y+sy)), 5)

        # 玩家
        pygame.draw.rect(screen, CYAN, (player.x-player.size//2+sx, player.y-player.size//2+sy, player.size, player.size))
        if player2 and player2.alive:
            pygame.draw.rect(screen, PINK, (player2.x-player2.size//2+sx, player2.y-player2.size//2+sy, player2.size, player2.size))

        # 伤害飘字
        for d in dmg_texts:
            draw_text(d.text, int(d.x+sx), int(d.y+sy), 16 if d.big else 12, d.color)

        # HUD
        pygame.draw.rect(screen, (0,0,0), (20, 20, 200, 20))
        pygame.draw.rect(screen, RED, (20, 20, 200*(player.hp/player.max_hp), 20))
        draw_text(f"HP {int(player.hp)}/{player.max_hp}", 25, 22, 14, WHITE)
        pygame.draw.rect(screen, (0,0,0), (20, 45, 200, 10))
        pygame.draw.rect(screen, CYAN, (20, 45, 200*(player.xp/player.xp_next), 10))
        draw_text(f"Lv.{player.level}  击杀:{player.kills}  时间:{int(game_time)}s  速度:{game_speeds[speed_idx]}x (Q切换)", 20, 65, 16, WHITE)
        y = 100
        for wid, lv in player.weapons.items():
            draw_text(f"{WEAPONS[wid]['name']} Lv.{lv}", 20, y, 14, WEAPONS[wid]['color'])
            y += 20
        if boss and boss.hp > 0:
            pygame.draw.rect(screen, (0,0,0), (WIDTH//2-200, 20, 400, 15))
            pygame.draw.rect(screen, PURPLE, (WIDTH//2-200, 20, 400*(boss.hp/boss.max_hp), 15))
            draw_text(boss.name, WIDTH//2, 12, 14, WHITE, center=True)

        if state == 'levelup':
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((0,0,0,180))
            screen.blit(overlay, (0,0))
            draw_text('选择升级', WIDTH//2, 150, 40, YELLOW, center=True)
            for i, card in enumerate(cards):
                x = WIDTH//2 - 350 + i*250
                rect = pygame.Rect(x, 250, 220, 150)
                hover = rect.collidepoint(mouse)
                pygame.draw.rect(screen, (50,80,140) if hover else (20,30,60), rect, border_radius=6)
                pygame.draw.rect(screen, card[3], rect, 2, border_radius=6)
                draw_text(card[2], x+110, 325, 16, card[3], center=True)

        if paused:
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((0,0,0,150))
            screen.blit(overlay, (0,0))
            draw_text('已暂停 (P)', WIDTH//2, HEIGHT//2-30, 36, YELLOW, center=True)
            draw_text('Q 切换速度', WIDTH//2, HEIGHT//2+20, 20, CYAN, center=True)

        if state == 'gameover':
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((0,0,0,200))
            screen.blit(overlay, (0,0))
            if level_clear:
                draw_text(f"通关第{current_level+1}关!", WIDTH//2, 250, 48, YELLOW, center=True)
            else:
                draw_text('系统崩溃', WIDTH//2, 250, 48, RED, center=True)
            score = player.kills*10 + int(game_time)*5
            draw_text(f"击杀 {player.kills} · 得分 {score}", WIDTH//2, 330, 22, WHITE, center=True)
            draw_text(f"已解锁第{min(5,current_level+2)}关", WIDTH//2, 370, 20, CYAN, center=True)
            draw_text('按 R 返回主菜单', WIDTH//2, 450, 18, CYAN, center=True)

    pygame.display.flip()

pygame.quit()
