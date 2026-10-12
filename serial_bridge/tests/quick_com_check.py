"""Quick check: COM data should have NO ANSI color wrapping."""
import socket, time, sys

def recv_all(sock, timeout=0.5):
    sock.settimeout(timeout)
    chunks = []
    try:
        while True:
            d = sock.recv(4096)
            if not d: break
            chunks.append(d)
    except socket.timeout: pass
    return b"".join(chunks)

c = socket.socket()
c.connect(("127.0.0.1", 9999))
time.sleep(0.3)
welcome = recv_all(c)

print("=== Welcome (first line only) ===")
first = welcome.split(b"\r\n")[0]
print(first.decode("utf-8", errors="replace").replace("\033", "<ESC>"))

c.send(b"test123\r\n")
time.sleep(0.4)
echo = recv_all(c)

print("\n=== Echo data ===")
text = echo.decode("utf-8", errors="replace").replace("\033", "<ESC>")
for line in text.split("\r\n"):
    if line.strip():
        print(f"  |{line}|")

green = b"\033[0;32m" in echo
client_color = b"\033[0;33mtest123" in echo or b"\033[0;36mtest123" in echo

print(f"\n=== Verdict ===")
print(f"  Client echo colored: {client_color} (expected: True)")
print(f"  COM data has green:  {green} (expected: False)")
print(f"  => {'PASS' if client_color and not green else 'FAIL'}")

c.close()
sys.exit(0 if (client_color and not green) else 1)
