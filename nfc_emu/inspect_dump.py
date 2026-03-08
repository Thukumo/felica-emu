#!/usr/bin/env python3
"""
inspect_dump.py - dump_card.py で保存した JSON のメモリ内容を hexdump 表示する

使い方:
    python inspect_dump.py              # cards/ から対話選択
    python inspect_dump.py <ファイル>   # 直接指定
"""

import sys
import os
import json
from .felica.card import FeliCaCard
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

console = Console()

def hexdump(data: bytes, offset_start: int = 0) -> str:
    """16バイトブロックを hexdump 形式 (オフセット | hex | ASCII) で返す。"""
    hex_part = " ".join(f"{b:02X}" for b in data)
    ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
    return f"  +{offset_start:02X}  {hex_part}  |{ascii_part}|"

def select_card(cards_dir="cards"):
    if not os.path.isdir(cards_dir):
        return None
    files = sorted(f for f in os.listdir(cards_dir) if f.endswith(".json"))
    if not files:
        return None
    
    from .utils import select_from_list
    idx = select_from_list(files, "--- カードデータを選択してください ---")
    return os.path.join(cards_dir, files[idx])

def main():
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=Console(stderr=False), markup=True)]
    )

    try:
        if len(sys.argv) > 1:
            path = sys.argv[1]
            # もしファイルが直接見つからず、かつ cards/ ディレクトリにあるなら補完する
            if not os.path.exists(path):
                alt_path = os.path.join("cards", path)
                if not alt_path.endswith(".json"):
                    alt_path += ".json"
                if os.path.exists(alt_path):
                    path = alt_path
                elif not path.endswith(".json") and os.path.exists(path + ".json"):
                    path += ".json"
        else:
            path = select_card()
            if not path:
                console.print("[bold red][!] cards/ にカードデータがありません。[/bold red]")
                sys.exit(1)

        console.print(f"\n[*] '{path}' を読み込み中...")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        card = FeliCaCard.from_dict(data)

        header_table = Table(show_header=False, box=None)
        header_table.add_row("File", os.path.basename(path))
        header_table.add_row("Primary IDm", f"[bold green]{card.idm.hex().upper()}[/bold green]")
        header_table.add_row("Primary PMm", f"[green]{card.pmm.hex().upper()}[/green]")
        header_table.add_row("Primary SC", f"[yellow]0x{card.primary_sys_code.hex().upper()}[/yellow]")
        
        # マルチシステム情報の表示
        if len(card.sys_map) > 1:
            sys_info = []
            for sc, info in sorted(card.sys_map.items()):
                sys_info.append(f"[bold yellow]0x{sc:04X}[/bold yellow] ([green]{info['idm'].hex().upper()}[/green])")
            header_table.add_row("All Systems", ", ".join(sys_info))

        from rich.panel import Panel
        console.print(Panel(header_table, title="[bold blue]FeliCa Card Inspection[/bold blue]", expand=False))

        # サービスの表示
        for svc_code in sorted(card.services):
            service = card.services[svc_code]
            attr = service.attr
            
            # エリア情報の追加
            details = ""
            if attr == "area" and svc_code in card.area_ends:
                details = f" [dim]End: 0x{card.area_ends[svc_code]:04X}[/dim]"
                
            console.print(f"  [bold cyan][ 0x{svc_code:04X} : {attr:<9} ][/bold cyan]{details} [dim]{'-'*20}[/dim]")
            
            if service.memory:
                for blk in sorted(service.memory):
                    raw = service.memory[blk]
                    hex_part = raw.hex().upper()
                    ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in raw)
                    console.print(f"    B{blk:>2}: [green]{hex_part}[/green] | [white]{ascii_part}[/white]")
                console.print()

        patches = data.get("patches", []) + data.get("ascii_patches", [])
        if patches:
            console.print(f"\n[bold yellow]" + "-"*10 + " 元データに含まれるパッチ情報 " + "-"*10 + "[/bold yellow]")
            for p in patches:
                svc = int(p["service"], 0) if isinstance(p["service"], str) else p["service"]
                blk = p["block"]
                offset = p.get("offset", 0)
                if "hex" in p:
                    val = f'hex: [green]{p["hex"]}[/green]'
                elif "ascii" in p:
                    val = f'str: [green]{p["ascii"]!r}[/green]'
                else:
                    val = f'str (old): [green]{p.get("value", "")!r}[/green]'
                console.print(f"  svc=[cyan]0x{svc:04X}[/cyan] blk=[cyan]{blk:<2}[/cyan] @+[cyan]{offset:02X}[/cyan]  {val}")
            console.print("[bold yellow]" + "-" * 50 + "[/bold yellow]")
    except KeyboardInterrupt:
        console.print("\n[bold red][!] 終了します。[/bold red]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red][!] エラーが発生しました: {e}[/bold red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
