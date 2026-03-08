#!/usr/bin/env python3
"""
probe_card.py - 実カードの生の挙動を観察するスクリプト
"""

import struct
import nfc
import nfc.clf
import logging
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.panel import Panel
from rich import box
from .felica.const import ServiceAttribute
from .felica.scanner import FeliCaScanner, FeliCaRenderer

logger = logging.getLogger(__name__)
console = Console()

def on_connect(tag):
    info_table = Table(show_header=False, box=box.ROUNDED)
    info_table.add_row("IDm", f"[bold green]{tag.identifier.hex().upper()}[/bold green]")
    info_table.add_row("PMm", f"[green]{tag.pmm.hex().upper()}[/green]")
    
    clf = tag.clf
    idm = tag.identifier

    # --- Mode Check ---
    mode = FeliCaScanner.get_mode(tag)
    mode_map = {0: "Normal", 1: "Authentication"}
    info_table.add_row("Mode", f"[bold yellow]{mode_map.get(mode, f'Custom (0x{mode:02X})')}[/bold yellow]")
    console.print(Panel(info_table, title="[bold blue]Card Detected[/bold blue]", expand=False))

    # ─── 1. Request System Code ─────────────────────────────────────
    console.print("\n[bold yellow][1] Request System Code[/bold yellow]")
    sys_codes = FeliCaScanner.get_system_codes(tag)
    sc_list = [f"[yellow]0x{sc:04X}[/yellow]" for sc in sys_codes]
    console.print(f"  Found {len(sys_codes)} codes: {', '.join(sc_list)}")

    # ─── 2. Search Service Code ─────────────────────────────────────
    console.print("\n[bold yellow][2] Search Service Code (Service Enumeration)[/bold yellow]")
    services = []
    found_sc_info = []

    for idx in range(256):
        sc, end_area = FeliCaScanner.search_service(tag, idm, idx)
        if sc is None or sc == 0xFFFF:
            break
        services.append(sc)
        attr = ServiceAttribute.from_code(sc)
        found_sc_info.append({
            "idx": idx, "sc": sc, "type": attr, "blocks": 0, "end_val": end_area
        })

    # キーバージョンを一括取得
    key_versions = FeliCaScanner.get_key_versions(tag, idm, services)
    for info in found_sc_info:
        info["key_ver"] = key_versions.get(info["sc"], 0x0000)

    # ─── 3. ブロック読み取り ───────────────────────────────────────
    console.print("\n[bold yellow][3] Block Reading (Plain Services Only)[/bold yellow]")
    for sc_data in found_sc_info:
        svc = sc_data["sc"]
        attr = sc_data["type"]
        if attr != "plain": continue

        blocks = {}
        for blk in range(32):
            data = FeliCaScanner.read_block(tag, idm, svc, blk)
            if data is None:
                break
            blocks[blk] = data

        sc_data["blocks"] = len(blocks)
        if blocks:
            console.print(FeliCaRenderer.render_block_panel(svc, attr, blocks))
        else:
            console.print(f"  [dim]0x{svc:04X}: No readable blocks[/dim]")

    # 最後にサマリーを表示
    console.print("\n[bold yellow]Service Enumeration Summary:[/bold yellow]")
    console.print(FeliCaRenderer.render_service_tree(found_sc_info))
    console.print("\n[bold green]Scan Completed[/bold green]")
    return False

def main():
    import sys
    import argparse
    parser = argparse.ArgumentParser(description="FeliCa 実カード観察ツール")
    parser.add_argument("-v", "--verbose", action="store_true", help="詳細な通信ログを表示")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(message)s", datefmt="[%X]",
                        handlers=[RichHandler(rich_tracebacks=True, console=Console(stderr=False), markup=True)])
    logging.getLogger("nfc").setLevel(logging.WARNING)

    from .utils import get_nfc_reader_path
    reader_path = get_nfc_reader_path()

    console.print(f"[bold blue]=== FeliCa 実カード観察ツール ({reader_path}) ===[/bold blue]")
    console.print("[italic white]RC-S380 に実カードをかざしてください...[/italic white]\n")

    try:
        with nfc.ContactlessFrontend(reader_path) as clf:
            clf.connect(rdwr={"on-connect": on_connect})
    except Exception as e:
        console.print(f"\n[bold red][!] エラーが発生しました: {e}[/bold red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
