import socket
import time
import subprocess
import sys

TARGET_IP = '192.168.10.159'
SHELL_PORT = 2323

def ping_test():
    try:
        res = subprocess.run(['ping', '-n', '1', '-w', '500', TARGET_IP], capture_output=True)
        return res.returncode == 0
    except Exception:
        return False

def connect_shell(timeout=180):
    print(f"[*] Starting client monitor for {TARGET_IP}:{SHELL_PORT} (up to {timeout}s)...", flush=True)
    start = time.time()
    
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        # Check TCP connect
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.8)
        try:
            res = s.connect_ex((TARGET_IP, SHELL_PORT))
            if res == 0:
                print(f"\n[+] SUCCESS! Connected to {TARGET_IP}:{SHELL_PORT} after {elapsed}s!", flush=True)
                # Disable watchdog immediately!
                s.sendall(b"touch /tmp/stay\n")
                time.sleep(0.5)
                print("[+] Watchdog cancelled: sent 'touch /tmp/stay'!", flush=True)
                
                # Run diagnostic commands
                for cmd in ["uname -a", "cat /proc/uptime", "grep -E '\[7|Auto-Tuning|LOCKED|SUCCESS|JACKPOT' /log/boot.log", "ifconfig eth0", "ls -la /newroot_rw"]:
                    print(f"\n--- [CMD: {cmd}] ---", flush=True)
                    s.sendall((cmd + "\n").encode('utf-8'))
                    time.sleep(0.5)
                    data = b""
                    s.settimeout(1.0)
                    while True:
                        try:
                            chunk = s.recv(4096)
                            if not chunk: break
                            data += chunk
                            if len(chunk) < 4096: break
                        except socket.timeout:
                            break
                    print(data.decode('utf-8', errors='replace').strip(), flush=True)
                
                s.close()
                return True
        except Exception:
            pass
        finally:
            s.close()
        
        if elapsed % 5 == 0:
            is_ping = ping_test()
            print(f"[{elapsed:03d}s] ping={'OK' if is_ping else 'TIMEOUT'}, port {SHELL_PORT} waiting...", flush=True)
        time.sleep(1)
        
    print(f"\n[-] Timed out after {timeout}s.", flush=True)
    return False

if __name__ == '__main__':
    connect_shell(180)
