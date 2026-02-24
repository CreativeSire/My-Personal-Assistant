"""
Project Sentinel
System Security & Anomaly Detection Daemon.
"""

import time
import os
import psutil # Requires: pip install psutil
from rich.console import Console
from victor_os.voice_of_god import VoiceOfGod # For critical alerts

console = Console()
voice = VoiceOfGod()

SAFE_PROCESSES = {"python.exe", "explorer.exe", "chrome.exe", "svchost.exe", "System Idle Process"}

def scan_system():
    console.print("[bold blue]Sentinel: Scanning...[/bold blue]")
    
    suspicious = []
    
    # Process Scan
    for proc in psutil.process_iter(['pid', 'name', 'username']):
        try:
            name = proc.info['name']
            if name not in SAFE_PROCESSES and "malware" in name.lower(): # Simple heuristic for demo
                suspicious.append(f"Process: {name} (PID: {proc.info['pid']})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Network Scan (High Traffic Outbound)
    # real implementation would check net_io_counters

    if suspicious:
        alert_msg = f"Sentinel Alert! Detected: {', '.join(suspicious)}"
        console.print(f"[bold red]{alert_msg}[/bold red]")
        
        # Trigger Voice of God (if enabled)
        if voice.enabled:
            voice.call_user("Security Alert. Suspicious process detected on your workstation.")
            
    else:
        console.print("[green]System Secure.[/green]")

def start_sentinel_daemon(interval=60):
    console.print(f"[bold green]Sentinel Active.[/bold green] Monitoring every {interval}s.")
    try:
        while True:
            scan_system()
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("[yellow]Sentinel Deactivated.[/yellow]")

if __name__ == "__main__":
    start_sentinel_daemon()
