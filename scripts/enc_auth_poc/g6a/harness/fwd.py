"""Tiny TCP forwarder used by the G6a lab. FWD_MAP="<listen-port>=<host>:<port>,..." (e.g. "9099=host.docker.internal:9099").
It only ever connects to the fixed targets named in FWD_MAP; it logs nothing about the traffic."""
import os
import socket
import sys
import threading
import time


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def serve(listen_port, host, port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", listen_port))
    server.listen(256)
    while True:
        client, _ = server.accept()
        upstream = None
        for attempt in range(3):                       # a burst of connections can make the first attempt fail: retry briefly
            try:
                upstream = socket.create_connection((host, port), timeout=10)
                break
            except OSError:
                time.sleep(0.2 * (attempt + 1))
        if upstream is None:
            client.close()
            continue
        threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
        threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()


def main():
    for item in os.environ["FWD_MAP"].split(","):
        listen, target = item.split("=")
        host, port = target.rsplit(":", 1)
        threading.Thread(target=serve, args=(int(listen), host, int(port)), daemon=True).start()
    threading.Event().wait()


if __name__ == "__main__":
    sys.exit(main())
