#!/usr/bin/env bash
set -e

echo "=== Setup of the environment ==="
VENV_DIR="venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtualenv in $VENV_DIR..."
    python3 -m venv $VENV_DIR
else
    echo "Virtualenv already existing $VENV_DIR"
fi

OS_TYPE=$(uname)
if [ "$OS_TYPE" == "Darwin" ] || [ "$OS_TYPE" == "Linux" ]; then
    echo "Activating virtualenv..."
    source $VENV_DIR/bin/activate
else
    echo "SO dont supported automatically: $OS_TYPE"
    echo "You need to manual activate the environment in: $VENV_DIR"
    exit 1
fi

if [ -f "requirements.txt" ]; then
    echo "Installing dependencies from requirements.txt..."
    pip3 install -r requirements.txt
fi

echo "=== Environment ready ==="
