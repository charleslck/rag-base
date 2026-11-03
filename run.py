#!/usr/bin/env python3
"""Entry point: python run.py [--host 0.0.0.0] [--port 7100]"""
import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(__import__("os").environ.get("PORT", "7100")))
    args = parser.parse_args()
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
