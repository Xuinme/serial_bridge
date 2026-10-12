"""
Manual echo test: connect 2 TCP clients, verify real-time echo + color behavior.
Run this AFTER starting the bridge: python main.py --cli COM9 115200 9999
"""
import socket
import time

PORT = 9999
HOST = "127.0.0.1"

def recv_all(sock, timeout=0.5):
    sock.settimeout(timeout)
    chunks = []
    try:
        while True:
            d = sock.recv(4096)
            if not d:
                break
            chunks.append(d)
    except socket.timeout:
        pass
    return b"".join(chunks)

def show_data(label, data):
    """Show hex + ASCII for debugging."""
    text = data.decode("utf-8", errors="replace")
    # Replace escape codes for display
    display = text.replace("\033", "<ESC>")
    print(f"  [{label}] ({len(data)} bytes): {display[:200]}")

def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"  {status} {name} {detail}")

print("=" * 60)
print("Manual Echo Test — 2 clients connected simultaneously")
print("=" * 60)

# ── Connect both clients ────────────────────────────────────
print("\n── Connecting Client-1 (User/MobaXterm) ──")
c1 = socket.socket()
c1.connect((HOST, PORT))
time.sleep(0.3)
w1 = recv_all(c1)
show_data("C1 welcome", w1)

print("\n── Connecting Client-2 (AI Agent) ──")
c2 = socket.socket()
c2.connect((HOST, PORT))
time.sleep(0.3)
w2 = recv_all(c2)
show_data("C2 welcome", w2)

all_pass = True

# Extract actual colors from welcome banners
import re
def get_color(data):
    m = re.search(rb"\033\[0;(\d+)m", data)
    return m.group(1).decode() if m else "?"

c1_color_code = get_color(w1)
c2_color_code = get_color(w2)
print(f"  C1 color code: {c1_color_code}, C2 color code: {c2_color_code}")

c1_color_pat = f"\033[0;{c1_color_code}m".encode()
c2_color_pat = f"\033[0;{c2_color_code}m".encode()

# ── Test 1: Welcome has IAC negotiation ─────────────────────
print("\n── Test 1: Welcome IAC negotiation ──")
has_iac_1 = b"\xff" in w1
has_iac_2 = b"\xff" in w2
check("C1 welcome has IAC WILL ECHO+SGA", has_iac_1)
check("C2 welcome has IAC WILL ECHO+SGA", has_iac_2)
all_pass &= has_iac_1 and has_iac_2

# ── Test 2: Client-1 sends, sees own echo ───────────────────
print("\n── Test 2: C1 sends, checks self-echo ──")
c1.send(b"Hello from Client-1\r\n")
time.sleep(0.3)

echo_1 = recv_all(c1)
show_data("C1 self-echo", echo_1)
check("C1 sees own echo", b"Hello from Client-1" in echo_1)
check(f"C1 echo has C1 color ({c1_color_code})", c1_color_pat in echo_1)
all_pass &= b"Hello from Client-1" in echo_1

# ── Test 3: Client-2 sees C1's message ──────────────────────
see_2 = recv_all(c2)
show_data("C2 sees C1", see_2)
check("C2 sees C1 message", b"Hello from Client-1" in see_2)
check(f"C2 sees C1 in C1 color ({c1_color_code})", c1_color_pat in see_2)
all_pass &= b"Hello from Client-1" in see_2

# ── Test 4: Client-2 sends, sees own echo ───────────────────
print("\n── Test 4: C2 sends, checks self-echo ──")
c2.send(b"Hello from Client-2\r\n")
time.sleep(0.3)

echo_2 = recv_all(c2)
show_data("C2 self-echo", echo_2)
check("C2 sees own echo", b"Hello from Client-2" in echo_2)
check(f"C2 echo has C2 color ({c2_color_code})", c2_color_pat in echo_2)
all_pass &= b"Hello from Client-2" in echo_2

# ── Test 5: Client-1 sees C2's message ──────────────────────
see_1 = recv_all(c1)
show_data("C1 sees C2", see_1)
check("C1 sees C2 message", b"Hello from Client-2" in see_1)
check(f"C1 sees C2 in C2 color ({c2_color_code})", c2_color_pat in see_1)
all_pass &= b"Hello from Client-2" in see_1

# ── Test 6: Color differentiation ───────────────────────────
print("\n── Test 6: Color differentiation ──")
check(f"C1+C2 have different colors", c1_color_code != c2_color_code,
      f"C1={c1_color_code}, C2={c2_color_code}")
all_c1_data = w1 + echo_1 + see_1
all_c2_data = w2 + see_2 + echo_2
check(f"C1 has own color ({c1_color_code})", c1_color_pat in all_c1_data)
check(f"C1 has C2 color ({c2_color_code})", c2_color_pat in all_c1_data)
check(f"C2 has own color ({c2_color_code})", c2_color_pat in all_c2_data)
check(f"C2 has C1 color ({c1_color_code})", c1_color_pat in all_c2_data)

# ── Test 7: Multi-line exchange ─────────────────────────────
print("\n── Test 7: Multi-line exchange ──")
c1.send(b"CMD_1: first command\r\n")
time.sleep(0.2)
c2.send(b"CMD_2: second command\r\n")
time.sleep(0.3)

ex1 = recv_all(c1)
ex2 = recv_all(c2)
check("C1 sees both commands", b"CMD_1" in ex1 and b"CMD_2" in ex1)
check("C2 sees both commands", b"CMD_1" in ex2 and b"CMD_2" in ex2)
all_pass &= b"CMD_1" in ex1 and b"CMD_2" in ex1
all_pass &= b"CMD_1" in ex2 and b"CMD_2" in ex2

# ── Cleanup ─────────────────────────────────────────────────
c1.close()
c2.close()

print("\n" + "=" * 60)
if all_pass:
    print("ALL CHECKS PASSED — Echo + color differentiation works!")
else:
    print("SOME CHECKS FAILED — See above")
print("=" * 60)
