"""
FeliCa スキャン・共通処理ユーティリティ
"""

import struct
import nfc
import logging
from typing import Optional, List, Dict, Tuple
from rich.tree import Tree
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich import box
from .const import CommandCode, ResponseCode, ServiceAttribute

logger = logging.getLogger(__name__)

def exchange(tag, cmd_code: int, idm: bytes, data: bytes = b"", timeout: float = 2.0) -> Optional[bytes]:
    """FeliCa コマンドを送信し、応答を返す共通ヘルパー"""
    req = bytes([len(data) + 10, cmd_code]) + idm + data
    try:
        res = tag.clf.exchange(req, timeout=timeout)
        if res and len(res) >= 2 and res[1] == cmd_code + 1:
            return res
    except (nfc.clf.CommunicationError, nfc.clf.TimeoutError):
        pass
    return None

class FeliCaScanner:
    """実カードとの対話・スキャンを担当するクラス"""
    
    @staticmethod
    def get_system_codes(tag) -> List[int]:
        res = exchange(tag, CommandCode.REQUEST_SYSTEM_CODE, tag.identifier)
        if res and len(res) >= 11:
            num = res[10]
            return [struct.unpack(">H", res[11+i*2 : 13+i*2])[0] for i in range(num)]
        return [0x0003]

    @staticmethod
    def get_mode(tag) -> int:
        res = exchange(tag, CommandCode.REQUEST_RESPONSE, tag.identifier)
        if res and len(res) >= 11:
            return res[10]
        return 0

    @staticmethod
    def get_key_versions(tag, idm: bytes, service_codes: List[int]) -> Dict[int, int]:
        versions = {}
        chunk_size = 32
        for i in range(0, len(service_codes), chunk_size):
            chunk = service_codes[i:i+chunk_size]
            data = bytes([len(chunk)]) + b"".join([struct.pack("<H", s) for s in chunk])
            res = exchange(tag, CommandCode.REQUEST_SERVICE, idm, data)
            if res and len(res) >= 11:
                num = res[10]
                for j in range(num):
                    ver = struct.unpack("<H", res[11+j*2:13+j*2])[0]
                    if ver != 0xFFFF:
                        versions[chunk[j]] = ver
        return versions

    @staticmethod
    def search_service(tag, idm: bytes, idx: int) -> Tuple[Optional[int], Optional[int]]:
        res = exchange(tag, CommandCode.SEARCH_SERVICE_CODE, idm, struct.pack("<H", idx))
        if res and len(res) >= 12:
            sc = struct.unpack("<H", res[10:12])[0]
            end = struct.unpack("<H", res[12:14])[0] if len(res) >= 14 else None
            return sc, end
        return None, None

    @staticmethod
    def read_block(tag, idm: bytes, service_code: int, block_num: int) -> Optional[bytes]:
        # req: [0x06, サービス数(1), サービスリスト(2*n), ブロック数(1), ブロックリスト(2*m)]
        req_data = bytes([1]) + struct.pack("<H", service_code) + bytes([1, 0x80, block_num])
        res = exchange(tag, CommandCode.READ_WITHOUT_ENCRYPTION, idm, req_data)
        if res and len(res) >= 29 and res[10] == 0x00:
            return res[13:29]
        return None

class FeliCaRenderer:
    """ツリー表示やパネル表示を担当するクラス"""

    @staticmethod
    def render_service_tree(found_sc_info: List[dict]) -> Tree:
        if not found_sc_info:
            return Tree("[red]No services found[/red]")

        root_info = found_sc_info[0]
        root_tree = Tree("[bold white]FeliCa Service/Area Tree Structure[/bold white]")
        
        root_label = Text()
        root_label.append(f"Root Area 0x{root_info['sc']:04X}", style="bold white")
        root_label.append(f" (End: 0x{root_info.get('end_val', 0xFFFE):04X})", style="dim")
        
        stack = [(root_info["sc"], root_tree.add(root_label), root_info.get("end_val", 0xFFFE))]

        for info in found_sc_info[1:]:
            sc = info["sc"]
            while len(stack) > 1 and sc > stack[-1][2]:
                stack.pop()
            
            parent_node = stack[-1][1]
            attr = info.get("type", "unknown")
            
            if attr == "area":
                end_val = info.get("end_val", 0xFFFE)
                label = Text()
                label.append(f"Area 0x{sc:04X}", style="bold cyan")
                label.append(f" (End: 0x{end_val:04X})", style="dim")
                area_node = parent_node.add(label)
                stack.append((sc, area_node, end_val))
            else:
                label = Text()
                label.append(f"0x{sc:04X}", style="green")
                label.append(f" ({attr})".ljust(22))
                label.append(f"KeyVer: 0x{info.get('key_ver', 0):04X}", style="yellow")
                label.append("  ")
                blocks = info.get("blocks", 0)
                if blocks > 0:
                    label.append(f"{blocks:>2} blocks", style="bold magenta")
                else:
                    label.append(" - blocks", style="dim")
                parent_node.add(label)
        
        return root_tree

    @staticmethod
    def render_block_panel(service_code: int, attr: str, blocks: Dict[int, bytes]) -> Panel:
        table = Table(show_header=True, box=box.SIMPLE_HEAD, header_style="bold magenta")
        table.add_column("Blk", justify="right", style="dim")
        table.add_column("Data (HEX)", style="green")
        table.add_column("ASCII", style="white")

        for blk, data in sorted(blocks.items()):
            hex_part = data.hex().upper()
            ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
            table.add_row(str(blk), hex_part, ascii_part)

        return Panel(table, title=f"[bold cyan]Service 0x{service_code:04X} ({attr})[/bold cyan]", expand=False)
