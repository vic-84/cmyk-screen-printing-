"""Arranca la versión web: python -m src.web [--host 0.0.0.0] [--port 8000]"""

import argparse

import uvicorn

parser = argparse.ArgumentParser(description="Separador de color para serigrafía (web)")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8000)
args = parser.parse_args()
uvicorn.run("src.web.app:app", host=args.host, port=args.port)
