"""
FeliCa カードデータモデル
"""

from typing import Dict, List, Optional, Union, Tuple
import struct
from ..base import BaseCard
from .const import ServiceAttribute

class FeliCaService:
    def __init__(self, code: int, attr: Optional[str] = None, max_blocks: Optional[int] = None, key_version: int = 0x0000):
        self.code = code
        self.attr = attr or ServiceAttribute.from_code(code)
        self.max_blocks = max_blocks
        self.key_version = key_version
        self.memory: Dict[int, bytes] = {}

    def set_block(self, block_num: int, data: bytes):
        if len(data) != 16:
            raise ValueError("Block data must be 16 bytes")
        self.memory[block_num] = data
        if self.max_blocks is None or block_num >= self.max_blocks:
            self.max_blocks = block_num + 1

    def set_cyclic_block(self, data: bytes):
        """サイクリックサービスへの書き込み（データをシフトして最新を B0 にする）"""
        self.set_cyclic_blocks([data])

    def set_cyclic_blocks(self, data_list: List[bytes]):
        """複数のブロックをサイクリックサービスへ一括で書き込む (data_list[0] が最新として B0 になる)"""
        for data in data_list:
            if len(data) != 16:
                raise ValueError("Block data must be 16 bytes")

        num_new = len(data_list)
        if num_new == 0: return

        # 制限 (max_blocks) がある場合はそれを尊重、なければ現在の最大 + num_new
        if self.max_blocks is not None:
            limit = self.max_blocks
        else:
            limit = (max(self.memory.keys()) + 1 if self.memory else 0) + num_new
            # 動的に max_blocks を更新していく
            self.max_blocks = limit

        # データを num_new 分だけ後ろに一気にシフト
        for b in range(limit - 1, num_new - 1, -1):
            if (b - num_new) in self.memory:
                self.memory[b] = self.memory[b - num_new]
            elif b in self.memory:
                # limit を超えた古いデータは削除
                del self.memory[b]

        # データを B0..B(num_new-1) にセット
        for i, data in enumerate(data_list):
            if i < limit:
                self.memory[i] = data

    def get_block(self, block_num: int) -> Optional[bytes]:
        return self.memory.get(block_num)

class FeliCaCard(BaseCard):
    def __init__(self, idm: bytes, pmm: bytes, primary_sys_code: bytes, sys_codes: Optional[List[bytes]] = None, mode: int = 0):
        if len(idm) != 8: raise ValueError("IDm must be 8 bytes")
        if len(pmm) != 8: raise ValueError("PMm must be 8 bytes")
        if len(primary_sys_code) != 2: raise ValueError("SysCode must be 2 bytes")

        self.primary_sys_code = primary_sys_code
        self.mode = mode
        self.sys_map: Dict[int, Dict[str, bytes]] = {
            struct.unpack(">H", primary_sys_code)[0]: {"idm": idm, "pmm": pmm}
        }
        self.sys_codes_set = {struct.unpack(">H", primary_sys_code)[0]}
        
        if sys_codes:
            for sc in sys_codes:
                sc_int = struct.unpack(">H", sc)[0]
                self.sys_codes_set.add(sc_int)
                if sc_int not in self.sys_map:
                    self.sys_map[sc_int] = {"idm": idm, "pmm": pmm}

        self.services: Dict[int, FeliCaService] = {}
        self.area_ends: Dict[int, int] = {}
        self._service_list: List[int] = []

    @property
    def idm(self) -> bytes:
        return self.sys_map[struct.unpack(">H", self.primary_sys_code)[0]]["idm"]

    @property
    def pmm(self) -> bytes:
        return self.sys_map[struct.unpack(">H", self.primary_sys_code)[0]]["pmm"]

    @property
    def all_idms(self) -> List[bytes]:
        return [info["idm"] for info in self.sys_map.values()]

    @property
    def sys_codes(self) -> List[bytes]:
        return [struct.pack(">H", sc) for sc in self.sys_map.keys()]

    @property
    def service_list(self) -> List[int]:
        if self._service_list:
            return self._service_list
        # 自動生成: 0x0000 (Root Area) を先頭にし、他をソートして並べる
        codes = set(self.services.keys())
        codes.add(0x0000)
        return sorted(list(codes))

    @service_list.setter
    def service_list(self, value: List[int]):
        self._service_list = value

    def get_idm_pmm(self, sys_code_int: int) -> Tuple[bytes, bytes]:
        if sys_code_int == 0xFFFF:
            sys_code_int = struct.unpack(">H", self.primary_sys_code)[0]
        
        info = self.sys_map.get(sys_code_int)
        if info:
            return info["idm"], info["pmm"]
        return self.idm, self.pmm

    def add_service(self, code: int, attr: Optional[str] = None, max_blocks: Optional[int] = None, key_version: int = 0x0000):
        if code not in self.services:
            self.services[code] = FeliCaService(code, attr, max_blocks, key_version)
        else:
            if attr is not None:
                self.services[code].attr = attr
            if max_blocks is not None:
                self.services[code].max_blocks = max_blocks
            if key_version != 0x0000:
                self.services[code].key_version = key_version
        
        if self._service_list and code not in self._service_list:
            self._service_list.append(code)

    def set_block(self, service_code: int, block_num: int, data: bytes):
        if service_code not in self.services:
            self.add_service(service_code)
        
        svc = self.services[service_code]
        if svc.attr == "cyclic":
            svc.set_cyclic_block(data)
        else:
            svc.set_block(block_num, data)

    def get_service_attr(self, service_code: int) -> str:
        if service_code in self.services:
            return self.services[service_code].attr
        return ServiceAttribute.from_code(service_code)

    def get_area_end(self, s_code: int) -> int:
        """Area の終端コードを取得する。指定がなければ次の Area の手前を推測する。"""
        if s_code in self.area_ends:
            return self.area_ends[s_code]
        
        # 0x0000 (Root) の特殊対応
        if s_code == 0x0000:
            return 0xFFFE
        
        # 次の Area を探す
        all_codes = self.service_list
        try:
            idx = all_codes.index(s_code)
            for i in range(idx + 1, len(all_codes)):
                next_code = all_codes[i]
                if (next_code & 0x3F) == 0x00: # Area
                    return next_code - 1
        except ValueError:
            pass
            
        # 見つからない場合は従来のヒューリスティック
        return s_code | 0x00FF

    def get_block(self, service_code: int, block_num: int) -> Optional[bytes]:
        if service_code in self.services:
            return self.services[service_code].get_block(block_num)
        return None

    def to_dict(self) -> dict:
        """現在のカード状態を JSON 互換の辞書形式で出力する"""
        res = {
            "idm": self.idm.hex().upper(),
            "pmm": self.pmm.hex().upper(),
            "sys_code": self.primary_sys_code.hex().upper(),
            "mode": self.mode,
            "sys_codes": [sc.hex().upper() for sc in self.sys_codes],
            "sys_details": {
                f"{sc:04X}": {
                    "idm": info["idm"].hex().upper(),
                    "pmm": info["pmm"].hex().upper()
                } for sc, info in self.sys_map.items()
            },
            "service_list": self.service_list,
            "service_attrs": {
                str(code): self.get_service_attr(code)
                for code in self.services.keys()
            },
            "area_ends": {str(k): v for k, v in self.area_ends.items()},
            "service_versions": {
                str(code): svc.key_version for code, svc in self.services.items()
            },
            "memory": {},
            "patches": []
        }
        
        # メモリデータの構築 (データがあるサービスのみ)
        for code, svc in self.services.items():
            if svc.memory:
                svc_mem = {}
                for b_num, data in sorted(svc.memory.items()):
                    svc_mem[str(b_num)] = data.hex().upper()
                res["memory"][str(code)] = svc_mem
        
        return res

    @classmethod
    def from_dict(cls, data: dict) -> 'FeliCaCard':
        primary_idm = bytes.fromhex(data.get("idm", "0000000000000000"))
        primary_pmm = bytes.fromhex(data.get("pmm", "0000000000000000"))
        primary_sys_code = bytes.fromhex(data.get("sys_code", "FFFF"))
        mode = data.get("mode", 0)
        
        sys_codes_hex = data.get("sys_codes", [data.get("sys_code", "FFFF")])
        sys_codes = [bytes.fromhex(sc) for sc in sys_codes_hex]

        card = cls(primary_idm, primary_pmm, primary_sys_code, sys_codes, mode)

        # SCごとの個別設定があれば上書き
        if "sys_details" in data:
            for sc_hex, detail in data["sys_details"].items():
                sc_int = int(sc_hex, 16)
                card.sys_map[sc_int] = {
                    "idm": bytes.fromhex(detail.get("idm", "0000000000000000")),
                    "pmm": bytes.fromhex(detail.get("pmm", "0000000000000000"))
                }

        # service_list は任意（なければ自動生成される）
        if "service_list" in data:
            card.service_list = [int(s) for s in data["service_list"]]
            
        # area_ends の読み取り
        raw_area_ends = data.get("area_ends", {})
        card.area_ends = {int(k): int(v) for k, v in raw_area_ends.items()}

        raw_attrs = data.get("service_attrs", {})
        service_attrs = {int(k): v for k, v in raw_attrs.items()}
        
        raw_versions = data.get("service_versions", {})
        service_versions = {int(k): v for k, v in raw_versions.items()}

        memory = data.get("memory", {})
        memory_keys = {int(k) for k in memory.keys()}

        # 存在する全サービスコードの集合 (attrs, versions, memory から抽出)
        all_svc_codes = set(service_attrs.keys()) | set(service_versions.keys()) | memory_keys

        # 属性情報の流し込み
        for code in sorted(all_svc_codes):
            attr = service_attrs.get(code, "unknown")
            # メモリがあって属性がない場合は plain とみなす
            if attr == "unknown" and code in memory_keys:
                attr = "plain"
            
            card.add_service(code, attr, key_version=service_versions.get(code, 0x0000))

        # メモリの読み込み
        for svc_str, blocks in memory.items():
            svc = int(svc_str)
            if blocks:
                max_blk = max(int(b) for b in blocks.keys())
                # 既存サービスの max_blocks を更新
                card.services[svc].max_blocks = max_blk + 1
            for b_str, hex_data in blocks.items():
                card.set_block(svc, int(b_str), bytes.fromhex(hex_data))

        # Apply patches
        patches = data.get("patches", [])
        for patch in patches:
            svc_raw = patch.get("service", 0)
            svc = int(svc_raw, 0) if isinstance(svc_raw, str) else svc_raw
            blk = patch.get("block", 0)
            offset = patch.get("offset", 0)
            
            # データ取得の優先順位: hex > ascii
            if "hex" in patch:
                raw = bytes.fromhex(patch["hex"])
            elif "ascii" in patch:
                raw = patch["ascii"].encode("ascii")
            else:
                continue

            base = bytearray(card.get_block(svc, blk) or bytes(16))
            patch_len = max(0, 16 - offset)
            patch_data = raw[:patch_len]
            base[offset:offset + len(patch_data)] = patch_data
            card.set_block(svc, blk, bytes(base))

        return card
