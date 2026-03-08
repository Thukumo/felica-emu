"""
FeliCa プロトコル定数定義
"""

from enum import IntEnum

class CommandCode(IntEnum):
    POLLING = 0x00
    REQUEST_SERVICE = 0x02
    READ_WITHOUT_ENCRYPTION = 0x06
    WRITE_WITHOUT_ENCRYPTION = 0x08
    SEARCH_SERVICE_CODE = 0x0A
    REQUEST_SYSTEM_CODE = 0x0C

class ResponseCode(IntEnum):
    POLLING_RES = 0x01
    REQUEST_SERVICE_RES = 0x03
    READ_WITHOUT_ENCRYPTION_RES = 0x07
    WRITE_WITHOUT_ENCRYPTION_RES = 0x09
    SEARCH_SERVICE_CODE_RES = 0x0B
    REQUEST_SYSTEM_CODE_RES = 0x0D

class ServiceAttribute(IntEnum):
    AREA = 0x00
    # bit0=0, attr≠0x00: Protected
    # bit0=1, bit2=0: Plain
    # bit0=1, bit2=1: Encrypted

    @classmethod
    def from_code(cls, code: int) -> str:
        attr = code & 0x3F
        if attr == 0x00:
            return "area"
        if not (attr & 0x01):
            return "protected"
        if attr & 0x04:
            return "encrypted"
        return "plain"

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
