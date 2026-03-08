import pytest
import struct
from nfc_emu.base import ProtocolResult, BaseHook
from nfc_emu.felica.card import FeliCaCard
from nfc_emu.felica.protocol import FeliCaProtocol
from nfc_emu.felica.const import CommandCode, ResponseCode, StatusFlag2

@pytest.fixture
def sample_card():
    idm = bytes.fromhex("0123456789ABCDEF")
    pmm = bytes.fromhex("0123456789ABCDEF")
    sys_code = bytes.fromhex("FE00")
    card = FeliCaCard(idm, pmm, sys_code)
    
    # サービスとデータのセットアップ
    # 0x100B: Plain R-only
    card.add_service(0x100B, "plain")
    card.set_block(0x100B, 0, b"DATA_BLOCK_0____")
    card.set_block(0x100B, 1, b"DATA_BLOCK_1____")
    
    # 0x1009: Plain R/W
    card.add_service(0x1009, "plain")
    card.set_block(0x1009, 0, b"RW_BLOCK_0______")
    
    # 0x1008: Protected
    card.add_service(0x1008, "protected")
    
    return card

@pytest.fixture
def protocol(sample_card):
    return FeliCaProtocol(sample_card)

def test_polling_exact_sc(protocol, sample_card):
    # Polling for 0xFE00
    cmd = bytes([CommandCode.POLLING, 0xFE, 0x00, 0x01, 0x00])
    result, res = protocol.handle(cmd)
    
    assert result == ProtocolResult.RESPONSE
    assert res[1] == ResponseCode.POLLING_RES
    assert res[2:10] == sample_card.idm
    assert res[18:20] == bytes.fromhex("FE00")

def test_polling_wildcard_sc(protocol, sample_card):
    # Polling for 0xFFFF
    cmd = bytes([CommandCode.POLLING, 0xFF, 0xFF, 0x01, 0x00])
    result, res = protocol.handle(cmd)
    
    assert result == ProtocolResult.RESPONSE
    assert res[18:20] == bytes.fromhex("FE00")

def test_polling_mismatch_sc(protocol):
    # Polling for un registered SC
    cmd = bytes([CommandCode.POLLING, 0x12, 0x34, 0x01, 0x00])
    result, res = protocol.handle(cmd)
    
    assert result == ProtocolResult.CONTINUE
    assert res == b""

def test_request_service(protocol):
    # Request Service for 0x100B (exists) and 0x9999 (not exists)
    cmd = bytes([CommandCode.REQUEST_SERVICE, 0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00, 
                 2, 0x0B, 0x10, 0x99, 0x99])
    # IDm check is skipped in handle() for convenience if not matching current_idm, 
    # but protocol.current_idm is initialized to card.idm.
    
    # Update current_idm to match "zero" IDm in cmd for this test or vice versa
    protocol.current_idm = b"\x00" * 8
    
    result, res = protocol.handle(cmd)
    assert result == ProtocolResult.RESPONSE
    assert res[10] == 2 # num_nodes
    assert res[11:13] == b"\x00\x00" # 0x100B exists
    assert res[13:15] == b"\xFF\xFF" # 0x9999 not exists

def test_read_without_encryption_success(protocol, sample_card):
    protocol.current_idm = sample_card.idm
    # Read 1 service (0x100B), 1 block (No. 0)
    # [Code][IDm(8)][NumS(1)][SList(2)][NumB(1)][BList(2)]
    cmd = (bytes([CommandCode.READ_WITHOUT_ENCRYPTION]) + sample_card.idm + 
           bytes([1, 0x0B, 0x10, 1, 0x80, 0x00]))
    
    result, res = protocol.handle(cmd)
    assert result == ProtocolResult.RESPONSE
    assert res[10] == 0x00 # Status1
    assert res[11] == 0x00 # Status2
    assert res[13:29] == b"DATA_BLOCK_0____"

def test_read_protected_service(protocol, sample_card):
    protocol.current_idm = sample_card.idm
    # Read 0x1008 (protected)
    cmd = (bytes([CommandCode.READ_WITHOUT_ENCRYPTION]) + sample_card.idm + 
           bytes([1, 0x08, 0x10, 1, 0x80, 0x00]))
    
    result, res = protocol.handle(cmd)
    assert result == ProtocolResult.RESPONSE
    assert res[10] == 0x01 # Error
    assert res[11] == StatusFlag2.SECURITY_ERROR

def test_write_without_encryption_success(protocol, sample_card):
    protocol.current_idm = sample_card.idm
    new_data = b"NEW_DATA_WRITTEN"
    # Write to 0x1009 (R/W)
    cmd = (bytes([CommandCode.WRITE_WITHOUT_ENCRYPTION]) + sample_card.idm + 
           bytes([1, 0x09, 0x10, 1, 0x80, 0x00]) + new_data)
    
    result, res = protocol.handle(cmd)
    assert result == ProtocolResult.RESPONSE
    assert res[10] == 0x00 # Success
    assert sample_card.get_block(0x1009, 0) == new_data

def test_search_service_code(protocol, sample_card):
    protocol.current_idm = sample_card.idm
    # Index 0 is Root Area 0x0000 by default in service_list if auto-generated
    cmd = (bytes([CommandCode.SEARCH_SERVICE_CODE]) + sample_card.idm + 
           struct.pack("<H", 0))
    
    result, res = protocol.handle(cmd)
    assert result == ProtocolResult.RESPONSE
    # 0x0000 is Area (14 bytes response)
    assert len(res) == 14
    assert struct.unpack("<H", res[10:12])[0] == 0x0000

def test_request_response(protocol, sample_card):
    protocol.current_idm = sample_card.idm
    sample_card.mode = 0x01 # Authentication mode
    
    cmd = bytes([CommandCode.REQUEST_RESPONSE]) + sample_card.idm
    result, res = protocol.handle(cmd)
    
    assert result == ProtocolResult.RESPONSE
    assert res[1] == ResponseCode.REQUEST_RESPONSE_RES
    assert res[2:10] == sample_card.idm
    assert res[10] == 0x01

def test_unknown_command(protocol, sample_card):
    cmd = bytes([0xFE, 0x01, 0x02]) # Unknown code 0xFE
    result, res = protocol.handle(cmd)
    assert result == ProtocolResult.UNKNOWN
    assert res is None

def test_hook_interception(sample_card):
    class InterceptHook(BaseHook):
        def on_command(self, code, cmd):
            if code == 0x98:
                return b"\x05\x9A\x00\x01\x02"
            return None
            
    proto = FeliCaProtocol(sample_card, hooks=InterceptHook())
    result, res = proto.handle(b"\x98\x01")
    assert result == ProtocolResult.RESPONSE
    assert res == b"\x05\x9A\x00\x01\x02"
