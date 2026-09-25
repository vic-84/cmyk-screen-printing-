#!/bin/bash
# Instala las dependencias en las sesiones de Claude Code en la web para poder
# correr las pruebas (python -m unittest tests.test_core) y el linter (ruff).
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# En el contenedor no hay pantalla: OpenCV sin GUI y Qt en modo offscreen.
pip install --quiet --disable-pip-version-check \
  numpy opencv-python-headless PyQt5 pillow PyMuPDF psd-tools ruff \
  fastapi uvicorn python-multipart httpx

echo 'export QT_QPA_PLATFORM=offscreen' >> "$CLAUDE_ENV_FILE"
