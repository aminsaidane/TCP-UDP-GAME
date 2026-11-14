"""Network check: run it on a player PC before the demo.

    python -m client.check                 # find the server by LAN broadcast, then test it
    python -m client.check 192.168.1.12    # test this server address

It checks, one by one, everything the game needs from the network and says
what to fix when something fails (usually the firewall on the server PC).
No account and no pygame needed.
"""
import queue
import socket
import sys
import time

from common import config
from common import protocol as proto
from common.net import local_addresses
from client.network import parse_address
from client.udp import discover

OK, FAIL, INFO = "  [ OK ] ", "  [FAIL] ", "  [ .. ] "


def check_discovery():
    print("2. LAN discovery (UDP broadcast to port %d)" % config.DISCOVERY_PORT)
    inbox = queue.Queue()
    discover(inbox, timeout=2.0)
    servers = inbox.get(timeout=5)["servers"]
    if servers:
        for s in servers:
            print(OK + f"server '{s['name']}' answered from {s['host']} (TCP port {s['tcp_port']})")
    else:
        print(FAIL + "no server answered the broadcast")
        print("         -> is the server running? same Wi-Fi/hotspot? UDP %d allowed on the server PC?"
              % config.DISCOVERY_PORT)
        print("         -> some networks (university Wi-Fi) block broadcasts: type the IP instead")
    return servers


def check_tcp(host, port):
    print(f"3. TCP connection to {host}:{port}")
    start = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout=3)
    except socket.timeout:
        print(FAIL + "no answer after 3 s: the packets are dropped on the way")
        print("         -> on the server PC, allow TCP %d in the firewall (see DEMO.md)" % port)
        return False
    except OSError as exc:
        print(FAIL + f"{exc.strerror or exc}")
        print("         -> 'refused' means the PC is reachable but the server is not running on that port")
        return False
    ms = (time.perf_counter() - start) * 1000
    print(OK + f"connected (3-way handshake took {ms:.1f} ms)")
    conn = proto.Connection(sock)
    sock.settimeout(3)
    try:
        conn.send(proto.make("list_rooms"))
        reply = conn.recv()
        if reply and reply.get("type") == "error":
            print(OK + "the Pablo server answered over TCP")
    except (OSError, proto.ProtocolError):
        print(FAIL + "connected, but no Pablo answer: is something else using this port?")
        return False
    finally:
        conn.close()
    return True


def check_udp(host):
    port = config.DISCOVERY_PORT
    print(f"4. UDP round trip to {host}:{port}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    times = []
    for _ in range(5):
        start = time.perf_counter()
        sock.sendto(proto.encode_datagram(proto.make("discover", game="pablo")), (host, port))
        try:
            sock.recvfrom(proto.MAX_DATAGRAM + 1)
            times.append((time.perf_counter() - start) * 1000)
        except socket.timeout:
            pass
    sock.close()
    if times:
        print(OK + f"{len(times)}/5 UDP packets answered, round trip {min(times):.1f}-{max(times):.1f} ms")
        if len(times) < 5:
            print(INFO + f"{5 - len(times)} lost: normal for UDP on a busy Wi-Fi")
        return True
    print(FAIL + "no UDP answer: UDP is blocked")
    print("         -> on the server PC, allow UDP %d-%d in the firewall (see DEMO.md)"
          % (config.UDP_PORT, config.DISCOVERY_PORT))
    print("         -> the game still works over TCP, but without discovery, live timer and hover")
    return False


def main():
    print("Pablo network check\n")
    print("1. This PC")
    for ip in local_addresses() or ["(no network address found)"]:
        print(INFO + ip)

    servers = check_discovery()
    if len(sys.argv) > 1:
        host, port = parse_address(sys.argv[1])
    elif servers:
        host, port = servers[0]["host"], servers[0]["tcp_port"]
    else:
        print("\nGive the server IP to continue:  python -m client.check 192.168.x.x")
        return 1
    tcp_ok = check_tcp(host, port)
    udp_ok = check_udp(host)

    print()
    if tcp_ok and udp_ok:
        print(f"All good: start the game with  python -m client.main --server {host}")
        return 0
    if tcp_ok:
        print("The game will work (TCP is fine), but UDP is blocked: fix the firewall for the full demo.")
        return 0
    print("The game cannot connect yet: fix the TCP problem above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
