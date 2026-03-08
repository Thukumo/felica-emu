#!/usr/bin/env python3
"""
probe_card.py - 実カードの生の挙動を観察するスクリプト

RC-S380 に実カードをかざすと、以下を順番に実施してログを出力する:
  1. Request System Code で全システムコードを列挙
  2. Search Service Code で全サービスを列挙
  3. 発見した各サービスのブロックを読み取り (非暗号化のみ)

使い方:
    sudo python probe_card.py
"""

import struct
import nfc
import nfc.clf
from . import ServiceAttribute
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.panel import Panel
from rich.tree import Tree
from rich import box


import logging

logger = logging.getLogger(__name__)
console = Console()


def exchange(clf, req, timeout=2.0):
    """コマンドを送信し応答を返す。エラー時は None を返す。"""
    logger.debug(f"  --> REQ: {req.hex().upper()}")
    try:
        res = clf.exchange(req, timeout=timeout)
        logger.debug(f"  <-- RES: {res.hex().upper()}")
        return res
    except (nfc.clf.CommunicationError, nfc.clf.TimeoutError) as e:
        logger.warning(f"  [bold red][!] エラー: {type(e).__name__} {e}[/bold red]")
        return None
    except Exception as e:
        logger.error(f"  [bold red][!] 予期せぬエラー: {e}[/bold red]")
        return None


def search_service(clf, idm: bytes, idx: int):
    """Search Service Code を送り (service_code, end_area_code or None) を返す。"""
    req = bytes([12, 0x0A]) + idm + struct.pack("<H", idx)
    res = exchange(clf, req)
    if res and len(res) >= 12:
        sc = struct.unpack("<H", res[10:12])[0]
        end = struct.unpack("<H", res[12:14])[0] if len(res) >= 14 else None
        return sc, end
    return None, None


def get_key_versions(clf, idm, service_codes):
    """Request Service (0x02) を発行してサービスのキーバージョンを取得"""
    versions = {}
    chunk_size = 32
    for i in range(0, len(service_codes), chunk_size):
        chunk = service_codes[i:i+chunk_size]
        data = bytes([len(chunk)]) + b"".join([struct.pack("<H", s) for s in chunk])
        req = bytes([10 + len(data), 0x02]) + idm + data
        res = exchange(clf, req)
        if res and len(res) >= 11:
            num = res[10]
            for j in range(num):
                ver = struct.unpack("<H", res[11+j*2:13+j*2])[0]
                if ver != 0xFFFF:
                    versions[chunk[j]] = ver
    return versions


def read_block(clf, idm: bytes, service: int, block: int):
    """Read Without Encryption で 1 ブロック読み取り、16バイトを返す（エラー時 None）。"""
    svc_le = struct.pack("<H", service)
    # ブロックリスト要素: 2バイト形式 (0x80 | service_idx<<2, block_num)
    blk = bytes([0x80, block])
    req = bytes([16, 0x06]) + idm + bytes([1]) + svc_le + bytes([1]) + blk
    res = exchange(clf, req)
    # res: len(1) code(1) IDm(8) status1(1) status2(1) num_blocks(1) data(16)
    if res and len(res) >= 29 and res[10] == 0x00:
        return res[13:29]
    return None


def on_connect(tag):
    info_table = Table(show_header=False, box=box.ROUNDED)
    info_table.add_row("IDm", f"[bold green]{tag.identifier.hex().upper()}[/bold green]")
    info_table.add_row("PMm", f"[green]{tag.pmm.hex().upper()}[/green]")
    
    clf = tag.clf
    idm = tag.identifier

    # --- Request Response (Mode Check) ---
    req_res = bytes([10, 0x04]) + idm
    res = exchange(clf, req_res)
    mode_str = "Unknown"
    if res and len(res) >= 11:
        mode = res[10]
        mode_map = {0: "Normal", 1: "Authentication"}
        mode_str = mode_map.get(mode, f"Custom (0x{mode:02X})")
    info_table.add_row("Mode", f"[bold yellow]{mode_str}[/bold yellow]")

    console.print(Panel(info_table, title="[bold blue]Card Detected[/bold blue]", expand=False))

    # ─── 1. Request System Code ─────────────────────────────────────
    console.print("\n[bold yellow][1] Request System Code[/bold yellow]")
    try:
        req = bytes([10, 0x0C]) + idm
        res = exchange(clf, req)
        if res and len(res) >= 11:
            num = res[10]
            sc_list = []
            for i in range(num):
                sc = struct.unpack(">H", res[11+i*2:11+i*2+2])[0]
                sc_list.append(f"[yellow]0x{sc:04X}[/yellow]")
            console.print(f"  Found {num} codes: {', '.join(sc_list)}")
    except Exception as e:
        console.print(f"  [bold red][!] Request System Code 失敗: {e}[/bold red]")

    # ─── 2. Search Service Code ─────────────────────────────────────
    console.print("\n[bold yellow][2] Search Service Code (Service Enumeration)[/bold yellow]")
    services = []
    found_sc_info = [] # (idx, sc, type, details)

    for idx in range(256):
        sc, end_area = search_service(clf, idm, idx)
        if sc is None or sc == 0xFFFF:
            break
        services.append(sc)
        attr_name = ServiceAttribute.from_code(sc)
        attr = sc & 0x3F
        
        details = ""
        if attr == 0x00:
            details = f"End: [cyan]0x{end_area:04X}[/cyan]" if end_area is not None else ""
            svc_type = "area"
        else:
            svc_type = attr_name
        
        found_sc_info.append({
            "idx": idx, "sc": sc, "type": svc_type, "details": details, "blocks": 0, "end_val": end_area
        })

    # キーバージョンを一括取得
    key_versions = {}
    if services:
        key_versions = get_key_versions(clf, idm, services)
    for info in found_sc_info:
        info["key_ver"] = key_versions.get(info["sc"], 0x0000)

    # ─── 3. ブロック読み取り ───────────────────────────────────────
    console.print("\n[bold yellow][3] Block Reading (Plain Services Only)[/bold yellow]")
    for sc_data in found_sc_info:
        svc = sc_data["sc"]
        attr = svc & 0x3F
        if attr == 0x00: continue # Area
        if not (attr & 0x01): continue # Protected
        if attr & 0x04: continue # Encrypted

        # 各サービスをパネルで囲む
        svc_blk_table = Table(show_header=True, box=box.SIMPLE_HEAD, header_style="bold magenta")
        svc_blk_table.add_column("Blk", justify="right", style="dim")
        svc_blk_table.add_column("Data (HEX)", style="green")
        svc_blk_table.add_column("ASCII", style="white")

        read_count = 0
        for blk in range(32):
            data = read_block(clf, idm, svc, blk)
            if data is None:
                break
            
            read_count += 1
            hex_str = data.hex().upper()
            ascii_str = "".join([chr(c) if 32 <= c <= 126 else "." for c in data])
            svc_blk_table.add_row(str(blk), hex_str, ascii_str)

        sc_data["blocks"] = read_count
        if read_count > 0:
            console.print(Panel(svc_blk_table, title=f"[bold cyan]Service 0x{svc:04X} ({ServiceAttribute.from_code(svc)})[/bold cyan]", expand=False))
        else:
            console.print(f"  [dim]0x{svc:04X}: No readable blocks[/dim]")

    # 最後にサマリーを表示 (Tree 構造)
    root_tree = Tree("[bold white]FeliCa Service/Area Tree Structure[/bold white]")
    
    # 階層スタック: (area_code, tree_node, end_code)
    # Root (0x0000) は特殊なのでデフォルトでスタックに入れておく
    # FeliCa では 0x0000 は全領域をカバーしうるが、Search Service で出てきた場合は明示的な Area として扱う
    stack = [(0x0000, root_tree.add("[bold white]Root Area (0x0000)[/bold white]"), 0xFFFE)]

    for info in found_sc_info:
        sc = info["sc"]
        if sc == 0x0000: continue # Root は既に作成済み
        
        # 現在の SC が現在のエリアの範囲外であれば、スタックを戻す
        while len(stack) > 1 and sc > stack[-1][2]:
            stack.pop()
            
        parent_node = stack[-1][1]
        
        if info["type"] == "area":
            end_val = info.get("end_val", 0xFFFE)
            area_node = parent_node.add(f"[bold cyan]Area 0x{sc:04X}[/bold cyan] [dim](End: 0x{end_val:04X})[/dim]")
            stack.append((sc, area_node, end_val))
        else:
            # サービス
            blocks_str = f" [bold magenta]{info['blocks']} blks[/bold magenta]" if info["blocks"] > 0 else ""
            key_ver_str = f" [yellow]KV: 0x{info.get('key_ver', 0):04X}[/yellow]" if info["type"] != "area" else ""
            parent_node.add(f"[green]0x{sc:04X}[/green] ({info['type']}){key_ver_str}{blocks_str}")
    
    console.print("\n[bold yellow]Service Enumeration Summary:[/bold yellow]")
    console.print(root_tree)
    console.print("\n[bold green]Scan Completed[/bold green]")
    return False


def main():
    import sys
    import argparse
    parser = argparse.ArgumentParser(description="FeliCa 実カード観察ツール")
    parser.add_argument("-v", "--verbose", action="store_true", help="詳細な通信ログを表示")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=Console(stderr=False), markup=True)]
    )
    logging.getLogger("nfc").setLevel(logging.WARNING)

    from .utils import get_nfc_reader_path
    reader_path = get_nfc_reader_path()

    console.print(f"[bold blue]=== FeliCa 実カード観察ツール ({reader_path}) ===[/bold blue]")
    console.print("[italic white]RC-S380 に実カードをかざしてください...[/italic white]\n")

    try:
        with nfc.ContactlessFrontend(reader_path) as clf:
            clf.connect(
                rdwr={"on-connect": on_connect},
            )
    except Exception as e:
        if "No such device" in str(e) or "NFC reader" in str(e):
            console.print("[bold red][!] NFC リーダーが見つかりません。USB 接続を確認してください。[/bold red]")
        elif isinstance(e, KeyboardInterrupt):
            console.print("\n[bold red][!] 中断されました。[/bold red]")
            sys.exit(0)
        else:
            console.print(f"\n[bold red][!] エラーが発生しました: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
