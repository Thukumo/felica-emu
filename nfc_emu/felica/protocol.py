"""
FeliCa プロトコルのパースとレスポンス構築
"""

import struct
import logging
from typing import Optional, List, Tuple
from ..base import BaseProtocol, ProtocolResult, BaseHook
from .const import (
    CommandCode, ResponseCode, StatusFlag2, ServiceAttribute,
    OFFSET_CODE, OFFSET_IDM, OFFSET_PMM, OFFSET_POLLING_SYS_CODE,
    OFFSET_NUM_NODES, OFFSET_NODE_LIST, OFFSET_READ_NUM_SERVICES,
    OFFSET_READ_SERVICE_LIST, OFFSET_SEARCH_INDEX
)
from .card import FeliCaCard

logger = logging.getLogger(__name__)

class FeliCaProtocol(BaseProtocol):
    def __init__(self, card: FeliCaCard, hooks: Optional[BaseHook] = None):
        super().__init__()
        self.card = card
        self.hooks = hooks or BaseHook()
        # セッション内で最後に Polling 応答した IDm/PMm を保持
        self.current_idm = card.idm
        self.current_pmm = card.pmm
        
        # コマンドディスパッチテーブル
        self._handlers = {
            CommandCode.POLLING: self._handle_polling,
            CommandCode.REQUEST_SERVICE: self._handle_request_service,
            CommandCode.REQUEST_RESPONSE: self._handle_request_response,
            CommandCode.READ_WITHOUT_ENCRYPTION: self._handle_read_without_encryption,
            CommandCode.WRITE_WITHOUT_ENCRYPTION: self._handle_write_without_encryption,
            CommandCode.SEARCH_SERVICE_CODE: self._handle_search_service_code,
            CommandCode.REQUEST_SYSTEM_CODE: self._handle_request_system_code,
        }

        # ログバッファ用
        self._reset_log_state()

    def _reset_log_state(self):
        self._log_type = None
        self._log_svc = None
        self._log_start = None
        self._log_end = None
        self._log_extra = []

    def flush_logs(self):
        if not self._log_type:
            return
        
        # 概要ログイベントの発行
        if self._log_type == "Read":
            range_str = f"B{self._log_start}-{self._log_end}" if self._log_start != self._log_end else f"B{self._log_start}"
            self._emit_event("log", {"type": "Read", "service": self._log_svc, "range": range_str})
        elif self._log_type == "Search":
            idx_range = f"{self._log_start}-{self._log_end}" if self._log_start != self._log_end else f"{self._log_start}"
            self._emit_event("log", {"type": "Search", "indices": idx_range, "found": self._log_extra})
        
        self._reset_log_state()

    def _trace_packet(self, name: str, color: str, cmd: bytes, res: bytes, extra: str = ""):
        """全パケットを詳細に出力する (DEBUG モード用)"""
        req_hex = cmd.hex().upper()
        res_hex = res.hex().upper() if res else "NO RESPONSE"
        
        # ロギングモジュールに出力
        logger.debug(f"[{name}] {extra} REQ:{req_hex} RES:{res_hex}")
        
        # 詳細トレースイベントの発行
        self._emit_event("trace", {
            "name": name, "color": color, "cmd": cmd, "res": res, "extra": extra
        })

    def handle(self, cmd: bytes) -> Tuple[ProtocolResult, Optional[bytes]]:
        """
        受信したコマンドペイロードを処理し、応答を構築する。
        cmd: 生のコマンドデータ (長さバイトなし)
        """
        if not cmd:
            return ProtocolResult.ERROR, None

        code = cmd[OFFSET_CODE]

        # エコーバック対策: レスポンスコード（奇数）なら無視
        if code & 0x01:
            return ProtocolResult.CONTINUE, b""

        # --- Low-level Hook: on_command ---
        try:
            hook_res = self.hooks.on_command(code, cmd)
            if hook_res is not None:
                self._trace_packet("Hook:Cmd", "yellow", cmd, hook_res, f"code=0x{code:02X}")
                return ProtocolResult.RESPONSE, hook_res
        except Exception as e:
            logger.error(f"on_command hook error: {e}")

        # DEBUG モードなら集約せず、即座にフラッシュ
        is_debug = logger.isEnabledFor(logging.DEBUG)
        if is_debug:
            self.flush_logs()

        # 異なるコマンドならフラッシュ (通常モード用)
        should_flush = (
            code not in (CommandCode.READ_WITHOUT_ENCRYPTION, CommandCode.SEARCH_SERVICE_CODE) or
            (code == CommandCode.SEARCH_SERVICE_CODE and self._log_type != "Search") or
            (code == CommandCode.READ_WITHOUT_ENCRYPTION and self._log_type != "Read")
        )
        if not is_debug and should_flush:
            self.flush_logs()

        handler = self._handlers.get(code)
        if not handler:
            return ProtocolResult.UNKNOWN, None

        # IDm チェック (Polling 以外)
        if code != CommandCode.POLLING and len(cmd) >= 9:
            target_idm = cmd[OFFSET_IDM:OFFSET_IDM+8]
            # 最後に Polling した IDm か、またはカードが持ついずれかの IDm と一致するか確認
            if target_idm != self.current_idm and target_idm not in self.card.all_idms:
                # 別の IDm 宛てのコマンドは無視
                return ProtocolResult.CONTINUE, b""

        res = handler(cmd)
        if res is None:
            return ProtocolResult.CONTINUE, b""
        
        # DEBUG モードなら詳細出力
        if is_debug and code not in (CommandCode.POLLING, CommandCode.REQUEST_SERVICE, CommandCode.REQUEST_SYSTEM_CODE):
            if code == CommandCode.READ_WITHOUT_ENCRYPTION:
                if self._log_type == "Read":
                    self._trace_packet("Read", "cyan", cmd, res, f"0x{self._log_svc:04X}:B{self._log_start}")
                    self._reset_log_state()
            elif code == CommandCode.SEARCH_SERVICE_CODE:
                if self._log_type == "Search":
                    self._trace_packet("Search Service", "magenta", cmd, res, f"Index {self._log_start} -> {self._log_extra[0]}")
                    self._reset_log_state()
            elif code == CommandCode.WRITE_WITHOUT_ENCRYPTION:
                self._trace_packet("Write", "red", cmd, res)

        return ProtocolResult.RESPONSE, res

    def _handle_polling(self, cmd: bytes) -> Optional[bytes]:
        if len(cmd) < 3: return None
        req_sc = struct.unpack(">H", cmd[OFFSET_POLLING_SYS_CODE:OFFSET_POLLING_SYS_CODE+2])[0]
        
        # TSN (Time Slot Number)
        tsn = cmd[4] if len(cmd) >= 5 else 0
        
        idm, pmm = self.card.get_idm_pmm(req_sc)
        
        # --- Hook: on_polling ---
        try:
            hook_res = self.hooks.on_polling(req_sc)
            if isinstance(hook_res, dict):
                if "idm" in hook_res: idm = hook_res["idm"]
                if "pmm" in hook_res: pmm = hook_res["pmm"]
                logger.debug("[Hook:Polling] Overrode IDm/PMm")
        except Exception as e:
            logger.error(f"on_polling hook error: {e}")
            return None # フックエラー時は応答しない

        # 0xFFFF または登録済みの SC の場合のみ応答
        if req_sc == 0xFFFF or req_sc in self.card.sys_codes_set:
            self.current_idm = idm
            self.current_pmm = pmm
            sc_bytes = self.card.primary_sys_code if req_sc == 0xFFFF else struct.pack(">H", req_sc)
            sc_val = struct.unpack(">H", sc_bytes)[0]
            
            res = struct.pack("BB8s8s2s", 20, ResponseCode.POLLING_RES, idm, pmm, sc_bytes)
            
            # Polling イベント発行
            self._emit_event("polling", {"req_sc": req_sc, "matched_sc": sc_val, "tsn": tsn})
            
            self._trace_packet("Polling", "green", cmd, res, f"SC=0x{req_sc:04X} -> Match SC=0x{sc_val:04X}")
            return res
        
        self._trace_packet("Polling", "white", cmd, b"", f"SC=0x{req_sc:04X} -> No Match")
        return None

    def _handle_request_service(self, cmd: bytes) -> bytes:
        if len(cmd) < OFFSET_NUM_NODES + 1:
            return b""
        num_nodes = cmd[OFFSET_NUM_NODES]
        if len(cmd) < OFFSET_NODE_LIST + num_nodes * 2:
            return b""
        versions = b""
        nodes = []
        for i in range(num_nodes):
            s_code = struct.unpack("<H", cmd[OFFSET_NODE_LIST + i * 2:OFFSET_NODE_LIST + 2 + i * 2])[0]
            nodes.append(f"0x{s_code:04X}")
            
            if s_code in self.card.services:
                ver = self.card.services[s_code].key_version
                versions += struct.pack("<H", ver)
            elif s_code in self.card.service_list:
                # メモリはないがリストにはある場合 (0x0000 など)
                versions += b"\x00\x00"
            else:
                versions += b"\xFF\xFF"
        
        res = struct.pack("BB8sB", 11 + 2 * num_nodes, ResponseCode.REQUEST_SERVICE_RES, self.current_idm, num_nodes) + versions
        
        # Request Service イベント発行
        self._emit_event("request_service", {"nodes": nodes})
        
        self._trace_packet("Request Service", "blue", cmd, res, f"Nodes: {', '.join(nodes)}")
        return res

    def _handle_request_response(self, cmd: bytes) -> bytes:
        res = struct.pack("BB8sB", 11, ResponseCode.REQUEST_RESPONSE_RES, self.current_idm, self.card.mode)
        
        # Request Response イベント発行
        self._emit_event("request_response", {"mode": self.card.mode})
        
        self._trace_packet("Request Response", "yellow", cmd, res, f"Mode: {self.card.mode}")
        return res

    def _parse_block_list(self, cmd: bytes, num_s: int, offset: int) -> Optional[Tuple[List[Tuple[int, int]], int]]:
        """ブロックリスト要素を解析するヘルパー"""
        num_b = cmd[offset]
        offset += 1
        block_list = []
        for _ in range(num_b):
            if len(cmd) < offset + 1: return None
            b_info = cmd[offset]
            if b_info & 0x80: # 2-byte format
                if len(cmd) < offset + 2: return None
                s_idx = (b_info & 0x78) >> 3
                b_num = cmd[offset + 1]
                offset += 2
            else: # 3-byte format
                if len(cmd) < offset + 3: return None
                s_idx = (b_info & 0x78) >> 3
                # Byte 2 (下位) と Byte 3 (上位) の計16ビットをブロック番号として読み取る
                # Bit 0-2 (Access Mode) はブロック番号には含まれない
                b_num = struct.unpack("<H", cmd[offset + 1:offset + 3])[0]
                offset += 3
            block_list.append((s_idx, b_num))
        return block_list, offset

    def _handle_read_without_encryption(self, cmd: bytes) -> Optional[bytes]:
        if len(cmd) < OFFSET_READ_NUM_SERVICES + 1:
            return None
        num_s = cmd[OFFSET_READ_NUM_SERVICES]
        if len(cmd) < OFFSET_READ_SERVICE_LIST + num_s * 2 + 1:
            return None
        services = [
            struct.unpack("<H", cmd[OFFSET_READ_SERVICE_LIST + i * 2:OFFSET_READ_SERVICE_LIST + 2 + i * 2])[0]
            for i in range(num_s)
        ]

        parsed = self._parse_block_list(cmd, num_s, OFFSET_READ_SERVICE_LIST + 2 * num_s)
        if not parsed: return None
        block_list, _ = parsed
        num_b = len(block_list)

        if num_b > 15:
            logger.warning(f"Read: Too many blocks ({num_b})")
            return self._error_response(ResponseCode.READ_WITHOUT_ENCRYPTION_RES, StatusFlag2.BLOCK_ERROR)

        # --- 事前バリデーション ---
        for s_idx, b_num in block_list:
            if s_idx >= len(services):
                return self._error_response(ResponseCode.READ_WITHOUT_ENCRYPTION_RES, StatusFlag2.BLOCK_ERROR)
            svc = services[s_idx]
            if ServiceAttribute.is_auth_required(svc):
                return self._error_response(ResponseCode.READ_WITHOUT_ENCRYPTION_RES, StatusFlag2.SECURITY_ERROR)
            if self.card.get_block(svc, b_num) is None:
                return self._error_response(ResponseCode.READ_WITHOUT_ENCRYPTION_RES, StatusFlag2.BLOCK_ERROR)

        # --- 実際の読み取り ---
        payload = b""
        for s_idx, b_num in block_list:
            svc = services[s_idx]
            block_data = self.card.get_block(svc, b_num)
            
            # --- Hook: on_read ---
            try:
                hook_res = self.hooks.on_read(svc, b_num, block_data)
                if hook_res is not None and isinstance(hook_res, bytes) and len(hook_res) == 16:
                    block_data = hook_res
            except Exception as e:
                logger.error(f"on_read hook error: {e}")
                return self._error_response(ResponseCode.READ_WITHOUT_ENCRYPTION_RES, StatusFlag2.SECURITY_ERROR)

            # ログバッファの更新
            if self._log_type == "Read" and self._log_svc == svc and b_num == self._log_end + 1:
                self._log_end = b_num
            else:
                if not logger.isEnabledFor(logging.DEBUG): self.flush_logs()
                self._log_type, self._log_svc, self._log_start, self._log_end = "Read", svc, b_num, b_num

            payload += block_data
        
        res_len = 13 + len(payload)
        return struct.pack("BB8sBBB", res_len, ResponseCode.READ_WITHOUT_ENCRYPTION_RES, self.current_idm, 0x00, 0x00, num_b) + payload

    def _handle_write_without_encryption(self, cmd: bytes) -> Optional[bytes]:
        if len(cmd) < OFFSET_READ_NUM_SERVICES + 1:
            return None
        num_s = cmd[OFFSET_READ_NUM_SERVICES]
        if len(cmd) < OFFSET_READ_SERVICE_LIST + num_s * 2 + 1:
            return None
        services = [
            struct.unpack("<H", cmd[OFFSET_READ_SERVICE_LIST + i * 2:OFFSET_READ_SERVICE_LIST + 2 + i * 2])[0]
            for i in range(num_s)
        ]

        parsed = self._parse_block_list(cmd, num_s, OFFSET_READ_SERVICE_LIST + 2 * num_s)
        if not parsed: return None
        block_list, offset = parsed
        num_b = len(block_list)

        if num_b > 8:
            logger.warning(f"Write: Too many blocks ({num_b})")
            return self._error_response(ResponseCode.WRITE_WITHOUT_ENCRYPTION_RES, StatusFlag2.BLOCK_ERROR)

        # 書き込みデータのパース
        block_data_list = []
        for _ in range(num_b):
            if len(cmd) < offset + 16: return None
            block_data_list.append(cmd[offset:offset + 16])
            offset += 16

        # --- 事前バリデーション ---
        ops = []
        for i, (s_idx, b_num) in enumerate(block_list):
            if s_idx >= len(services):
                return self._error_response(ResponseCode.WRITE_WITHOUT_ENCRYPTION_RES, StatusFlag2.BLOCK_ERROR)
            svc = services[s_idx]
            data = block_data_list[i]
            
            # 属性チェック (JIS X 6319-4 に基づき、ReadOnly ビットをチェック)
            if ServiceAttribute.is_read_only(svc):
                return self._error_response(ResponseCode.WRITE_WITHOUT_ENCRYPTION_RES, StatusFlag2.SECURITY_ERROR)
            
            # --- Hook: on_write ---
            try:
                hook_res = self.hooks.on_write(svc, b_num, data)
                if hook_res is False:
                    return self._error_response(ResponseCode.WRITE_WITHOUT_ENCRYPTION_RES, StatusFlag2.SECURITY_ERROR)
                if isinstance(hook_res, bytes) and len(hook_res) == 16:
                    data = hook_res
            except Exception as e:
                logger.error(f"on_write hook error: {e}")
                return self._error_response(ResponseCode.WRITE_WITHOUT_ENCRYPTION_RES, StatusFlag2.SECURITY_ERROR)
            
            ops.append((svc, b_num, data))

        # --- 実際の書き込み (全バリデーション通過後) ---
        # 連続する同じサービスへの書き込みをグループ化 (サイクリック対応)
        service_groups = []
        if ops:
            current_svc = ops[0][0]
            current_ops = []
            for svc, b_num, data in ops:
                if svc == current_svc:
                    current_ops.append((b_num, data))
                else:
                    service_groups.append((current_svc, current_ops))
                    current_svc = svc
                    current_ops = [(b_num, data)]
            service_groups.append((current_svc, current_ops))

        write_log_ops = []
        for svc_code, svc_ops in service_groups:
            attr = self.card.get_service_attr(svc_code)
            if attr == "cyclic":
                # サイクリックサービス: ブロックリスト順に B0, B1... となるように一括シフト書き込み
                data_list = [d for _, d in svc_ops]
                if svc_code not in self.card.services:
                    self.card.add_service(svc_code, attr="cyclic")
                self.card.services[svc_code].set_cyclic_blocks(data_list)
                for b_num, _ in svc_ops:
                    write_log_ops.append(f"0x{svc_code:04X}:B{b_num}")
            else:
                # 通常サービス: 指定されたブロック番号に書き込み
                for b_num, data in svc_ops:
                    self.card.set_block(svc_code, b_num, data)
                    write_log_ops.append(f"0x{svc_code:04X}:B{b_num}")

        self._emit_event("write", {"ops": write_log_ops})
        self._trace_packet("Write", "red", cmd, b"", f"Ops: {', '.join(write_log_ops)}")
        return struct.pack("BB8sBB", 12, ResponseCode.WRITE_WITHOUT_ENCRYPTION_RES, self.current_idm, 0x00, 0x00)

    def _handle_search_service_code(self, cmd: bytes) -> Optional[bytes]:
        if len(cmd) < OFFSET_SEARCH_INDEX + 2:
            return None
        idx = struct.unpack("<H", cmd[OFFSET_SEARCH_INDEX:OFFSET_SEARCH_INDEX+2])[0]
        if idx < len(self.card.service_list):
            s_code = self.card.service_list[idx]
            
            if self._log_type == "Search" and idx == self._log_end + 1:
                self._log_end = idx
                self._log_extra.append(f"0x{s_code:04X}")
            else:
                if not logger.isEnabledFor(logging.DEBUG):
                    self.flush_logs()
                self._log_type = "Search"
                self._log_start = idx
                self._log_end = idx
                self._log_extra = [f"0x{s_code:04X}"]

            if (s_code & 0x3F) == 0x00: # Area
                end_area = self.card.get_area_end(s_code)
                return struct.pack("BB8s", 14, ResponseCode.SEARCH_SERVICE_CODE_RES, self.current_idm) + struct.pack("<HH", s_code, end_area)
            return struct.pack("BB8s", 12, ResponseCode.SEARCH_SERVICE_CODE_RES, self.current_idm) + struct.pack("<H", s_code)
        else:
            if not logger.isEnabledFor(logging.DEBUG):
                self.flush_logs()
            return struct.pack("BB8s", 12, ResponseCode.SEARCH_SERVICE_CODE_RES, self.current_idm) + b"\xFF\xFF"

    def _handle_request_system_code(self, cmd: bytes) -> bytes:
        n = len(self.card.sys_codes)
        res_len = 11 + 2 * n
        payload = b"".join(self.card.sys_codes)
        res = struct.pack("BB8sB", res_len, ResponseCode.REQUEST_SYSTEM_CODE_RES, self.current_idm, n) + payload
        
        # Request System Code イベント発行
        self._emit_event("request_system_code", {"num_codes": n})
        
        self._trace_packet("Request System Code", "white", cmd, res, f"Found {n} codes")
        return res

    def _error_response(self, res_code: int, sf2: int) -> bytes:
        return struct.pack("BB8sBB", 12, res_code, self.current_idm, 0x01, sf2)
