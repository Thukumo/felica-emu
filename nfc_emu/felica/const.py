"""
FeliCa プロトコル定数定義
"""

from enum import IntEnum

class CommandCode(IntEnum):
    POLLING = 0x00
    REQUEST_SERVICE = 0x02
    REQUEST_RESPONSE = 0x04
    READ_WITHOUT_ENCRYPTION = 0x06
    WRITE_WITHOUT_ENCRYPTION = 0x08
    SEARCH_SERVICE_CODE = 0x0A
    REQUEST_SYSTEM_CODE = 0x0C

class ResponseCode(IntEnum):
    POLLING_RES = 0x01
    REQUEST_SERVICE_RES = 0x03
    REQUEST_RESPONSE_RES = 0x05
    READ_WITHOUT_ENCRYPTION_RES = 0x07
    WRITE_WITHOUT_ENCRYPTION_RES = 0x09
    SEARCH_SERVICE_CODE_RES = 0x0B
    REQUEST_SYSTEM_CODE_RES = 0x0D

class ServiceAttribute:
    """FeliCa サービス属性の判定 (JIS X 6319-4 準拠)"""
    
    @staticmethod
    def is_area(code: int) -> bool:
        return (code & 0x30) == 0x00
    
    @staticmethod
    def is_service(code: int) -> bool:
        return (code & 0x30) == 0x20
    
    @staticmethod
    def is_auth_required(code: int) -> bool:
        return (code & 0x08) == 0x08
        
    @staticmethod
    def is_cyclic(code: int) -> bool:
        # Bit 2-1: 00=Random, 01=Cyclic, 10=Purse Direct, 11=Purse Cashback
        return (code & 0x06) == 0x02
        
    @staticmethod
    def is_read_only(code: int) -> bool:
        return (code & 0x01) == 0x01

    @classmethod
    def from_code(cls, code: int) -> str:
        attr = code & 0x3F
        
        # Area 判定
        if cls.is_area(code):
            return "area" if not cls.is_auth_required(code) else "area-auth"
            
        if not cls.is_service(code):
            return f"unknown(0x{attr:02X})"
            
        # Service の性質判定
        parts = []
        if cls.is_auth_required(code):
            parts.append("auth")
        else:
            parts.append("plain")
            
        # サービス区分 (Bit 2-1)
        sub_type = (attr & 0x06) >> 1
        if sub_type == 0x01:
            parts.append("cyclic")
        elif sub_type >= 0x02:
            parts.append("purse")
        else:
            parts.append("random")
            
        # 読み書き権限 (Bit 0)
        if cls.is_read_only(code):
            parts.append("ro")
        else:
            parts.append("rw")
            
        return "-".join(parts)

class ErrorCode(IntEnum):
    SUCCESS = 0x00
    ERROR = 0x01

class StatusFlag2(IntEnum):
    SUCCESS = 0x00
    SECURITY_ERROR = 0xA5  # Protected サービスへのアクセス拒否
    BLOCK_ERROR = 0xA8     # 存在しないブロック（範囲外）

# パケットオフセット定義 (サイズ1バイトを除いた cmd/res 先頭からの位置)
OFFSET_CODE = 0
OFFSET_IDM = 1
OFFSET_PMM = 9

# Polling (0x00)
OFFSET_POLLING_SYS_CODE = 1

# Request Service (0x02) / Request System Code (0x0C)
OFFSET_NUM_NODES = 9
OFFSET_NODE_LIST = 10

# Read Without Encryption (0x06) / Write Without Encryption (0x08)
OFFSET_READ_NUM_SERVICES = 9
OFFSET_READ_SERVICE_LIST = 10

# Search Service Code (0x0A)
OFFSET_SEARCH_INDEX = 9
