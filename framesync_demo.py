# -*- coding: utf-8 -*-
"""
framesync_demo.py — 帧同步 vs 状态同步 对比演示
=============================================
在一台电脑上用两个窗口模拟本地双人，让你亲自感受两种同步方式的差别。

操作：
  WASD 移动蓝色角色（同步方式按 Tab 切换）
  方向键移动红色角色（模拟远程玩家的输入）
  Tab 键切换：状态同步 -> 帧同步 -> 状态同步 ...
"""

import pygame
import math
import random
import time as _time

WIDTH, HEIGHT = 900, 500
FPS = 60
DT = 1.0 / FPS

# 颜色
BLACK = (10, 10, 18)
BLUE = (0, 200, 255)
RED = (255, 60, 80)
GREEN = (80, 255, 120)
GRAY = (80, 80, 100)
WHITE = (255, 255, 255)
YELLOW = (255, 220, 80)

pygame.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("帧同步 vs 状态同步 演示 (Tab 切换)")
font = pygame.font.Font(None, 28)
font_sm = pygame.font.Font(None, 18)

# ============ 帧同步引擎 ============
class FrameSync:
    """帧同步核心：所有客户端各自跑完整的确定性模拟。
    每帧只同步「输入」，不做状态回滚。输入定长、按帧对齐。"""
    def __init__(self):
        self.frame = 0
        self.input_history = []    # [(frame, input_dict), ...]
        self.players = {}          # id -> Player
        self.bullets = []
        self.seed = 42
        self.rng = random.Random(self.seed)

    def register_player(self, pid, x, y):
        self.players[pid] = {"x": x, "y": y, "hp": 100, "w": 16, "h": 16}

    def apply_input(self, pid, inp):
        """接收远程玩家的输入，按帧存起来"""
        self.input_history.append((self.frame, pid, inp))

    def tick(self, inputs):
        """一帧更新：所有客户端必须用完全相同的 inputs 调用此函数。
        如果 inputs 不同步，后续状态立即分裂。"""
        self.frame += 1
        # 1) 移动（确定性）
        for pid, inp in inputs.items():
            p = self.players.get(pid)
            if not p:
                continue
            dx, dy = inp
            spd = 200 * DT
            p["x"] += dx * spd
            p["y"] += dy * spd
            p["x"] = max(10, min(WIDTH - 10, p["x"]))
            p["y"] = max(10, min(HEIGHT - 10, p["y"]))
        # 2) 射击（确定性：所有客户端用同一个伪随机种子自嗨）
        for pid, inp in inputs.items():
            if inp[0] or inp[1]:
                if self.rng.random() < 0.04:   # 每帧 4% 概率开火（所有客户端相同）
                    p = self.players[pid]
                    ang = math.atan2(inp[1], inp[0]) if inp[0] or inp[1] else self.rng.uniform(0, 6.28)
                    ang += self.rng.uniform(-0.3, 0.3)
                    spd = 400
                    self.bullets.append({
                        "x": p["x"], "y": p["y"],
                        "vx": math.cos(ang) * spd, "vy": math.sin(ang) * spd,
                        "life": 20,
                    })
        # 3) 子弹更新（确定性）
        for b in self.bullets:
            b["x"] += b["vx"] * DT
            b["y"] += b["vy"] * DT
            b["life"] -= 1
        self.bullets = [b for b in self.bullets if b["life"] > 0]


class P2PStateSync:
    """状态同步（即你现在的方案）：
    本地全量模拟，远程只同步坐标快照。
    这里用"延迟模拟"来模拟网络延迟。"""
    def __init__(self):
        self.players = {}
        self.bullets = []
        self.remote_snapshot = {}

    def register_player(self, pid, x, y):
        self.players[pid] = {"x": x, "y": y, "hp": 100, "w": 16, "h": 16}

    def local_tick(self, inputs):
        """本地全量更新（模拟房主权威）"""
        for pid, inp in inputs.items():
            p = self.players.get(pid)
            if not p:
                continue
            dx, dy = inp
            spd = 200 * DT
            p["x"] += dx * spd
            p["y"] += dy * spd
            p["x"] = max(10, min(WIDTH - 10, p["x"]))
            p["y"] = max(10, min(HEIGHT - 10, p["y"]))
            if inp[0] or inp[1]:
                if random.random() < 0.04:
                    ang = math.atan2(inp[1], inp[0]) if inp[0] or inp[1] else random.uniform(0, 6.28)
                    self.bullets.append({"x": p["x"], "y": p["y"], "vx": math.cos(ang) * 400, "vy": math.sin(ang) * 400, "life": 20})
        for b in self.bullets:
            b["x"] += b["vx"] * DT
            b["y"] += b["vy"] * DT
            b["life"] -= 1
        self.bullets = [b for b in self.bullets if b["life"] > 0]

    def send_snapshot(self):
        """模拟发送快照到远端"""
        p = self.players.get(0)
        return {"x": p["x"], "y": p["y"]} if p else {}

    def receive_snapshot(self, snap):
        """模拟收到远端快照（不插值的话位置会跳）"""
        self.remote_snapshot = snap


mode = "framesync"   # framesync / statesync
fs = FrameSync()
ss = P2PStateSync()

# 两个玩家
fs.register_player(0, 200, 250)
fs.register_player(1, 700, 250)
ss.register_player(0, 200, 250)
ss.register_player(1, 700, 250)

running = True
clock = pygame.time.Clock()
last_toggle = 0

while running:
    dt = clock.tick(FPS) / 1000.0
    inputs_local = {}      # 本地玩家输入
    inputs_remote = {}     # 远端玩家输入（模拟网络来的）

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_TAB:
            now = _time.time()
            if now - last_toggle > 0.3:
                mode = "framesync" if mode == "statesync" else "statesync"
                last_toggle = now

    # 收集本地玩家（WASD）输入
    keys = pygame.key.get_pressed()
    dx = (keys[pygame.K_d] - keys[pygame.K_a])
    dy = (keys[pygame.K_s] - keys[pygame.K_w])
    inputs_local[0] = (dx, dy)

    # 远端玩家输入：方向键模拟远程玩家的原始操作（帧同步用）
    rx = (keys[pygame.K_RIGHT] - keys[pygame.K_LEFT])
    ry = (keys[pygame.K_DOWN] - keys[pygame.K_UP])
    inputs_remote[1] = (rx, ry)
    inputs_local[1] = (rx, ry)        # 帧同步：本地也要用同一份输入

    # ===== 两种同步方式 =====
    if mode == "framesync":
        # 帧同步：所有客户端用完全相同的 inputs
        fs.tick(inputs_local)
        # 两个玩家都在 fs 里直接渲染
        p0 = fs.players[0]
        p1 = fs.players[1]
    else:
        # 状态同步：本地玩家直接移动，远端玩家走延迟快照
        ss.local_tick(inputs_local)

        # 模拟网络延迟：第 1 帧 snap 还没到；第 2 帧收到第 1 帧数据
        snap = ss.send_snapshot()
        if hasattr(ss, "_prev_snap"):
            # 用上一次的快照渲染远端玩家
            rp = ss._prev_snap
            ss.players[1]["x"] = rp["x"]
            ss.players[1]["y"] = rp["y"]
        ss._prev_snap = ss.send_snapshot()

        p0 = ss.players[0]
        p1 = ss.players[1]

    # ===== 渲染 =====
    screen.fill(BLACK)

    # 地图网格
    for gx in range(0, WIDTH, 60):
        pygame.draw.line(screen, (25, 28, 42), (gx, 0), (gx, HEIGHT), 1)
    for gy in range(0, HEIGHT, 60):
        pygame.draw.line(screen, (25, 28, 42), (0, gy), (WIDTH, gy), 1)

    # 子弹
    bullist = fs.bullets if mode == "framesync" else ss.bullets
    for b in bullist:
        pygame.draw.circle(screen, YELLOW, (int(b["x"]), int(b["y"])), 3)

    # 玩家 0（本地）
    pygame.draw.rect(screen, BLUE, (p0["x"] - 8, p0["y"] - 8, 16, 16))
    pygame.draw.rect(screen, (60, 240, 255), (p0["x"] - 8, p0["y"] - 8, 16, 16), 2)
    tag = font_sm.render("本地", True, (100, 220, 255))
    screen.blit(tag, (p0["x"] - 20, p0["y"] - 28))

    # 玩家 1（远端模拟）
    color = GREEN if mode == "framesync" else (RED[0], RED[1] - 40, RED[2])
    label = "远端(帧同步)" if mode == "framesync" else "远端(状态同步)"
    pygame.draw.rect(screen, color, (p1["x"] - 8, p1["y"] - 8, 16, 16))
    pygame.draw.rect(screen, (255, 255, 255), (p1["x"] - 8, p1["y"] - 8, 16, 16), 2)
    tag2 = font_sm.render(label, True, (180, 220, 180))
    screen.blit(tag2, (p1["x"] - 36, p1["y"] - 28))

    # 状态信息
    mode_name = "帧同步 (Frame Sync)" if mode == "framesync" else "状态同步 (State Sync)"
    mode_color = GREEN if mode == "framesync" else ORANGE if "ORANGE" in dir() else (255, 200, 120)
    t = font.render(f"当前: {mode_name}   按 Tab 切换", True, mode_color)
    screen.blit(t, (20, 20))

    # 两种同步的差异说明
    if mode == "framesync":
        exp = [
            "✓ 远端角色和你同时移动，没有延迟",
            "✓ 双方子弹完全一致（伪随机同步）",
            "✓ 原理：所有客户端跑同一份确定性代码",
        ]
    else:
        exp = [
            "✗ 远端角色有 1 帧延迟（模拟网络快照）",
            "✗ 远端可能丢子弹（快照丢细节）",
            "✗ 原理：房主算好了再广播给你",
        ]
    for i, line in enumerate(exp):
        c = (200, 230, 200) if mode == "framesync" else (230, 180, 180)
        t = font_sm.render(line, True, c)
        screen.blit(t, (20, 55 + i * 24))

    # 操作提示
    t = font_sm.render(
        "WASD 移动蓝色 · 方向键模拟远端输入 · Tab 切换同步方式",
        True, (120, 130, 160))
    screen.blit(t, (20, HEIGHT - 30))

    pygame.display.flip()

pygame.quit()
