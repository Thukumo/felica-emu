"""
NFC エミュレーションの基底クラス定義
"""

from abc import ABC, abstractmethod
from typing import Optional, Union, Tuple, Callable, Any
from enum import Enum, auto

class ProtocolResult(Enum):
    """プロトコルハンドラの処理結果ステータス"""
    RESPONSE = auto()    # 応答データあり
    CONTINUE = auto()    # 無応答でセッション継続 (SC不一致、エコーバック等)
    UNKNOWN = auto()     # 未知のコマンド
    ERROR = auto()       # プロトコルエラー（切断を推奨）

class BaseHook:
    """エミュレーション中に特定のイベントに介入するためのフック基底クラス"""
    
    def on_command(self, code: int, cmd: bytes) -> Optional[bytes]:
        """
        全コマンドに対する低レベルフック。
        
        Args:
            code (int): コマンドコード
            cmd (bytes): 受信した生データ (長さバイトなし)
            
        Returns:
            Optional[bytes]: 応答データを返すと、プロトコルの標準処理をスキップしてその応答を返す。
        """
        return None

    def on_polling(self, req_sc: int) -> Optional[dict]:
        """
        Polling (0x00) 時のフック。
        
        Args:
            req_sc (int): リクエストされたシステムコード
            
        Returns:
            Optional[dict]: {"idm": bytes, "pmm": bytes} を返すと、その IDm/PMm で応答する。
        """
        return None

    def on_read(self, service: int, block: int, data: bytes) -> Optional[bytes]:
        """
        Read Without Encryption (0x06) での読み取り時フック。
        
        Args:
            service (int): サービスコード
            block (int): ブロック番号
            data (bytes): 読み取られた元のデータ (16 bytes)
            
        Returns:
            Optional[bytes]: 16バイトのデータを返すと、そのデータで応答を上書きする。
        """
        return None

    def on_write(self, service: int, block: int, data: bytes) -> Union[bool, bytes, None]:
        """
        Write Without Encryption (0x08) での書き込み時フック。
        
        Args:
            service (int): サービスコード
            block (int): ブロック番号
            data (bytes): 書き込まれようとしているデータ (16 bytes)
            
        Returns:
            Union[bool, bytes, None]:
                False: 書き込みを拒否し、Security Error を返す。
                bytes: 16バイトのデータを返すと、そのデータでメモリを書き換える。
                None または True: 通常通り書き込みを許可する。
        """
        return None

class BaseCard(ABC):
    """カードデータの抽象クラス"""
    @abstractmethod
    def from_dict(cls, data: dict) -> 'BaseCard':
        pass

class BaseProtocol(ABC):
    """プロトコルハンドラの抽象クラス"""
    def __init__(self):
        self._event_handler: Optional[Callable[[str, Any], None]] = None

    def set_event_handler(self, handler: Callable[[str, Any], None]):
        """イベントハンドラを登録する (UI層などが購読するために使用)"""
        self._event_handler = handler

    def _emit_event(self, event_type: str, data: Any):
        """イベントを発行する"""
        if self._event_handler:
            self._event_handler(event_type, data)

    @abstractmethod
    def handle(self, cmd: bytes) -> Tuple[ProtocolResult, Optional[bytes]]:
        """
        コマンドペイロードを処理し、結果ステータスとレスポンスを返す。
        
        Args:
            cmd (bytes): 受信したコマンドペイロード (長さバイトなし)
            
        Returns:
            Tuple[ProtocolResult, Optional[bytes]]: (ステータス, 応答データ)
        """
        pass

    def flush_logs(self):
        """バッファリングされているログがあれば出力する"""
        pass

class BaseEmulator(ABC):
    """エミュレータ本体の抽象クラス"""
    @abstractmethod
    def run(self):
        """エミュレータを起動する"""
        pass

    @abstractmethod
    def stop(self):
        """エミュレータを停止する"""
        pass
