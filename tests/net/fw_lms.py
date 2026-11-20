"""A fake Lyrion server on the LAN for the live firewall test (scripts/run_net.sh): answers discovery requests on UDP 3483 like LMS
(from port 3483 to the sender's address and port) until it is stopped. Prints every request it answered."""
import socket

NAME = b"Lab Lyrion"
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
s.bind(("0.0.0.0", 3483))
print("ready", flush=True)
while True:
    data, addr = s.recvfrom(2048)
    if data.startswith(b"e"):
        s.sendto(b"E" + b"NAME" + bytes([len(NAME)]) + NAME + b"JSON\x049000", addr)
        print(f"answered discovery from {addr[0]}:{addr[1]}", flush=True)
