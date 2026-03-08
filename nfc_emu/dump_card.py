"""
dump_card.py - FeliCa カードの全領域をダンプする (学生証・Suica 両対応版)
"""

import json
import binascii
import nfc
import struct
import os
import logging
from tqdm import tqdm
from .felica.const import ServiceAttribute
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.text import Text

console = Console()

def exchange(tag, cmd_code, idm, data, timeout=2.0):
    """
    FeliCa コマンドを低レベルで送信する。
    [サイズ(1)] [コマンド(1)] [IDm(8)] [データ(n)]
    """
    req = bytes([len(data) + 10, cmd_code]) + idm + data
    try:
        res = tag.clf.exchange(req, timeout=timeout)
        if res and len(res) >= 10 and res[1] == cmd_code + 1:
            return res
    except nfc.clf.CommunicationError:
        pass
    return None

def get_system_codes(tag):
    """Request System Code (0x0C) を発行して全システムコードを取得"""
    res = exchange(tag, 0x0C, tag.identifier, b"")
    sys_codes = []
    if res and len(res) >= 11:
        num_sc = res[10]
        for i in range(num_sc):
            sc = struct.unpack(">H", res[11+i*2 : 13+i*2])[0]
            sys_codes.append(sc)
    return sys_codes if sys_codes else [0x0003]

def get_mode(tag):
    """Request Response (0x04) を発行して現在の動作モードを取得"""
    res = exchange(tag, 0x04, tag.identifier, b"")
    if res and len(res) >= 11:
        return res[10]
    return 0

def get_key_versions(tag, service_codes):
    """Request Service (0x02) を発行してサービスのキーバージョンを取得"""
    versions = {}
    # 一度にリクエストできるのは最大 32 ノード程度
    chunk_size = 32
    for i in range(0, len(service_codes), chunk_size):
        chunk = service_codes[i:i+chunk_size]
        data = bytes([len(chunk)]) + b"".join([struct.pack("<H", s) for s in chunk])
        res = exchange(tag, 0x02, tag.identifier, data)
        if res and len(res) >= 11:
            num = res[10]
            for j in range(num):
                ver = struct.unpack("<H", res[11+j*2:13+j*2])[0]
                if ver != 0xFFFF:
                    versions[chunk[j]] = ver
    return versions

def _read_blocks(tag, idm, service_code, max_blocks=64):
    """Read Without Encryption (0x06) を発行してブロックを読み取る"""
    blocks = {}
    for b in range(max_blocks):
        # req: [0x06, サービス数(1), サービスリスト(2*n), ブロック数(1), ブロックリスト(2*m)]
        # ブロックリスト要素: 2バイト形式 (0x80 | サービスインデックス, ブロック番号)
        req_data = bytes([1]) + struct.pack("<H", service_code) + bytes([1, 0x80, b])
        res = exchange(tag, 0x06, idm, req_data)
        
        if res and len(res) >= 13 and res[10] == 0x00: # status1 == 0
            data = res[13:29]
            blocks[b] = data.hex().upper()
        else:
            break
    return blocks

def scan_and_read(tag, idm):
    """Search Service Code (0x0A) を使ってサービスを高速に走査する"""
    service_list = []
    service_attrs = {}
    area_ends = {}
    memory = {}
    found_sc_info = [] # (idx, sc, type, details, blocks, end_val)

    from rich import box
    console.print(f"[*] サービスをスキャン中...\n")
    
    try:
        # FeliCa のインデックス走査 (最大 256 程度が一般的)
        for idx in range(512):
            req_data = struct.pack("<H", idx)
            res = exchange(tag, 0x0A, idm, req_data)
            
            if not res or len(res) < 12:
                break
                
            found = struct.unpack("<H", res[10:12])[0]
            if found == 0xFFFF: # 終端
                break
            
            if found in service_list:
                continue

            attr = ServiceAttribute.from_code(found)
            service_list.append(found)
            service_attrs[str(found)] = attr
            
            end_code = None
            if (found & 0x3F) == 0x00 and len(res) >= 14:
                end_code = struct.unpack("<H", res[12:14])[0]
                area_ends[str(found)] = end_code
            
            read_count = 0
            if attr == "plain":
                blocks = _read_blocks(tag, idm, found)
                if blocks:
                    memory[str(found)] = blocks
                    read_count = len(blocks)
                    
                    # ブロックデータのテーブル表示 (nfc-probe と同様)
                    svc_blk_table = Table(show_header=True, box=box.SIMPLE_HEAD, header_style="bold magenta")
                    svc_blk_table.add_column("Blk", justify="right", style="dim")
                    svc_blk_table.add_column("Data (HEX)", style="green")
                    svc_blk_table.add_column("ASCII", style="white")

                    for b_num, hex_data in blocks.items():
                        raw = bytes.fromhex(hex_data)
                        ascii_str = "".join([chr(c) if 32 <= c <= 126 else "." for c in raw])
                        svc_blk_table.add_row(str(b_num), hex_data.upper(), ascii_str)
                    
                    console.print(Panel(svc_blk_table, title=f"[bold cyan]Service 0x{found:04X} ({attr})[/bold cyan]", expand=False))
                else:
                    console.print(f"  [italic dim]0x{found:04X}: No readable blocks[/italic dim]")
            
            found_sc_info.append({
                "idx": idx, "sc": found, "type": attr, "blocks": read_count, "end_val": end_code
            })

    except KeyboardInterrupt:
        console.print(f"\n[bold red][!] スキャンを中断しました。[/bold red]")

    # キーバージョンの取得
    service_versions = {}
    if service_list:
        service_versions = get_key_versions(tag, service_list)

    # サマリーを表示 (Tree 構造)
    root_tree = Tree("[bold white]FeliCa Service/Area Tree Structure[/bold white]")
    stack = [(0x0000, root_tree.add("[bold white]Root Area (0x0000)[/bold white]"), 0xFFFE)]

    for info in found_sc_info:
        sc = info["sc"]
        if sc == 0x0000: continue
        while len(stack) > 1 and sc > stack[-1][2]:
            stack.pop()
        parent_node = stack[-1][1]
        
        if info["type"] == "area":
            end_val = info.get("end_val", 0xFFFE)
            label = Text()
            label.append(f"Area 0x{sc:04X}", style="bold cyan")
            label.append(f" (End: 0x{end_val:04X})", style="dim")
            area_node = parent_node.add(label)
            stack.append((sc, area_node, end_val))
        else:
            label = Text()
            label.append(f"0x{sc:04X}", style="green")
            label.append(f" ({info['type']})".ljust(14))
            label.append(f"KeyVer: 0x{service_versions.get(sc, 0x0000):04X}", style="yellow")
            label.append("  ")
            if info["blocks"] > 0:
                label.append(f"{info['blocks']:>2} blocks", style="bold magenta")
            else:
                label.append(" - blocks", style="dim")
            parent_node.add(label)

    console.print(root_tree)
    return service_list, service_attrs, area_ends, memory, service_versions

def fix_ownership(path):
    """sudo で実行されている場合、ファイルの所有権を元のユーザーに戻す"""
    if not os.path.exists(path):
        return
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_uid and sudo_gid:
        try:
            os.chown(path, int(sudo_uid), int(sudo_gid))
        except OSError:
            pass

def dump_card(tag, output_path):
    all_sys_codes = get_system_codes(tag)
    primary_sc = all_sys_codes[0]
    mode = get_mode(tag)
    
    device_info = str(tag.clf.device) if hasattr(tag.clf, "device") else "Unknown Reader"
    
    info_table = Table(show_header=False, box=None)
    info_table.add_row("Device", f"[cyan]{device_info}[/cyan]")
    info_table.add_row("Primary System", f"[bold yellow]0x{primary_sc:04X}[/bold yellow]")
    info_table.add_row("All Systems", f"[yellow]{', '.join([f'0x{sc:04X}' for sc in all_sys_codes])}[/yellow]")
    mode_map = {0: "Normal", 1: "Authentication"}
    info_table.add_row("Mode", f"[bold white]{mode_map.get(mode, f'Custom (0x{mode:02X})')}[/bold white]")
    
    console.print(Panel(info_table, title="[bold blue]FeliCa Multi-System Card Dumping[/bold blue]", expand=False))

    sys_details = {}
    combined_service_list = set()
    combined_service_attrs = {}
    combined_area_ends = {}
    combined_memory = {}
    combined_service_versions = {}

    # 全システムコードを巡回
    for sc in all_sys_codes:
        console.print(f"\n[bold magenta]>>> Switching to System Code: 0x{sc:04X} <<<[/bold magenta]")
        
        # プライマリ SC でかつ既にタグがその IDm を持っている場合は、Polling をスキップして直接スキャンに進む
        is_primary = (sc == primary_sc)
        if is_primary:
            idm = tag.identifier
            pmm = tag.pmm
        else:
            # 指定した SC で Polling を送り直す
            # req: [Size, 0x00, SC(2), ReqCode(1), TSN(1)]
            polling_req = struct.pack("BBHBB", 6, 0x00, sc, 0x01, 0x00)
            try:
                res = tag.clf.exchange(polling_req, timeout=1.5)
                if not res or len(res) < 18:
                    console.print(f"  [bold red][!] System 0x{sc:04X} への Polling に失敗しました。スキップします。[/bold red]")
                    continue
                idm = res[2:10]
                pmm = res[10:18]
            except nfc.clf.TimeoutError:
                console.print(f"  [bold red][!] System 0x{sc:04X} への Polling がタイムアウトしました。スキップします。[/bold red]")
                continue
        
        sys_details[f"{sc:04X}"] = {
            "idm": idm.hex().upper(),
            "pmm": pmm.hex().upper()
        }
        
        # スキャン実行
        s_list, s_attrs, a_ends, mem, s_vers = scan_and_read(tag, idm)
        
        combined_service_list.update(s_list)
        combined_service_attrs.update(s_attrs)
        combined_area_ends.update(a_ends)
        combined_memory.update(mem)
        combined_service_versions.update(s_vers)

    if not combined_service_list:
        console.print("\n[bold red][!] 有効なサービスが見つかりませんでした。[/bold red]")
        return

    dump = {
        "idm": sys_details[f"{primary_sc:04X}"]["idm"],
        "pmm": sys_details[f"{primary_sc:04X}"]["pmm"],
        "sys_code": f"{primary_sc:04X}",
        "mode": mode,
        "sys_codes": [f"{sc:04X}" for sc in all_sys_codes],
        "sys_details": sys_details,
        "service_list": sorted(list(combined_service_list)),
        "service_attrs": combined_service_attrs,
        "area_ends": combined_area_ends,
        "service_versions": {str(k): v for k, v in combined_service_versions.items()},
        "memory": combined_memory,
        "patches": [],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dump, f, indent=2, ensure_ascii=False)
    
    fix_ownership(output_path)

    console.print(f"\n[bold green][+] データを '{output_path}' に保存しました。[/bold green]")

def on_connect(tag, output_path):
    if tag.type == "Type3Tag":
        dump_card(tag, output_path)
    else:
        console.print(f"\n[bold red][!] FeliCa (Type 3) ではないカードです: {tag.type}[/bold red]")
    return False

def main():
    import sys
    from rich.logging import RichHandler
    os.makedirs("cards", exist_ok=True)
    fix_ownership("cards")

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=Console(stderr=False), markup=True)]
    )

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        # パス区切りが含まれておらず、かつカレントディレクトリに同名ファイルがない場合は cards/ 下とみなす
        if os.path.sep not in arg and not os.path.exists(arg):
            output_path = os.path.join("cards", arg)
        else:
            output_path = arg
            
        if not output_path.endswith(".json"):
            output_path += ".json"
    else:
        name = console.input("[bold white]保存ファイル名 (cards/<名前>.json): cards/[/bold white]").strip() or "card"
        if not name.endswith(".json"): name += ".json"
        output_path = os.path.join("cards", name)
    
    from .utils import get_nfc_reader_path
    reader_path = get_nfc_reader_path()

    console.print(f"\n[*] NFC リーダー ([bold cyan]{reader_path}[/bold cyan]) を探索中...")
    try:
        with nfc.ContactlessFrontend(reader_path) as clf:
            console.print(f"[*] カードをリーダーに乗せてください... (保存先: [bold green]{output_path}[/bold green])\n")
            clf.connect(rdwr={"on-connect": lambda tag: on_connect(tag, output_path)})
    except Exception as e:
        if "No such device" in str(e) or "NFC reader" in str(e):
            console.print("[bold red][!] NFC リーダーが見つかりません。USB 接続を確認してください。[/bold red]")
        elif isinstance(e, KeyboardInterrupt):
            raise
        else:
            # AttributeError やその他のエラーを表示
            console.print(f"[bold red][!] エラーが発生しました: {e}[/bold red]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[bold red][!] 中断されました。[/bold red]")
        sys.exit(0)

if __name__ == "__main__":
    main()
