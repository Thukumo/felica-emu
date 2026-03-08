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
from rich.panel import Panel
from rich.tree import Tree
from rich.text import Text
from rich import box

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
        
        mode_map = {0: "Normal", 1: "Authentication"}
        header_table.add_row("Mode", f"[bold white]{mode_map.get(card.mode, f'Custom (0x{card.mode:02X})')}[/bold white]")

        # マルチシステム情報の表示
        if len(card.sys_map) > 1:
            sys_info = []
            for sc, info in sorted(card.sys_map.items()):
                sys_info.append(f"[bold yellow]0x{sc:04X}[/bold yellow] ([green]{info['idm'].hex().upper()}[/green])")
            header_table.add_row("All Systems", ", ".join(sys_info))

        console.print(Panel(header_table, title="[bold blue]FeliCa Card Inspection[/bold blue]", expand=False))

        # ─── サマリー Tree の表示 ───
        # service_list が JSON にあればそれを使用し、なければ card.services から構築
        svc_codes = card.service_list
        if not svc_codes:
            console.print("\n[bold red]No services found in dump.[/bold red]")
        else:
            root_sc = svc_codes[0]
            root_attr = card.get_service_attr(root_sc)
            root_end = card.area_ends.get(root_sc, 0xFFFE)
            
            root_tree = Tree(f"[bold white]FeliCa Service/Area Tree Structure[/bold white]")
            
            if root_attr == "area":
                root_label = Text()
                root_label.append(f"Root Area 0x{root_sc:04X}", style="bold white")
                root_label.append(f" (End: 0x{root_end:04X})", style="dim")
                root_node = root_tree.add(root_label)
                stack = [(root_sc, root_node, root_end)]
                items_to_process = svc_codes[1:]
            else:
                # 稀なケース：ルートがエリアではない場合は仮想的なルートを作成
                virtual_root = root_tree.add("[bold white]System Root[/bold white]")
                stack = [(None, virtual_root, 0xFFFF)]
                items_to_process = svc_codes

            for sc in items_to_process:
                while len(stack) > 1 and sc > stack[-1][2]:
                    stack.pop()
                parent_node = stack[-1][1]
                
                attr = card.get_service_attr(sc)
                if attr == "area":
                    end_val = card.area_ends.get(sc, 0xFFFE)
                    label = Text()
                    label.append(f"Area 0x{sc:04X}", style="bold cyan")
                    label.append(f" (End: 0x{end_val:04X})", style="dim")
                    area_node = parent_node.add(label)
                    stack.append((sc, area_node, end_val))
                else:
                    label = Text()
                    label.append(f"0x{sc:04X}", style="green")
                    label.append(f" ({attr})".ljust(14))
                    
                    # キーバージョンの取得
                    key_ver = 0
                    if sc in card.services:
                        key_ver = card.services[sc].key_version
                    label.append(f"KeyVer: 0x{key_ver:04X}", style="yellow")
                    label.append("  ")
                    
                    num_blocks = 0
                    if sc in card.services:
                        num_blocks = len(card.services[sc].memory)
                    
                    if num_blocks > 0:
                        label.append(f"{num_blocks:>2} blocks", style="bold magenta")
                    else:
                        label.append(" - blocks", style="dim")
                    parent_node.add(label)

            console.print("\n[bold yellow]Service Enumeration Summary:[/bold yellow]")
            console.print(root_tree)

        # ─── 各サービスのメモリデータ (Hexdump) ───
        console.print("\n[bold yellow]Memory Contents:[/bold yellow]")
        has_memory = False
        for svc_code in sorted(card.services):
            service = card.services[svc_code]
            if not service.memory:
                continue
            
            has_memory = True
            attr = service.attr
            
            svc_blk_table = Table(show_header=True, box=box.SIMPLE_HEAD, header_style="bold magenta")
            svc_blk_table.add_column("Blk", justify="right", style="dim")
            svc_blk_table.add_column("Data (HEX)", style="green")
            svc_blk_table.add_column("ASCII", style="white")

            for blk in sorted(service.memory):
                raw = service.memory[blk]
                hex_part = raw.hex().upper()
                ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in raw)
                svc_blk_table.add_row(str(blk), hex_part, ascii_part)

            console.print(Panel(svc_blk_table, title=f"[bold cyan]Service 0x{svc_code:04X} ({attr})[/bold cyan]", expand=False))

        if not has_memory:
            console.print("  [dim](No memory data available)[/dim]")

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
