# -*- coding: utf-8 -*-
"""smoke_gameplay.py — 单机实战冒烟：自动登录→开局→全武器→长时间跑帧
检查：崩溃 / 异常 / 内存泄漏 / 武器是否真的造成伤害
"""
import os, sys, time, threading, random
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame
pygame.init()
pygame.display.set_mode((100, 100))

HERE = os.path.dirname(os.path.abspath(__file__))

# 起本地服务器（登录用）
import survival_server as S
threading.Thread(target=S.main, daemon=True).start()
time.sleep(0.8)

src = open(os.path.join(HERE, 'game_pygame.py'), encoding='utf-8').read()
src = src.replace('SERVER_HOST = "167.104.99.187"', 'SERVER_HOST = "127.0.0.1"')
src = src.replace('SERVER_PORT = 18765', 'SERVER_PORT = 8765')
src = src.replace('for event in pygame.event.get():', 'for event in _evq.get():')
src = src.replace('    dt = clock.tick(FPS) / 1000.0', '    dt = 0.016')
src = src.replace('pygame.quit()', '')
# 帧预算 + 时间快进（让怪物血量/速度上来，压力更大）
src = src.replace('while running:',
                  'while running and _F[0] > 0:\n    _F[0] -= 1')
src = src.replace("    pump_net()",
                  "    pump_net()\n"
                  "    # 保持玩家满血（测试重点在武器/刷怪/崩溃，不是生存能力）\n"
                  "    if state == 'playing' and player:\n"
                  "        player.hp = player.max_hp\n"
                  "    # 每 300 帧补满武器\n"
                  "    if _F[0] % 300 == 0 and state == 'playing' and player:\n"
                  "        for wk in WEAPONS:\n"
                  "            player.weapons[wk] = max(player.weapons.get(wk, 0), 5)\n"
                  "    # 每 240 帧批量刷怪，制造高压场景\n"
                  "    if _F[0] % 240 == 0 and state == 'playing':\n"
                  "        for _ in range(25):\n"
                  "            e = Enemy(LEVELS[current_level], game_time)\n"
                  "            e.x = random.randint(80, WIDTH-80)\n"
                  "            e.y = random.randint(80, HEIGHT-80)\n"
                  "            enemies.append(e)\n")

class Evq:
    def __init__(self): self.q = []
    def post(self, e): self.q.append(e)
    def get(self):
        o, self.q = self.q, []
        return o

g = {'__file__': os.path.join(HERE, 'game_pygame.py'), '__name__': '__main__',
     '_evq': Evq(), '_F': [15000]}
threading.Thread(target=exec, args=(compile(src, 'gp', 'exec'), g), daemon=True).start()
time.sleep(1.5)

def tt(t):
    for c in t: g['_evq'].post(pygame.event.Event(pygame.TEXTINPUT, text=c))
def kd(k): g['_evq'].post(pygame.event.Event(pygame.KEYDOWN, key=k))
def ck(x, y): g['_evq'].post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(x, y)))

print("[1] 注册 + 登录")
tt('smoketest'); kd(pygame.K_TAB); tt('pass1234')
ck(640, 515); time.sleep(0.8)
ck(640, 455); time.sleep(1.5)
print("    state:", g.get('state'))
assert g.get('state') == 'menu', f"登录失败: {g.get('login_msg')}"

print("[2] 进入选关 -> 第1关")
ck(640, 300); time.sleep(0.6)   # 开始战斗
ck(640, 190); time.sleep(2.0)   # 第1关
# 自动选卡直到进游戏
for _ in range(15):
    if g.get('state') == 'levelup' and g.get('cards'):
        ck(400, 325); time.sleep(0.3)
    else:
        break
print("    state:", g.get('state'))
assert g.get('state') == 'playing', f"未进入游戏: {g.get('state')}"

print("[3] 跑 15000 帧（约 4 分钟游戏时间，全武器 + 时间快进）")
errors = []
kills0 = g['player'].kills
peak_e = peak_b = 0
t0 = time.time()
for i in range(60):
    time.sleep(0.5)
    if not g.get('running', True):
        errors.append('主循环意外退出')
        break
    # 持续自动选卡（避免卡在升级界面）
    if g.get('state') == 'levelup' and g.get('cards'):
        ck(400, 325)
    st = g.get('state')
    if st == 'playing':
        peak_e = max(peak_e, len(g.get('enemies', [])))
        peak_b = max(peak_b, len(g.get('bullets', [])))
    if st == 'gameover':
        print(f"    第 {i*0.5:.0f}s 游戏结束（正常结算）")
        break

kills = g['player'].kills
print(f"    实际运行: {time.time()-t0:.0f}s")
print(f"    game_time = {g.get('game_time', 0):.0f}s")
print(f"    击杀数 = {kills} (起始 {kills0})")
print(f"    敌人峰值 = {peak_e}, 子弹峰值 = {peak_b}")
print(f"    最终 state = {g.get('state')}")

print("[4] 断言")
assert not errors, f"发现错误: {errors}"
assert kills > kills0, "全武器状态下击杀数未增长 —— 武器可能没造成伤害"
assert peak_e > 5, f"敌人峰值过低({peak_e})，刷怪异常"
print("    击杀增长 OK，刷怪 OK，无崩溃")

print("[5] 对象泄漏检查")
print(f"    粒子数 = {len(g.get('particles', []))}")
print(f"    飘字数 = {len(g.get('dmg_texts', []))}")
print(f"    敌人残留 = {len(g.get('enemies', []))}")
assert len(g.get('particles', [])) < 3000, "粒子泄漏"
assert len(g.get('dmg_texts', [])) < 3000, "飘字泄漏"

g['running'] = False
try: g['net'].close()
except Exception: pass
time.sleep(0.5)
print()
print("=== 单机实战冒烟 PASS ===")
