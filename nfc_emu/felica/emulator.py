"""
FeliCa エミュレータ本体
"""

import threading
import signal
import nfc
import logging
from typing import Optional
from ..base import BaseEmulator
from .card import FeliCaCard
from .protocol import FeliCaProtocol

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich import box

logger = logging.getLogger(__name__)
console = Console()

class FeliCaEmulator(BaseEmulator):
    def __init__(self, card: FeliCaCard, hooks=None):
        self.card = card
        self.protocol = FeliCaProtocol(card, hooks=hooks)
        self.protocol.set_event_handler(self._on_protocol_event)
        self.stop_event = threading.Event()
        self._setup_signals()

    def _on_protocol_event(self, event_type: str, data: dict):
        """プロトコル層からのイベントを処理し、表示を行う"""
        if event_type == "log":
            log_type = data.get("type")
            if log_type == "Read":
                svc = data.get("service")
                range_str = data.get("range")
                console.print(f"  [bold cyan][Read][/bold cyan] 0x{svc:04X}:{range_str}")
            elif log_type == "Search":
                indices = data.get("indices")
                found = data.get("found", [])
                console.print(f"  [bold magenta][Search Service][/bold magenta] Indices {indices} -> {', '.join(found)}")

        elif event_type == "polling":
            req_sc = data.get("req_sc")
            matched_sc = data.get("matched_sc")
            tsn = data.get("tsn", 0)
            tsn_info = f" (TSN={tsn})" if tsn > 0 else ""
            console.print(f"  [bold green][Polling][/bold green] SC=0x{req_sc:04X} -> Match SC=0x{matched_sc:04X}{tsn_info}")

        elif event_type == "request_service":
            nodes = data.get("nodes", [])
            console.print(f"  [bold blue][Request Service][/bold blue] Nodes: {', '.join(nodes)}")

        elif event_type == "write":
            ops = data.get("ops", [])
            console.print(f"  [bold red][Write][/bold red] {', '.join(ops)}")

        elif event_type == "request_system_code":
            num_codes = data.get("num_codes")
            console.print(f"  [bold white][Request System Code][/bold white] Found {num_codes} codes")

        elif event_type == "trace":
            # トレースログ（DEBUG時）の装飾表示
            if logger.isEnabledFor(logging.DEBUG):
                name = data.get("name")
                color = data.get("color", "white")
                cmd = data.get("cmd", b"")
                res = data.get("res", b"")
                extra = data.get("extra", "")
                
                req_hex = cmd.hex().upper()
                res_hex = res.hex().upper() if res else "NO RESPONSE"
                tag = f"[bold {color}][{name}][/bold {color}]"
                info = f" {extra}" if extra else ""
                console.print(f"  {tag}{info} [dim]REQ:{req_hex} RES:{res_hex}[/dim]")

    def _setup_signals(self):
        try:
            signal.signal(signal.SIGINT, lambda s, f: self.stop())
        except ValueError:
            pass

    def stop(self):
        console.print("\n[bold red]Stopping emulator...[/bold red]")
        self.stop_event.set()

    def run(self, reader_path: str = "usb"):
        try:
            with nfc.ContactlessFrontend(reader_path) as clf:
                device_info = str(clf.device) if hasattr(clf, "device") else "Unknown Reader"
                
                # 起動情報の表示
                info_table = Table(box=box.ROUNDED, show_header=False)
                info_table.add_row("Device", f"[cyan]{device_info}[/cyan] ({reader_path})")
                info_table.add_row("IDm", f"[bold green]{self.card.idm.hex().upper()}[/bold green]")
                info_table.add_row("PMm", f"[green]{self.card.pmm.hex().upper()}[/green]")
                info_table.add_row("Primary SC", f"[yellow]0x{self.card.primary_sys_code.hex().upper()}[/yellow]")
                
                console.print(Panel(info_table, title="[bold blue]FeliCa Emulator[/bold blue]", expand=False))
                console.print("[italic white]Waiting for reader... (Ctrl+C to stop)[/italic white]\n")

                while not self.stop_event.is_set():
                    try:
                        clf.connect(
                            card={
                                "on-startup": self._on_startup,
                                "on-connect": self._on_connect
                            },
                            terminate=self.stop_event.is_set
                        )
                    except nfc.clf.CommunicationError:
                        pass
                    except Exception as e:
                        if self.stop_event.is_set(): break
                        console.print(f"[bold red]Unexpected error in connect loop: {e}[/bold red]")
                    finally:
                        self.protocol.flush_logs()

        except Exception as e:
            if "No such device" in str(e) or "NFC reader" in str(e):
                console.print("[bold red][!] NFC リーダー (USB) が見つかりませんでした。[/bold red]")
            else:
                console.print(f"[bold red][!] エミュレータ実行中にエラーが発生しました: {e}[/bold red]")
            return

    def _on_startup(self, target):
        target.brty = "212F"
        target.sensf_res = b"\x01" + self.card.idm + self.card.pmm + self.card.primary_sys_code
        return target

    def _on_connect(self, tag):
        from ..base import ProtocolResult
        console.print("[bold blue]>>> Reader Detected: Session Started <<<[/bold blue]")
        cmd_data = tag.cmd
        while cmd_data:
            # データの正規化: 長さバイトが含まれていれば除去
            cmd = cmd_data[1:] if cmd_data[0] == len(cmd_data) else cmd_data
            
            result, response = self.protocol.handle(cmd)

            if result == ProtocolResult.UNKNOWN:
                code = cmd[0] if cmd else None
                console.print(f"  [yellow][?] Unknown Command (0x{code:02X}?) -> No response[/yellow]")
                try:
                    cmd_data = tag.clf.exchange(None, timeout=2.0)
                except nfc.clf.CommunicationError as e:
                    self._log_comm_error(e)
                    break
            elif result == ProtocolResult.CONTINUE:
                try:
                    cmd_data = tag.clf.exchange(None, timeout=2.0)
                except nfc.clf.CommunicationError as e:
                    self._log_comm_error(e)
                    break
            elif result == ProtocolResult.RESPONSE:
                try:
                    cmd_data = tag.clf.exchange(response, timeout=2.0)
                except nfc.clf.CommunicationError as e:
                    self._log_comm_error(e)
                    break
            else: # ProtocolResult.ERROR
                break

        console.print("[bold blue]<<< Session Finished >>>[/bold blue]\n")
        return False

    def _log_comm_error(self, e: Exception):
        estr = str(e)
        if "RF_OFF" in estr or "RECEIVE_TIMEOUT" in estr:
            logger.debug("  [*] Session ended normally (RF_OFF)")
        else:
            console.print(f"  [dim yellow][!] Session ended: {e}[/dim yellow]")

