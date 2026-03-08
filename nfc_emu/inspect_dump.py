#!/usr/bin/env python3
"""
inspect_dump.py - dump_card.py で保存した JSON のメモリ内容を表示
"""

import sys
import os
import json
from .felica.card import FeliCaCard
from .felica.scanner import FeliCaRenderer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.panel import Panel

console = Console()

def select_card(cards_dir="cards"):
    if not os.path.isdir(cards_dir): return None
    files = sorted(f for f in os.listdir(cards_dir) if f.endswith(".json"))
    if not files: return None
    from .utils import select_from_list
    idx = select_from_list(files, "--- カードデータを選択してください ---")
    return os.path.join(cards_dir, files[idx])

def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s", datefmt="[%X]",
                        handlers=[RichHandler(rich_tracebacks=True, console=Console(stderr=False), markup=True)])

    try:
        if len(sys.argv) > 1:
            path = sys.argv[1]
            if not os.path.exists(path):
                alt_path = os.path.join("cards", path)
                if not alt_path.endswith(".json"): alt_path += ".json"
                if os.path.exists(alt_path): path = alt_path
                elif not path.endswith(".json") and os.path.exists(path + ".json"): path += ".json"
        else:
            path = select_card()
            if not path: sys.exit(1)

        console.print(f"\n[*] '{path}' を読み込み中...")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        card = FeliCaCard.from_dict(data)

        header_table = Table(show_header=False, box=None)
        header_table.add_row("File", os.path.basename(path))
        header_table.add_row("Primary IDm", f"[bold green]{card.idm.hex().upper()}[/bold green]")
        header_table.add_row("Primary PMm", f"[green]{card.pmm.hex().upper()}[/green]")
        header_table.add_row("Primary SC", f"[yellow]0x{card.primary_sys_code.hex().upper()}[/yellow]")
        
        mode_map = {0: "Normal", 1: "Authentication"}
        header_table.add_row("Mode", f"[bold white]{mode_map.get(card.mode, f'Custom (0x{card.mode:02X})')}[/bold white]")

        if len(card.sys_map) > 1:
            sys_info = [f"[bold yellow]0x{sc:04X}[/bold yellow] ([green]{info['idm'].hex().upper()}[/green])" 
                        for sc, info in sorted(card.sys_map.items())]
            header_table.add_row("All Systems", ", ".join(sys_info))

        console.print(Panel(header_table, title="[bold blue]FeliCa Card Inspection[/bold blue]", expand=False))

        # ─── サマリー Tree の表示 ───
        found_sc_info = []
        for sc in card.service_list:
            attr = card.get_service_attr(sc)
            num_blocks = len(card.services[sc].memory) if sc in card.services else 0
            found_sc_info.append({
                "sc": sc, "type": attr, "key_ver": card.services[sc].key_version if sc in card.services else 0,
                "blocks": num_blocks, "end_val": card.area_ends.get(sc, 0xFFFE)
            })

        console.print("\n[bold yellow]Service Enumeration Summary:[/bold yellow]")
        console.print(FeliCaRenderer.render_service_tree(found_sc_info))

        # ─── 各サービスのメモリデータ (Hexdump) ───
        console.print("\n[bold yellow]Memory Contents:[/bold yellow]")
        has_memory = False
        for svc_code in sorted(card.services):
            service = card.services[svc_code]
            if not service.memory: continue
            has_memory = True
            console.print(FeliCaRenderer.render_block_panel(svc_code, service.attr, service.memory))

        if not has_memory: console.print("  [dim](No memory data available)[/dim]")

        # ─── パッチ情報の表示 ───
        patches = data.get("patches", []) + data.get("ascii_patches", [])
        if patches:
            console.print(f"\n[bold yellow]" + "-"*10 + " パッチ情報 " + "-"*10 + "[/bold yellow]")
            for p in patches:
                svc = int(p["service"], 0) if isinstance(p["service"], str) else p["service"]
                val = f'hex: [green]{p["hex"]}[/green]' if "hex" in p else f'str: [green]{p["ascii"]!r}[/green]'
                console.print(f"  svc=[cyan]0x{svc:04X}[/cyan] blk=[cyan]{p['block']:<2}[/cyan] @+[cyan]{p.get('offset', 0):02X}[/cyan]  {val}")
            console.print("[bold yellow]" + "-" * 50 + "[/bold yellow]")
    except Exception as e:
        console.print(f"\n[bold red][!] エラーが発生しました: {e}[/bold red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
