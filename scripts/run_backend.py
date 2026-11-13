"""Start the local backend, loading a Git-ignored project key if available."""
import os
from pathlib import Path
import sys


def main():
    env = os.environ.copy()
    path = Path(__file__).resolve().parent.parent / '.env'
    if not env.get('OPENAI_API_KEY') and path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith('OPENAI_API_KEY='):
                value = line.split('=', 1)[1].strip().strip('"\'')
                if value:
                    env['OPENAI_API_KEY'] = value
                break
    os.execve(sys.executable, [sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000'], env)


if __name__ == '__main__':
    main()
