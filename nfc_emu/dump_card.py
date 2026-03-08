"""
dump_card.py - FeliCa カードの全領域をダンプする (学生証・Suica 両対応版)
"""

import json
import nfc
import struct
import os
import logging
from .felica.const import ServiceAttribute
from .felica.scanner import FeliCaScanner, FeliCaRenderer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

def scan_and_read(tag, idm):
    """FeliCaScanner を使ってサービスを走査し、データを読み取る"""
    service_list = []
    service_attrs = {}
    area_ends = {}
    memory = {}
    found_sc_info = []

    console.print(f"[*] サービスをスキャン中...\n")
    
    try:
        # FeliCa のインデックス走査
        for idx in range(512):
            sc, end_code = FeliCaScanner.search_service(tag, idm, idx)
            if sc is None or sc == 0xFFFF:
                break
            
            if sc in service_list:
                continue

            attr = ServiceAttribute.from_code(sc)
            service_list.append(sc)
            service_attrs[str(sc)] = attr
            if end_code is not None:
                area_ends[str(sc)] = end_code
            
            blocks = {}
            if attr.startswith("plain"):
                for b in range(64):
                    data = FeliCaScanner.read_block(tag, idm, sc, b)
                    if data is None:
                        break
                    blocks[b] = data
                
                if blocks:
                    memory[str(sc)] = {str(k): v.hex().upper() for k, v in blocks.items()}
                    console.print(FeliCaRenderer.render_block_panel(sc, attr, blocks))
                else:
                    console.print(f"  [italic dim]0x{sc:04X}: No readable blocks[/italic dim]")
            
            found_sc_info.append({
                "idx": idx, "sc": sc, "type": attr, "blocks": len(blocks), "end_val": end_code
            })

    except KeyboardInterrupt:
        console.print(f"\n[bold red][!] スキャンを中断しました。[/bold red]")

    # キーバージョンの取得
    service_versions = FeliCaScanner.get_key_versions(tag, idm, service_list)
    for info in found_sc_info:
        info["key_ver"] = service_versions.get(info["sc"], 0x0000)

    # サマリーを表示
    console.print(FeliCaRenderer.render_service_tree(found_sc_info))
    return service_list, service_attrs, area_ends, memory, service_versions

def fix_ownership(path):
    if not os.path.exists(path): return
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_uid and sudo_gid:
        try: os.chown(path, int(sudo_uid), int(sudo_gid))
        except OSError: pass

def dump_card(tag, output_path):
    all_sys_codes = FeliCaScanner.get_system_codes(tag)
    primary_sc = all_sys_codes[0]
    mode = FeliCaScanner.get_mode(tag)
    
    device_info = str(tag.clf.device) if hasattr(tag.clf, "device") else "Unknown Reader"
    
    info_table = Table(show_header=False, box=None)
    info_table.add_row("Device", f"[cyan]{device_info}[/cyan]")
    info_table.add_row("Primary System", f"[bold yellow]0x{primary_sc:04X}[/bold yellow]")
    info_table.add_row("All Systems", f"[yellow]{', '.join([f'0x{sc:04X}' for sc in all_sys_codes])}[/yellow]")
    mode_map = {0: "Normal", 1: "Authentication"}
    info_table.add_row("Mode", f"[bold white]{mode_map.get(mode, f'Custom (0x{mode:02X})')}[/bold white]")
    
    console.print(Panel(info_table, title="[bold blue]FeliCa Multi-System Card Dumping[/bold blue]", expand=False))

    sys_details, combined_service_list, combined_service_attrs = {}, set(), {}
    combined_area_ends, combined_memory, combined_service_versions = {}, {}, {}

    for sc in all_sys_codes:
        console.print(f"\n[bold magenta]>>> Switching to System Code: 0x{sc:04X} <<<[/bold magenta]")
        
        if sc == primary_sc:
            idm, pmm = tag.identifier, tag.pmm
        else:
            # Polling を送り直す
            polling_req = struct.pack("BBHBB", 6, 0x00, sc, 0x01, 0x00)
            try:
                res = tag.clf.exchange(polling_req, timeout=1.5)
                if not res or len(res) < 18:
                    continue
                idm, pmm = res[2:10], res[10:18]
            except nfc.clf.TimeoutError:
                continue
        
        sys_details[f"{sc:04X}"] = {"idm": idm.hex().upper(), "pmm": pmm.hex().upper()}
        s_list, s_attrs, a_ends, mem, s_vers = scan_and_read(tag, idm)
        
        combined_service_list.update(s_list)
        combined_service_attrs.update(s_attrs)
        combined_area_ends.update(a_ends)
        combined_memory.update(mem)
        combined_service_versions.update(s_vers)

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
    return False

def main():
    import sys
    from rich.logging import RichHandler
    os.makedirs("cards", exist_ok=True)
    fix_ownership("cards")

    logging.basicConfig(level=logging.INFO, format="%(message)s", datefmt="[%X]",
                        handlers=[RichHandler(rich_tracebacks=True, console=Console(stderr=False), markup=True)])

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        output_path = os.path.join("cards", arg) if os.path.sep not in arg and not os.path.exists(arg) else arg
        if not output_path.endswith(".json"): output_path += ".json"
    else:
        name = console.input("[bold white]保存ファイル名 (cards/<名前>.json): cards/[/bold white]").strip() or "card"
        if not name.endswith(".json"): name += ".json"
        output_path = os.path.join("cards", name)
    
    from .utils import get_nfc_reader_path
    reader_path = get_nfc_reader_path()
    try:
        with nfc.ContactlessFrontend(reader_path) as clf:
            clf.connect(rdwr={"on-connect": lambda tag: on_connect(tag, output_path)})
    except Exception as e:
        console.print(f"[bold red][!] エラーが発生しました: {e}[/bold red]")
        sys.exit(1)

if __name__ == "__main__":
    main()
