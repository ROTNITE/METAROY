"""Запуск: python main.py --port 8000 --width 30 --height 30 --seed 42"""
import argparse
import webbrowser
from Starter import Starter
from World import Config
from World.server import run_server


def main():
    parser = argparse.ArgumentParser(description='Evolution World — 2D MVP')
    parser.add_argument('--width', type=int, default=30)
    parser.add_argument('--height', type=int, default=30)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    app = Starter(Config(width=args.width, height=args.height, seed=args.seed))
    url = f'http://127.0.0.1:{args.port}'
    print(f'Evolution World запущен: {url} — Ctrl+C для остановки')
    if not args.no_browser:
        import threading
        threading.Timer(.7, lambda: webbrowser.open(url)).start()
    run_server(app, port=args.port)


if __name__ == '__main__':
    main()
