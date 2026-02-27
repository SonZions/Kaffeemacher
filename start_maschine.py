#!/usr/bin/env python3
"""
DeLonghi PrimaDonna Soul – Startup-Script
==========================================

Startet die Kaffeemaschine (ECAM612) über das Heimnetzwerk via cremalink.

Die DeLonghi Soul kommuniziert über einen lokalen Reverse-Proxy (cremalink-server):
  1. cremalink-server läuft auf deinem PC/Server im Heimnetz
  2. Die Maschine verbindet sich aktiv mit diesem Server
  3. Du schickst Befehle an den Server → Maschine holt sie ab und führt sie aus

== Setup (einmalig) ==

    pip install cremalink

Dann Zugangsdaten einrichten:

    python start_maschine.py setup

Das generiert eine config.json mit IP, DSN und LAN-Key.

== Verwendung ==

    python start_maschine.py          # Maschine einschalten (config.json)
    python start_maschine.py --off    # Maschine ausschalten

Optional direkt per Argument:
    python start_maschine.py --ip 192.168.1.100 --dsn ABC123 --lan-key XYZ

== Erklärung DSN / LAN-Key ==

DSN (Device Serial Number) und LAN-Key erhält man über den „setup"-Modus,
der sich mit der De'Longhi Cloud verbindet. Dafür brauchst du ein
De'Longhi Coffee Link Konto und einen Refresh-Token.

Den Refresh-Token bekommst du, indem du den Netzwerkverkehr der
Coffee-Link-App mitschneidest (z.B. mit mitmproxy oder Charles Proxy)
und im Login-Aufruf nach dem Feld „refresh_token" suchst.
"""

import argparse
import json
import os
import subprocess
import sys
import time

import requests

# ─── Konstanten ────────────────────────────────────────────────────────────────

POWER_ON_CMD  = "0d07840f02015512"   # aus ECAM612.json (cremalink)
POWER_OFF_CMD = "0d07840f01010041"

CREMALINK_HOST    = "127.0.0.1"
CREMALINK_PORT    = 10280
CREMALINK_URL     = f"http://{CREMALINK_HOST}:{CREMALINK_PORT}"
DEFAULT_CFG_PATH  = "config.json"

# ─── Hilfsfunktionen ───────────────────────────────────────────────────────────

def server_running() -> bool:
    """Prüft, ob der Cremalink-Server bereits läuft."""
    try:
        r = requests.get(f"{CREMALINK_URL}/health", timeout=2)
        return r.ok
    except Exception:
        return False


def start_server() -> subprocess.Popen:
    """Startet den Cremalink-Server als Hintergrundprozess."""
    proc = subprocess.Popen(
        ["cremalink-server", "--ip", CREMALINK_HOST, "--port", str(CREMALINK_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(10):
        time.sleep(0.5)
        if server_running():
            return proc
    proc.terminate()
    raise RuntimeError(
        "Cremalink-Server konnte nicht gestartet werden. "
        "Ist cremalink installiert? (pip install cremalink)"
    )


def configure_device(machine_ip: str, dsn: str, lan_key: str) -> None:
    """Schickt die Verbindungsdetails an den Cremalink-Server."""
    payload = {"ip": machine_ip, "dsn": dsn, "lan_key": lan_key}
    r = requests.post(f"{CREMALINK_URL}/configure", json=payload, timeout=10)
    r.raise_for_status()


def send_command(hex_cmd: str) -> None:
    """Stellt einen Befehl in die Warteschlange des Cremalink-Servers."""
    r = requests.post(f"{CREMALINK_URL}/command", json={"command": hex_cmd}, timeout=10)
    r.raise_for_status()


def get_status() -> dict:
    """Ruft den aktuellen Maschinenstatus ab."""
    r = requests.get(f"{CREMALINK_URL}/monitor", timeout=10)
    r.raise_for_status()
    return r.json()


def load_config(path: str) -> dict:
    """Lädt die Konfigurationsdatei."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(path: str, data: dict) -> None:
    """Speichert die Konfigurationsdatei."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Konfiguration gespeichert in: {path}")


# ─── Setup-Modus ───────────────────────────────────────────────────────────────

def cmd_setup(cfg_path: str) -> None:
    """Interaktiver Setup: Zugangsdaten über De'Longhi Cloud abrufen."""
    print("=== DeLonghi Soul – Ersteinrichtung ===\n")
    print("Zum Abrufen von DSN und LAN-Key wird ein De'Longhi-Account benötigt.")
    print("Du brauchst deinen Refresh-Token aus der Coffee-Link-App.\n")
    print("Tipp: Den Token findest du im Netzwerkverkehr der App (z.B. mit")
    print("mitmproxy oder Charles Proxy im Login-Request, Feld 'refresh_token').\n")

    refresh_token = input("Refresh-Token eingeben: ").strip()
    if not refresh_token:
        print("Fehler: Kein Token eingegeben.")
        sys.exit(1)

    try:
        from cremalink import Client
    except ImportError:
        print("Fehler: cremalink ist nicht installiert. Bitte zuerst ausführen:")
        print("  pip install cremalink")
        sys.exit(1)

    token_file = ".token.json"
    with open(token_file, "w") as f:
        json.dump({"refresh_token": refresh_token}, f)

    try:
        client = Client(token_file=token_file)
        devices = client.get_devices()

        if not devices:
            print("Keine Geräte im Account gefunden.")
            sys.exit(1)

        if len(devices) == 1:
            device = devices[0]
        else:
            print("\nGefundene Geräte:")
            for i, d in enumerate(devices):
                print(f"  [{i}] {d.dsn} – {getattr(d, 'product_name', 'Unbekannt')}")
            idx = int(input("Gerät auswählen [0]: ").strip() or "0")
            device = devices[idx]

        machine_ip = input(f"\nIP-Adresse der Maschine im Heimnetz: ").strip()
        if not machine_ip:
            print("Fehler: Keine IP-Adresse angegeben.")
            sys.exit(1)

        config = {
            "machine_ip": machine_ip,
            "dsn":        device.dsn,
            "lan_key":    device.lan_key,
        }
        save_config(cfg_path, config)
        print("\nSetup abgeschlossen! Maschine starten mit:")
        print(f"  python start_maschine.py")

    finally:
        if os.path.exists(token_file):
            os.remove(token_file)


# ─── Hauptfunktion ─────────────────────────────────────────────────────────────

def cmd_power(args, cfg_path: str) -> None:
    """Maschine ein- oder ausschalten."""
    config = {}
    if os.path.exists(cfg_path):
        config = load_config(cfg_path)

    machine_ip = args.ip      or config.get("machine_ip")
    dsn        = args.dsn     or config.get("dsn")
    lan_key    = args.lan_key or config.get("lan_key")

    missing = [k for k, v in [("--ip", machine_ip), ("--dsn", dsn), ("--lan-key", lan_key)] if not v]
    if missing:
        print(f"Fehler: Folgende Angaben fehlen: {', '.join(missing)}")
        print(f"Entweder {cfg_path} anlegen (via 'python start_maschine.py setup')")
        print("oder die fehlenden Argumente direkt übergeben.")
        sys.exit(1)

    action = "AUS" if args.off else "EIN"
    cmd    = POWER_OFF_CMD if args.off else POWER_ON_CMD
    print(f"DeLonghi Soul ({machine_ip}) wird {action}geschaltet …")

    proc = None
    if not server_running():
        print("Starte Cremalink-Server …")
        proc = start_server()
        print("Cremalink-Server läuft.")

    try:
        configure_device(machine_ip, dsn, lan_key)
        send_command(cmd)
        # Kurz warten, damit die Maschine den Befehl abholen kann
        time.sleep(3)
        print(f"Befehl gesendet. Die Maschine schaltet sich {action}.")
    except requests.HTTPError as e:
        print(f"Fehler beim Senden des Befehls: {e}")
        sys.exit(1)
    finally:
        if proc:
            proc.terminate()


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DeLonghi PrimaDonna Soul via WLAN steuern",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Beispiele:\n"
            "  python start_maschine.py setup            # Ersteinrichtung\n"
            "  python start_maschine.py                  # Maschine einschalten\n"
            "  python start_maschine.py --off            # Maschine ausschalten\n"
            "  python start_maschine.py --ip 192.168.1.100 --dsn XYZ --lan-key ABC\n"
        ),
    )
    parser.add_argument("action", nargs="?", choices=["setup"],
                        help="'setup' für Ersteinrichtung")
    parser.add_argument("--config",  default=DEFAULT_CFG_PATH,
                        help=f"Konfigurationsdatei (Standard: {DEFAULT_CFG_PATH})")
    parser.add_argument("--off",     action="store_true",
                        help="Maschine ausschalten statt einschalten")
    parser.add_argument("--ip",      metavar="ADRESSE",
                        help="IP-Adresse der Kaffeemaschine")
    parser.add_argument("--dsn",     metavar="DSN",
                        help="Device Serial Number der Maschine")
    parser.add_argument("--lan-key", metavar="KEY",
                        help="LAN-Key der Maschine")
    args = parser.parse_args()

    if args.action == "setup":
        cmd_setup(args.config)
    else:
        cmd_power(args, args.config)


if __name__ == "__main__":
    main()
