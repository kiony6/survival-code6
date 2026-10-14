# -*- coding: utf-8 -*-
"""deploy_vps.py — 把 survival_server.py 部署到 VPS 并注册为 systemd 服务"""
import paramiko, os, sys, time

HOST, PORT, USER, PWD = "167.104.99.187", 63400, "root", "syc030924A"
REMOTE_DIR = "/opt/survival"
HERE = os.path.dirname(os.path.abspath(__file__))

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print(f"[1] SSH 连接 {HOST}:{PORT} ...")
cli.connect(HOST, port=PORT, username=USER, password=PWD, timeout=15)

def run(cmd, timeout=60):
    print(f"    $ {cmd}")
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    o = out.read().decode(errors="replace").strip()
    e = err.read().decode(errors="replace").strip()
    if o: print("      " + o.replace("\n", "\n      "))
    if e: print("      [err] " + e.replace("\n", "\n      [err] "))
    return o

print("[2] 建目录 + 上传服务器文件")
run(f"mkdir -p {REMOTE_DIR}")
sftp = cli.open_sftp()
sftp.put(os.path.join(HERE, "survival_server.py"), f"{REMOTE_DIR}/survival_server.py")
sftp.close()
print("    uploaded")

print("[3] 检查远端 python3")
run("python3 --version")

print("[4] 写 systemd 服务")
unit = """[Unit]
Description=Survival Code Game Server
After=network.target

[Service]
WorkingDirectory=/opt/survival
ExecStart=/usr/bin/python3 /opt/survival/survival_server.py
Restart=always
RestartSec=3
MemoryMax=48M
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""
sftp = cli.open_sftp()
with sftp.open("/etc/systemd/system/survival.service", "w") as f:
    f.write(unit)
sftp.close()

print("[5] 启动服务")
run("systemctl daemon-reload")
run("systemctl enable survival")
run("systemctl restart survival")
time.sleep(1.5)
run("systemctl is-active survival")
run("ss -tlnp | grep 8765 || netstat -tlnp 2>/dev/null | grep 8765")

print("[6] 防火墙放行 8765")
run("which ufw && ufw allow 8765/tcp; which firewall-cmd && firewall-cmd --add-port=8765/tcp --permanent && firewall-cmd --reload; echo fw-done")

print("[7] 服务日志")
run("journalctl -u survival -n 5 --no-pager")

cli.close()
print()
print("=== 部署完成 ===")
