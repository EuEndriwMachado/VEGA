# -*- coding: utf-8 -*-
"""SSDP scan correto (bind na porta 1900 + membership multicast)."""
import socket

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(6)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("", 1900))
except OSError as e:
    print("bind:", e)
try:
    s.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        socket.inet_aton("239.255.255.250") + socket.inet_aton("192.168.15.7"),
    )
except OSError as e:
    print("join:", e)

msg = (
    b"M-SEARCH * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\n"
    b'MAN: "ssdp:discover"\r\nMX:3\r\nST:ssdp:all\r\n\r\n'
)
s.sendto(msg, ("239.255.255.250", 1900))
try:
    while True:
        d, a = s.recvfrom(8192)
        text = d.decode("utf-8", "ignore").replace("\r\n", " | ")
        print(a[0], "|", text[:250])
except socket.timeout:
    print("fim")
