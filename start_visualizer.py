"""
Opens Visualizer.html through a local web server, so the Sandbox tab can load
Game_Engine.py and the greedy bot (browsers block that for pages opened as files).

    python start_visualizer.py            # sandbox (play manually / vs bot)
    python start_visualizer.py replays/seed7.json   # open a replay

Stop it with Ctrl+C.
"""
import functools
import http.server
import os
import sys
import threading
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):                        # keep the terminal quiet
            pass

    handler = functools.partial(QuietHandler, directory=ROOT)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)   # any free port
    query = f"?replay={sys.argv[1]}" if len(sys.argv) > 1 else "?sandbox=1"
    url = f"http://127.0.0.1:{server.server_port}/Visualizer.html{query}"
    print(f"Visualizer running at {url}\nPress Ctrl+C to stop.", flush=True)
    threading.Timer(0.5, webbrowser.open, args=[url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
