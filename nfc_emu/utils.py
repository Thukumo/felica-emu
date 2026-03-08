"""
共通ユーティリティ
"""

import nfc
import sys
import logging

def select_from_list(options: list, title: str, default_index: int = 0):
    """
    TUI (矢印キー) でリストから一つを選択させる。
    """
    try:
        from pick import pick
        option, index = pick(options, title, indicator="=>", default_index=default_index)
        return index
    except ImportError:
        print(f"\n{title}")
        for i, opt in enumerate(options):
            print(f"  [{i}] {opt}")
        while True:
            try:
                choice = input(f"番号を入力 (0-{len(options)-1}) [default: {default_index}]: ").strip()
                if not choice: return default_index
                idx = int(choice)
                if 0 <= idx < len(options): return idx
            except ValueError: pass

def get_nfc_reader_path() -> str:
    """
    接続されている NFC リーダーのパスを取得する。
    複数のリーダーがある場合は、TUI で選択を促す。
    """
    import nfc.clf.transport
    
    # スキャン中のログを抑制 (ERROR レベルのノイズも消すため CRITICAL に設定)
    nfc_logger = logging.getLogger("nfc")
    old_level = nfc_logger.level
    nfc_logger.setLevel(logging.CRITICAL)
    
    valid_readers = []
    seen_paths = set()
    
    try:
        # nfcpy の内部トランスポートスキャナを使用
        # USB.find('usb') は (vendor_id, product_id, bus, address) のタプルを返す
        candidates = []
        try:
            for dev in nfc.clf.transport.USB.find("usb"):
                bus, address = dev[2], dev[3]
                path = f"usb:{bus:03d}:{address:03d}"
                if path not in seen_paths:
                    candidates.append(path)
                    seen_paths.add(path)
        except Exception: pass
        
        try:
            tty_devs = nfc.clf.transport.TTY.find("usb")
            if tty_devs:
                for dev in tty_devs:
                    if dev not in seen_paths:
                        candidates.append(dev)
                        seen_paths.add(dev)
        except Exception: pass

        # 実際に応答があるか確認
        for path in candidates:
            try:
                with nfc.ContactlessFrontend(path) as clf:
                    desc = str(clf.device)
                    valid_readers.append((path, desc))
            except Exception:
                continue
    finally:
        nfc_logger.setLevel(old_level)

    # 1つしか見つからない、または見つからない場合はデフォルトの "usb" を返す
    if len(valid_readers) <= 1:
        return "usb"

    options = [f"{path} ({desc})" for path, desc in valid_readers]
    idx = select_from_list(options, "--- 複数の NFC リーダーが見つかりました ---")
    return valid_readers[idx][0]

def load_hooks(path: str):
    """
    指定されたパスの Python ファイルをフックとして読み込む。
    BaseHook を継承したクラスのインスタンスを返す。
    """
    import importlib.util
    import os
    from .base import BaseHook
    
    if not os.path.exists(path):
        return None
        
    try:
        spec = importlib.util.spec_from_file_location("nfc_hooks", path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 1. 'hook' という名前の BaseHook インスタンスを探す
            if hasattr(module, "hook") and isinstance(module.hook, BaseHook):
                return module.hook
            
            # 2. BaseHook を継承したクラスを探してインスタンス化する
            for name in dir(module):
                obj = getattr(module, name)
                if (isinstance(obj, type) and issubclass(obj, BaseHook) 
                    and obj is not BaseHook):
                    return obj()
                    
            return None
    except Exception as e:
        print(f"[!] フックの読み込みに失敗しました ({path}): {e}")
    return None
