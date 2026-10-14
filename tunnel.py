# -*- coding: utf-8 -*-
"""
tunnel.py — SSH 本地端口转发（云安全组没放行 8765 时的应急通道）
================================================================
用法：python tunnel.py
保持窗口开着，游戏客户端照常连 127.0.0.1:8765。
（正式方案还是去云控制台放行 8765，放行后本脚本可以不用）
"""
import paramiko, socketserver, threading, sys

HOST, PORT, USER, PWD = "167.104.99.187", 63400, "root", "syc030924A"
LOCAL_PORT = 8765
REMOTE_TARGET = ("127.0.0.1", 8765)

transport = paramiko.Transport((HOST, PORT))
print(f"[隧道] 连接 {HOST}:{PORT} ...")
transport.connect(username=USER, password=PWD)
print(f"[隧道] 已连接，本地 :{LOCAL_PORT} -> VPS:{REMOTE_TARGET[1]}")

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            chan = transport.open_channel("direct-tcpip", REMOTE_TARGET,
                                          self.client_address)
        except Exception as e:
            print("[隧道] 通道失败:", e)
            return
        if chan is None:
            return
        import select
        try:
            while True:
                r, _, _ = select.select([self.request, chan], [], [], 30)
                if self.request in r:
                    data = self.request.recv(8192)
                    if not data: break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(8192)
                    if not data: break
                    self.request.sendall(data)
        except Exception:
            pass
        finally:
            chan.close()

class Srv(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

try:
    srv = Srv(("127.0.0.1", LOCAL_PORT), Handler)
    print(f"[隧道] 监听 127.0.0.1:{LOCAL_PORT}，Ctrl+C 退出")
    srv.serve_forever()
except KeyboardInterrupt:
    print("\n[隧道] 退出")
finally:
    transport.close()
