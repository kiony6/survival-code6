# -*- coding: utf-8 -*-
"""drive_test.py — 无头驱动测试 game_pygame.py 完整流程"""
import os, sys
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
import pygame
pygame.init()
pygame.display.set_mode((100, 100))

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, 'game_pygame.py'), encoding='utf-8').read()

# 注入帧预算 + 事件驱动
prologue = """
MAX_FRAMES = 15000
_frame = [0]
_pending = []
_orig_evget = pygame.event.get
def _patched_get(*a, **k):
    evs = list(_orig_evget(*a, **k))
    return _pending + evs
pygame.event.get = _patched_get
"""
src = src.replace('while running:', prologue + 'while running and _frame[0] < MAX_FRAMES:\n    _frame[0] += 1')

inject = """
    dt = 0.016
    # 帧1: 点「开始战斗」  帧5: 点第1关
    if _frame[0] == 1:
        _pending.append(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(640, 325)))
    if _frame[0] == 5:
        _pending.append(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(640, 190)))
    # 升级界面自动选第一张卡
    if state == 'levelup' and cards:
        _pending.append(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1,
                        pos=(WIDTH//2-350+110, 325)))
    # 给玩家塞满武器，强制触发各种组合技（黑洞/陨石/天罚/风火轮/时间凝滞/超频）
    if _frame[0] == 300 and player is not None:
        for wid in list(WEAPONS.keys()):
            player.weapons[wid] = 3
    # 快进：每 200 帧 +90 秒，尽快到 boss 时间
    if _frame[0] % 200 == 0:
        game_time += 90
    # 玩家无敌，保证流程跑完（仅在游戏中且玩家存在时）
    if player is not None:
        player.hp = player.max_hp
"""
src = src.replace('    dt = clock.tick(FPS) / 1000.0', inject)
src = src.replace('pygame.quit()', '')

g = {'__file__': os.path.join(HERE, 'game_pygame.py'), '__name__': '__main__'}
exec(compile(src, 'game_pygame.py', 'exec'), g)

p = g['player']
boss = g['boss']
print(f"帧数={g['_frame'][0]} state={g['state']}")
print(f"玩家: lv={p.level} 击杀={p.kills} 武器数={len(p.weapons)}")
print(f"游戏时间={g['game_time']:.0f}s 敌人={len(g['enemies'])}")
print(f"boss={boss.name if boss else None} hp={boss.hp if boss else 0:.0f}")
print(f"子弹={len(g['bullets'])} 黑洞={len(g['blackholes'])} 陨石={len(g['meteors'])}")
print(f"combo触发过: timewarp={g['time_warp_active']:.1f}")
# 检查关键断言（本测试把时间快进到 6760s，敌人 HP 极高，
# 击杀数低是测试场景使然，不是游戏 bug；重点验证不崩溃 + boss 可被击杀）
assert p.kills >= 10, "应有击杀"
assert g['game_time'] > 100, "时间应推进"
assert boss is not None and boss.hp <= 0, "boss 应能被击杀（find_nearest_enemy 修复生效）"
assert p.level >= 5, "升级与卡牌选择链路应工作"
print("PASS: 完整流程 15000 帧无崩溃，boss 可击杀，升级链路正常")
