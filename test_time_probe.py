#!/usr/bin/env python3
import socket
import struct
import time
import sys

MAC = "41:42:6C:C2:DD:3C"

def main():
    print(f"Connecting to {MAC} via L2CAP ATT...")
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.settimeout(6.0)
    s.bind((socket.BDADDR_ANY, 0, 4, 1))
    s.connect((MAC, 0, 4, 1))
    print("Connected!")

    # Check MTU exchange
    s.settimeout(1.5)
    try:
        rx = s.recv(512)
        if rx and rx[0] == 0x02: # ATT_OP_MTU_REQ
            peer_mtu = struct.unpack_from("<H", rx, 1)[0]
            s.send(struct.pack("<BH", 0x03, 512))
            print(f"MTU exchange: peer {peer_mtu}, replied 512")
    except Exception as e:
        print(f"No MTU req: {e}")

    # Send MTU req
    s.send(struct.pack("<BH", 0x02, 512))
    try:
        mtu_rsp = s.recv(512)
        if mtu_rsp and mtu_rsp[0] == 0x03:
            att_mtu = struct.unpack_from("<H", mtu_rsp, 1)[0]
            print(f"ATT MTU agreed: {att_mtu}")
    except Exception:
        pass

    # Enable notifications on 0xFF18 (CCCD handle 0x0004)
    s.send(struct.pack("<BHH", 0x12, 0x0004, 0x0001))
    try:
        rsp = s.recv(512)
        print(f"CCCD write rsp: {rsp.hex()}")
    except Exception as e:
        print(f"CCCD err: {e}")

    # Test 1: Send query 0x27 (Device Info) with WRITE_CMD (0x52)
    # like upload_watchface does:
    info_pkt = bytes([0x00, 0x27, 0x01, 0x00, 0x02, 0xFF, 0x00])
    s.send(struct.pack("<BH", 0x52, 0x0008) + info_pkt)
    time.sleep(0.1)
    info_pkt2 = bytes([0x00, 0x27, 0x01, 0x00, 0x02, 0x80, 0x00])
    s.send(struct.pack("<BH", 0x52, 0x0008) + info_pkt2)

    s.settimeout(1.0)
    for _ in range(4):
        try:
            notif = s.recv(512)
            if notif and notif[0] == 0x1B:
                print(f"Received notification: {notif[3:].hex()}")
        except socket.timeout:
            break

    # Now let's test JieLi sys_time format!
    # Target date: Today 2026-10-27, Current local time (e.g. 16:55:00)
    # Year: 2026 (0x07EA), Month: 10, Day: 27, Hour: 16 (0x10), Min: 55 (0x37), Sec: 0
    year = 2026
    month = 10
    day = 27
    hour = 16
    minute = 55
    second = 0

    print(f"\nTarget setting: {year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}")

    # Format A: 7 bytes JieLi sys_time Little-Endian:
    # <HBBBBB -> [EA, 07, 0A, 1B, 10, 37, 00]
    payload_sys_time_le = struct.pack("<HBBBBB", year, month, day, hour, minute, second)
    pkt_sys_time_le = bytes([0x00, 0x93, 0x01, 0x00, len(payload_sys_time_le)]) + payload_sys_time_le

    print(f"\n--- 1. Testing JieLi sys_time (7 bytes LE): {pkt_sys_time_le.hex()} ---")
    s.send(struct.pack("<BH", 0x12, 0x0008) + pkt_sys_time_le)
    rsp = s.recv(512)
    print(f"ATT write rsp handle 0x0008: {rsp.hex()}")
    # Also write to handle 0x0006
    s.send(struct.pack("<BH", 0x12, 0x0006) + pkt_sys_time_le)
    rsp = s.recv(512)
    print(f"ATT write rsp handle 0x0006: {rsp.hex()}")
    # And write cmd
    s.send(struct.pack("<BH", 0x52, 0x0008) + pkt_sys_time_le)

    time.sleep(0.5)
    s.settimeout(0.8)
    while True:
        try:
            notif = s.recv(512)
            if notif and notif[0] == 0x1B:
                print(f"Notification received: {notif[3:].hex()}")
        except socket.timeout:
            break

    # Format B: 7 bytes JieLi sys_time Big-Endian:
    payload_sys_time_be = struct.pack(">HBBBBB", year, month, day, hour, minute, second)
    pkt_sys_time_be = bytes([0x00, 0x93, 0x01, 0x00, len(payload_sys_time_be)]) + payload_sys_time_be
    print(f"\n--- 2. Testing JieLi sys_time (7 bytes BE): {pkt_sys_time_be.hex()} ---")
    s.send(struct.pack("<BH", 0x12, 0x0008) + pkt_sys_time_be)
    rsp = s.recv(512)
    print(f"ATT write rsp: {rsp.hex()}")
    s.send(struct.pack("<BH", 0x52, 0x0008) + pkt_sys_time_be)

    # Format C: 6 bytes (YY, MM, DD, HH, MM, SS):
    payload_6b = bytes([year % 100, month, day, hour, minute, second])
    pkt_6b = bytes([0x00, 0x93, 0x01, 0x00, len(payload_6b)]) + payload_6b
    print(f"\n--- 3. Testing 6 bytes (YY MM DD HH MM SS): {pkt_6b.hex()} ---")
    s.send(struct.pack("<BH", 0x12, 0x0008) + pkt_6b)
    rsp = s.recv(512)
    print(f"ATT write rsp: {rsp.hex()}")
    s.send(struct.pack("<BH", 0x52, 0x0008) + pkt_6b)

    # Format D: 8 bytes (year, month, day, hour, minute, second, weekday):
    # weekday: 1 = Monday, 2 = Tuesday
    payload_8b = struct.pack("<HBBBBBB", year, month, day, hour, minute, second, 2)
    pkt_8b = bytes([0x00, 0x93, 0x01, 0x00, len(payload_8b)]) + payload_8b
    print(f"\n--- 4. Testing 8 bytes (with weekday): {pkt_8b.hex()} ---")
    s.send(struct.pack("<BH", 0x12, 0x0008) + pkt_8b)
    rsp = s.recv(512)
    print(f"ATT write rsp: {rsp.hex()}")
    s.send(struct.pack("<BH", 0x52, 0x0008) + pkt_8b)

    # Query info again to wake UI loop
    s.send(struct.pack("<BH", 0x52, 0x0008) + bytes([0x00, 0x27, 0x01, 0x00, 0x02, 0x01, 0x00]))
    time.sleep(0.5)

    s.close()
    print("\nAll formats transmitted! Check the box screen now!")

if __name__ == "__main__":
    main()
