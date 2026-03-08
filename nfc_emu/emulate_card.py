"""
emulate_card.py - dump_card.py で保存したJSONを読み込み、RC-S380でFeliCaをエミュレートする

使い方:
    python emulate_card.py              # cards/ から対話選択
    python emulate_card.py <ファイル>   # 直接指定
"""

import sys
import os
import json
import logging
from .felica.card import FeliCaCard
from .felica.emulator import FeliCaEmulator

def select_card(cards_dir="cards"):
    """cards/ 内の JSON を一覧表示して選ばせる"""
    if not os.path.isdir(cards_dir):
        return None
    files = sorted(f for f in os.listdir(cards_dir) if f.endswith(".json"))
    if not files:
        return None

    from .utils import select_from_list
    idx = select_from_list(files, "--- カードデータを選択してください ---")
    return os.path.join(cards_dir, files[idx])

def main():
    import argparse
    from rich.logging import RichHandler
    from rich.console import Console
    
    parser = argparse.ArgumentParser(description="FeliCa エミュレータ")
    parser.add_argument("input", nargs="?", help="JSON カードデータファイルのパス")
    parser.add_argument("-v", "--verbose", action="store_true", help="全パケットの生データをトレース表示")
    parser.add_argument("--hooks", help="Python フックスクリプトのパス")
    args = parser.parse_args()

    # デフォルトで概要ログを表示するため INFO に設定
    # -v があれば DEBUG (Trace) モード
    log_level = logging.DEBUG if args.verbose else logging.INFO
    
    # RichHandler を使ってログ出力をリッチにする
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=Console(stderr=False), markup=True)]
    )
    logging.getLogger("nfc").setLevel(logging.WARNING)
    
    console = Console()

    # フックの読み込み
    hooks = None
    if args.hooks:
        hook_path = args.hooks
        if not os.path.exists(hook_path):
            alt_path = os.path.join("hooks", hook_path)
            if not alt_path.endswith(".py"):
                alt_path += ".py"
            if os.path.exists(alt_path):
                hook_path = alt_path
            elif not hook_path.endswith(".py") and os.path.exists(hook_path + ".py"):
                hook_path += ".py"

        from .utils import load_hooks
        hooks = load_hooks(hook_path)
        if hooks:
            console.print(f"[*] フックを読み込みました: [bold green]{hook_path}[/bold green]")
        else:
            console.print(f"[bold red][!] フックの読み込みに失敗しました。[/bold red]")
            sys.exit(1)

    try:
        if args.input:
            input_path = args.input
            # もしファイルが直接見つからず、かつ cards/ ディレクトリにあるなら補完する
            if not os.path.exists(input_path):
                alt_path = os.path.join("cards", input_path)
                if not alt_path.endswith(".json"):
                    alt_path += ".json"
                if os.path.exists(alt_path):
                    input_path = alt_path
                elif not input_path.endswith(".json") and os.path.exists(input_path + ".json"):
                    input_path += ".json"
        else:
            input_path = select_card()
            if not input_path:
                print("[!] cards/ にカードデータがありません。dump_card.py で先にダンプしてください。")
                sys.exit(1)

        console.print(f"\n[*] '{input_path}' を読み込み中...")
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        card = FeliCaCard.from_dict(data)

        console.print(f"    IDm      : [bold green]{card.idm.hex().upper()}[/bold green]")
        console.print(f"    PMm      : [green]{card.pmm.hex().upper()}[/green]")
        console.print(f"    SysCode  : [yellow]{card.primary_sys_code.hex().upper()}[/yellow]")
        if len(card.sys_codes) > 1:
            console.print(f"    全SC     : [yellow]{[sc.hex().upper() for sc in card.sys_codes]}[/yellow]")
        console.print(f"    サービス : [cyan]{[f'0x{s:04X}' for s in card.service_list]}[/cyan]")

        from .utils import get_nfc_reader_path
        reader_path = get_nfc_reader_path()

        emulator = FeliCaEmulator(card, hooks=hooks)
        emulator.run(reader_path)
        console.print("\n[!] 終了します。")
    except KeyboardInterrupt:
        # emulator.run() 内で SIGINT がハンドルされるため、ここには到達しない場合が多い
        console.print("\n[bold red][!] 中断されました。[/bold red]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red][!] エラーが発生しました: {e}[/bold red]")
        sys.exit(1)

if __name__ == "__main__":
    main()

