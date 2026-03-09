import pytest
from nfc_emu.felica.card import FeliCaCard

def test_cyclic_service_memory_limit():
    idm = bytes.fromhex("0123456789ABCDEF")
    pmm = bytes.fromhex("0123456789ABCDEF")
    sys_code = bytes.fromhex("FE00")
    card = FeliCaCard(idm, pmm, sys_code)
    
    # Cyclic service with 3 blocks
    card.add_service(0x1001, "cyclic", max_blocks=3)
    
    # Write 3 blocks
    card.set_block(0x1001, 0, b"DATA_0__________")
    card.set_block(0x1001, 0, b"DATA_1__________")
    card.set_block(0x1001, 0, b"DATA_2__________")
    
    svc = card.services[0x1001]
    # Memory should have 3 blocks (0, 1, 2)
    assert len(svc.memory) == 3
    assert svc.memory[0] == b"DATA_2__________"
    assert svc.memory[1] == b"DATA_1__________"
    assert svc.memory[2] == b"DATA_0__________"
    
    # If we write more, it should discard the oldest (B2)
    card.set_block(0x1001, 0, b"DATA_3__________")
    assert len(svc.memory) == 3
    assert svc.memory[0] == b"DATA_3__________"
    assert svc.memory[1] == b"DATA_2__________"
    assert svc.memory[2] == b"DATA_1__________"
    assert 3 not in svc.memory

def test_cyclic_multi_block_write():
    from nfc_emu.felica.card import FeliCaCard
    idm = bytes.fromhex("0123456789ABCDEF")
    pmm = bytes.fromhex("0123456789ABCDEF")
    sys_code = bytes.fromhex("FE00")
    card = FeliCaCard(idm, pmm, sys_code)
    
    # 5ブロックのサイクリックサービス
    card.add_service(0x1001, "cyclic", max_blocks=5)
    svc = card.services[0x1001]
    
    # 2ブロック一括書き込み (リストの先頭が最新 B0 になる期待)
    svc.set_cyclic_blocks([b"DATA_A__________", b"DATA_B__________"])
    
    assert svc.memory[0] == b"DATA_A__________"
    assert svc.memory[1] == b"DATA_B__________"
    assert len(svc.memory) == 2
    
    # さらに1ブロック追加
    svc.set_cyclic_block(b"DATA_C__________")
    
    assert svc.memory[0] == b"DATA_C__________"
    assert svc.memory[1] == b"DATA_A__________"
    assert svc.memory[2] == b"DATA_B__________"
    assert len(svc.memory) == 3
