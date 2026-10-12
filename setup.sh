#!/bin/bash

# AutoRename-PDF Setup Script for macOS and Linux
# This script automates the installation and configuration of AutoRename-PDF

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print functions
print_header() {
    echo -e "\n${BLUE}=====================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}=====================================${NC}\n"
}

print_status() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

OS=$(detect_os)

print_header "AutoRename-PDF Setup for $(uname -s)"

# Step 1: Check Python version
print_status "Checking Python installation..."
if ! command_exists python3; then
    print_error "Python 3 is not installed. Please install Python 3.11 or later."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d'.' -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d'.' -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
    print_error "Python 3.11 or later is required. Found: Python $PYTHON_VERSION"
    exit 1
fi

print_status "Python $PYTHON_VERSION detected"

# Step 2: Create virtual environment
print_status "Creating virtual environment..."
if [ -d "venv" ]; then
    print_warning "Virtual environment already exists. Skipping creation."
else
    python3 -m venv venv
    print_status "Virtual environment created"
fi

# Step 3: Activate virtual environment and install dependencies
print_status "Installing Python dependencies..."
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
print_status "Dependencies installed"

# Step 4: Create config.yaml from example
print_status "Setting up configuration..."
if [ -f "config.yaml" ]; then
    print_warning "config.yaml already exists. Skipping."
else
    cp config.yaml.example config.yaml
    print_status "config.yaml created from template"
    print_warning "Please edit config.yaml and add your AI provider API key"
fi

# Step 5: Create harmonized company names file
print_status "Setting up company names harmonization..."
if [ -f "harmonized-company-names.yaml" ]; then
    print_warning "harmonized-company-names.yaml already exists. Skipping."
else
    if [ -f "harmonized-company-names.yaml.example" ]; then
        cp harmonized-company-names.yaml.example harmonized-company-names.yaml
        print_status "harmonized-company-names.yaml created from template"
    else
        print_warning "harmonized-company-names.yaml.example not found. Skipping."
    fi
fi

# Step 6: Test installation
print_status "Testing installation..."
if python autorename-pdf.py --version >/dev/null 2>&1; then
    VERSION=$(python autorename-pdf.py --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
    print_status "AutoRename-PDF v$VERSION is ready!"
else
    print_error "Installation test failed"
    exit 1
fi

# Step 7: Optional PaddleOCR installation
echo ""
read -p "$(echo -e "${YELLOW}Do you want to install PaddleOCR for offline OCR? (y/N):${NC} ")" -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    print_status "Installing PaddleOCR..."

    # Determine PaddleOCR venv path
    if [ "$OS" == "macos" ]; then
        PADDLEOCR_VENV="$HOME/Library/Application Support/autorename-pdf/paddleocr-venv"
    else
        PADDLEOCR_VENV="$HOME/.local/share/autorename-pdf/paddleocr-venv"
    fi

    # Create PaddleOCR venv
    if [ -d "$PADDLEOCR_VENV" ]; then
        print_warning "PaddleOCR venv already exists at $PADDLEOCR_VENV"
    else
        mkdir -p "$(dirname "$PADDLEOCR_VENV")"
        python3 -m venv "$PADDLEOCR_VENV"
        source "$PADDLEOCR_VENV/bin/activate"
        pip install --upgrade pip -q
        pip install paddlepaddle paddleocr -q
        print_status "PaddleOCR installed to $PADDLEOCR_VENV"
    fi

    # Update config.yaml with PaddleOCR venv path
    if grep -q "venv_path: \"\"" config.yaml 2>/dev/null; then
        if [ "$OS" == "macos" ]; then
            sed -i '' "s|venv_path: \"\"|venv_path: \"$PADDLEOCR_VENV\"|" config.yaml
        else
            sed -i "s|venv_path: \"\"|venv_path: \"$PADDLEOCR_VENV\"|" config.yaml
        fi
        print_status "config.yaml updated with PaddleOCR path"
    fi

    # Reactivate main venv
    deactivate 2>/dev/null || true
    source venv/bin/activate
else
    print_warning "Skipping PaddleOCR installation"
    print_warning "You can still use cloud AI providers with vision mode"
fi

# Step 8: Create shell alias (optional)
echo ""
read -p "$(echo -e "${YELLOW}Create shell alias 'autorename' for easy access? (y/N):${NC} ")" -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    ALIAS_CMD="alias autorename='source \"$SCRIPT_DIR/venv/bin/activate\" && python \"$SCRIPT_DIR/autorename-pdf.py\"'"

    # Detect shell config file
    if [ -n "$ZSH_VERSION" ]; then
        SHELL_CONFIG="$HOME/.zshrc"
    elif [ -n "$BASH_VERSION" ]; then
        SHELL_CONFIG="$HOME/.bashrc"
    else
        SHELL_CONFIG="$HOME/.profile"
    fi

    if grep -q "alias autorename=" "$SHELL_CONFIG" 2>/dev/null; then
        print_warning "Alias already exists in $SHELL_CONFIG"
    else
        echo "" >> "$SHELL_CONFIG"
        echo "# AutoRename-PDF alias" >> "$SHELL_CONFIG"
        echo "$ALIAS_CMD" >> "$SHELL_CONFIG"
        print_status "Alias added to $SHELL_CONFIG"
        print_warning "Run 'source $SHELL_CONFIG' or restart your terminal to use the alias"
    fi
    if ! grep -q "alias autorename-pdf=" "$SHELL_CONFIG" 2>/dev/null; then
        echo "alias autorename-pdf='autorename' # compatibility alias" >> "$SHELL_CONFIG"
    fi
fi

# Final instructions
print_header "Setup Complete!"
echo -e "${GREEN}AutoRename-PDF is installed and ready to use.${NC}\n"
echo "Next steps:"
echo "  1. Edit config.yaml and add your AI provider API key"
echo "  2. Activate the virtual environment: ${BLUE}source venv/bin/activate${NC}"
echo "  3. Test with a PDF: ${BLUE}autorename --dry-run your-file.pdf${NC}"
echo ""
echo "Documentation: https://github.com/ptmrio/autorename-pdf"
echo "Issues: https://github.com/ptmrio/autorename-pdf/issues"
echo ""

deactivate 2>/dev/null || true
