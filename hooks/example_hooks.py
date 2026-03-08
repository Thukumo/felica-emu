"""
FeliCa エミュレータ フックスクリプトの例
"""
import datetime
from typing import Optional, Union
from nfc_emu.base import BaseHook

class MyCustomHook(BaseHook):
    def on_read(self, service: int, block: int, data: bytes) -> Optional[bytes]:
        """
        データ読み取り時に呼ばれるフック。
        特定のサービスが読まれたら現在時刻を返す例。
        """
        if service == 0x100B:
            now = datetime.datetime.now().strftime("%Y%m%d%H%M%S").encode()
            return now.ljust(16, b"\x00")
        
        return None

    def on_write(self, service: int, block: int, data: bytes) -> Union[bool, bytes, None]:
        """
        データ書き込み時に呼ばれるフック。
        書き込みをトリガーに何か処理をする例。
        """
        print(f"  [Hook] Write access to 0x{service:04X}:B{block}")
        return True

    def on_command(self, code: int, cmd: bytes) -> Optional[bytes]:
        """
        低レベルコマンドフック。
        未実装のコマンド (例: Read With Encryption 0x10) を処理できる。
        """
        if code == 0x10: # Read With Encryption
            print(f"  [Hook] Intercepted Read With Encryption!")
            # 常にダミーの成功レスポンスを返す（例）
            # [Size][0x11][IDm(8)][Status1][Status2][NumBlocks][Data...]
            idm = cmd[1:9]
            res = bytes([13, 0x11]) + idm + b"\x00\x00\x00" 
            return res
        
        return None

# フックのインスタンスを作成
hook = MyCustomHook()
