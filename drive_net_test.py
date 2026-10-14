# -*- coding: utf-8 -*-
"""drive_net_test.py — 双客户端联机流程无头驱动测试
每实例独立事件队列 + 假键盘，模拟 host/guest 完整联机对局。
"""
import os, sys, json, threading, time
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame
pygame.init()
pygame.display.set_mode((100, 100))

# 假键盘：两个循环共享同一份按键状态表
class FakeKeys:
    def __init__(self): self.d = {}
    def __getitem__(self, k): return 1 if self.d.get(k) else 0
    def __len__(self): return 512
fk = FakeKeys()
pygame.key.get_pressed = lambda: fk

# 起服务器
import survival_server as S
threading.Thread(target=S.main, daemon=True).start()
time.sleep(0.8)

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, 'game_pygame.py'), encoding='utf-8').read()
src = src.replace('SERVER_HOST = "167.104.99.187"', 'SERVER_HOST = "127.0.0.1"')
src = src.replace('SERVER_PORT = 18765', 'SERVER_PORT = 8765')
src = src.replace('SERVER_CANDIDATES = [(SERVER_HOST, SERVER_PORT), ("127.0.0.1", 8765)]', 'SERVER_CANDIDATES = []')
src = src.replace('while running:', 'while running:')
src = src.replace('for event in pygame.event.get():', 'for event in _evq.get():')
src = src.replace('    dt = clock.tick(FPS) / 1000.0', '    dt = 0.016')
src = src.replace('pygame.quit()', '')

class Evq:
    def __init__(self): self.q = []
    def post(self, ev): self.q.append(ev)
    def get(self):
        out, self.q = self.q, []
        return out

def make():
    g = {'__file__': os.path.join(HERE, 'game_pygame.py'), '__name__': '__main__'}
    g['_evq'] = Evq()
    threading.Thread(target=exec, args=(compile(src, 'gp', 'exec'), g), daemon=True).start()
    return g

def click(g, x, y):
    g['_evq'].post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(x, y)))

def type_text(g, text):
    for ch in text:
        g['_evq'].post(pygame.event.Event(pygame.TEXTINPUT, text=ch))

def keydown(g, key):
    g['_evq'].post(pygame.event.Event(pygame.KEYDOWN, key=key))

gH, gG = make(), make()
time.sleep(1.5)


print("[1] 两边注册+登录")
type_text(gH, 'host01'); keydown(gH, pygame.K_TAB); type_text(gH, 'pass1234')
click(gH, 640, 515)   # 注册按钮
time.sleep(1.0)
click(gH, 640, 455)   # 登录按钮
type_text(gG, 'guest01'); keydown(gG, pygame.K_TAB); type_text(gG, 'pass1234')
click(gG, 640, 515)
time.sleep(1.0)
click(gG, 640, 455)
time.sleep(1.5)
print("   H:", gH.get('state'), " G:", gG.get('state'))
assert gH.get('state') == 'menu' and gG.get('state') == 'menu'

print("[2] 进大厅，host 创建房间")
click(gH, 640, 535); click(gG, 640, 535); time.sleep(0.5)
click(gH, 440, 205)   # 创建房间按钮中心
time.sleep(1.2)
room = gH.get('create_room_id')
print("   房间号:", room)
assert room, "host 未拿到房间号"

print("[3] guest 加入")
type_text(gG, room)
click(gG, 840, 205)   # 加入按钮中心
time.sleep(1.5)
print("   H paired:", gH['net'].paired, " G paired:", gG['net'].paired)
assert gH['net'].paired and gG['net'].paired
print("   双方回到选关:", gH.get('state'), gG.get('state'))

print("[4] host 选第1关")
click(gH, 640, 190)
time.sleep(2.5)
# 双方可能连升多级反复弹卡：循环点第一张直到进入游戏
for gg in (gH, gG):
    for _ in range(12):
        if gg.get('state') == 'levelup' and gg.get('cards'):
            click(gg, 400, 325)
            time.sleep(0.35)
        else:
            break
print("   H:", gH.get('state'), "role", gH.get('net_role'),
      "| G:", gG.get('state'), "role", gG.get('net_role'))
assert gH.get('state') == 'playing' and gG.get('state') == 'playing'
assert gH.get('online_mode') and gG.get('online_mode')

print("[5] 同步检查（持续采样取峰值）")
def keep_playing(gg, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        if gg.get('state') == 'levelup' and gg.get('cards'):
            click(gg, 400, 325)
        time.sleep(0.12)
peakH = peakG = 0
t0 = time.time()
th = threading.Thread(target=keep_playing, args=(gH, 6)); th.start()
tg = threading.Thread(target=keep_playing, args=(gG, 6)); tg.start()
while time.time() - t0 < 6:
    peakH = max(peakH, len(gH.get('enemies', [])))
    peakG = max(peakG, len(gG.get('enemies', [])))
    time.sleep(0.1)
th.join(); tg.join()
print(f"   host 敌人峰值={peakH}  guest 同步峰值={peakG}  game_time={gH.get('game_time',0):.0f}s")
assert peakH > 0 and peakG > 0, "敌人未同步"
assert gG['player2'] is not None, "guest 应有队友渲染槽位"

print("[6] guest 输入 -> host 端 P2 移动")
def keep_playing(gg, secs):
    # 升级界面会暂停模拟（单机设计如此），测试中持续自动选卡保持对局进行
    t0 = time.time()
    while time.time() - t0 < secs:
        if gg.get('state') == 'levelup' and gg.get('cards'):
            click(gg, 400, 325)
        time.sleep(0.15)
p2x0 = gH['player2'].x
fk.d[pygame.K_d] = 1
th = threading.Thread(target=keep_playing, args=(gH, 2.0)); th.start()
tg = threading.Thread(target=keep_playing, args=(gG, 2.0)); tg.start()
time.sleep(2.2); th.join(); tg.join()
moved = abs(gH['player2'].x - p2x0)
fk.d.pop(pygame.K_d)
print(f"   host 视角 P2 位移: {moved:.0f}px")
assert moved > 10, "guest 输入未驱动 host 端 P2"

print("[7] guest 本地预测位置被 host 快照校正")
print("   guest self pos:", [round(v) for v in (gG['player'].x, gG['player'].y)],
      " host p2 pos:", [round(v) for v in (gH['player2'].x, gH['player2'].y)])
assert abs(gG['player'].x - gH['player2'].x) < 60, "位置同步偏差过大"

print()
print("=== 联机双客户端驱动测试 PASS ===")
for gg in (gH, gG):
    gg['running'] = False
    try: gg['net'].close()
    except Exception: pass
time.sleep(0.8)
print('done')
