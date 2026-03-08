import pytest
from nfc_emu.base import ProtocolResult, BaseHook
from nfc_emu.felica.card import FeliCaCard
from nfc_emu.felica.protocol import FeliCaProtocol
from nfc_emu.felica.const import CommandCode, StatusFlag2

class ErrorHook(BaseHook):
    def on_polling(self, req_sc):
        raise RuntimeError("Polling Error")
    
    def on_read(self, svc, blk, data):
        raise RuntimeError("Read Error")
    
    def on_write(self, svc, blk, data):
        raise RuntimeError("Write Error")

@pytest.fixture
def protocol():
    idm = bytes.fromhex("0123456789ABCDEF")
    pmm = bytes.fromhex("0123456789ABCDEF")
    card = FeliCaCard(idm, pmm, bytes.fromhex("FE00"))
    card.add_service(0x1009, "plain")
    card.set_block(0x1009, 0, b"TEST_DATA_______")
    return FeliCaProtocol(card, hooks=ErrorHook())

def test_polling_hook_error(protocol):
    cmd = bytes([CommandCode.POLLING, 0xFF, 0xFF, 0x01, 0x00])
    result, res = protocol.handle(cmd)
    # フックエラー時は応答なし (CONTINUE)
    assert result == ProtocolResult.CONTINUE
    assert res == b""

def test_read_hook_error(protocol):
    idm = protocol.card.idm
    # Read service 0x1009, block 0
    cmd = (bytes([CommandCode.READ_WITHOUT_ENCRYPTION]) + idm + 
           bytes([1, 0x09, 0x10, 1, 0x80, 0x00]))
    result, res = protocol.handle(cmd)
    assert result == ProtocolResult.RESPONSE
    assert res[10] == 0x01 # Status1: Error
    assert res[11] == StatusFlag2.SECURITY_ERROR

def test_write_hook_error(protocol):
    idm = protocol.card.idm
    # Write service 0x1009, block 0
    cmd = (bytes([CommandCode.WRITE_WITHOUT_ENCRYPTION]) + idm + 
           bytes([1, 0x09, 0x10, 1, 0x80, 0x00]) + b"NEW_DATA________")
    result, res = protocol.handle(cmd)
    assert result == ProtocolResult.RESPONSE
    assert res[10] == 0x01 # Status1: Error
    assert res[11] == StatusFlag2.SECURITY_ERROR
    # メモリが更新されていないことを確認
    assert protocol.card.get_block(0x1009, 0) == b"TEST_DATA_______"
