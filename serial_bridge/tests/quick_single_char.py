"""Quick check: single char 'l' should NOT appear twice."""
import socket, time, sys

c = socket.socket()
c.connect(("127.0.0.1", 9999))
time.sleep(0.2)
c.recv(4096)  # drain welcome

c.send(b"l\r\n")
time.sleep(0.4)
echo = c.recv(4096)

text = echo.decode("utf-8", errors="replace").replace("\033", "<ESC>")
print("=== Received data ===")
for line in text.split("\r\n"):
    if line.strip():
        print(f"  |{line}|")

# Count occurrences of "l" in the raw data (excluding ANSI codes)
# Strip ANSI to count visible "l" characters
import re
visible = re.sub(r'\x1b\[[0-9;]*m', '', text)
l_count = visible.count("ll")  # check for double-l

print(f"\n=== Analysis ===")
print(f"  Visible text: {repr(visible[:200])}")
print(f"  'll' occurrences: {l_count}")
print(f"  => {'PASS: no double l' if l_count == 0 else 'FAIL: double l detected!'}")

c.close()
sys.exit(0 if l_count == 0 else 1)
