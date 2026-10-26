#!/bin/bash

set -euo pipefail

if [[ -r /etc/os-release ]]; then
    . /etc/os-release
    if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "22.04" ]]; then
        echo "This installer targets Ubuntu 22.04; detected ${PRETTY_NAME:-unknown OS}." >&2
        exit 1
    fi
fi

echo "🚀 Starting setup..."
sudo apt-get update
sudo apt-get install -y git wget gnupg curl gcc g++ cmake autoconf libtool pkg-config libmnl-dev libyaml-dev make libsctp-dev lksctp-tools iproute2 python3 python3-pip python3-venv

# Use the checkout that contains this installer so setup.sh installs the
# current branch. Clone only when this script is run outside a testbed checkout.
TESTBED_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$TESTBED_DIR/requirements.txt" || ! -d "$TESTBED_DIR/free5gc" ]]; then
    TESTBED_DIR="$HOME/testbed"
    if [[ ! -d "$TESTBED_DIR/.git" ]]; then
        echo "📥 Cloning testbed repository..."
        git clone https://github.com/HenokDanielbfg/testbed.git "$TESTBED_DIR"
    fi
fi
cd "$TESTBED_DIR"

# Install Go
echo "📦 Installing Go..."
wget https://dl.google.com/go/go1.21.8.linux-amd64.tar.gz
sudo tar -C /usr/local -zxvf go1.21.8.linux-amd64.tar.gz
mkdir -p ~/go/{bin,pkg,src}

# Set up Go environment variables
echo "🔧 Setting up Go environment..."
echo 'export GOPATH=$HOME/go' >> ~/.bashrc
echo 'export GOROOT=/usr/local/go' >> ~/.bashrc
echo 'export PATH=$PATH:$GOPATH/bin:$GOROOT/bin' >> ~/.bashrc
echo 'export GO111MODULE=auto' >> ~/.bashrc
export GOPATH="$HOME/go"
export GOROOT=/usr/local/go
export PATH="$PATH:$GOPATH/bin:$GOROOT/bin"

# Install dependencies
echo "📦 Installing dependencies..."
sudo apt-get -y install zlib1g-dev libcurl4-openssl-dev

# Install MongoDB
echo "📦 Installing MongoDB..."
curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo apt update
sudo apt install -y mongodb-org

# Start MongoDB
echo "🚀 Starting MongoDB..."
sudo systemctl start mongod
sudo systemctl enable mongod

# Install Node.js & Yarn
echo "📦 Installing Node.js & Yarn..."
sudo curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt update
sudo apt install -y nodejs
sudo corepack enable  # Enable Yarn

# Install the Python RAG/LLM dependencies in an isolated environment.
cd "$TESTBED_DIR"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
deactivate

# Install Prometheus C++ library
echo "📦 Installing Prometheus C++ library..."
cd "$TESTBED_DIR"
git clone https://github.com/jupp0r/prometheus-cpp.git
cd prometheus-cpp
git submodule init
git submodule update
mkdir _build
cd _build
sudo cmake .. -DBUILD_SHARED_LIBS=ON
sudo cmake --build . --parallel 4
sudo cmake --install .

# Install Prometheus
echo "📦 Installing Prometheus..."
cd "$TESTBED_DIR"
sudo wget https://github.com/prometheus/prometheus/releases/download/v2.41.0/prometheus-2.41.0.linux-amd64.tar.gz
sudo tar -xvf prometheus-2.41.0.linux-amd64.tar.gz

# Install UERANSIM
echo "🛠️ Installing UERANSIM..."
cd "$TESTBED_DIR/UERANSIM"
# git checkout e4c492d
sudo make  # Make sure Prometheus C++ is installed first

# Install Free5GC
echo "🛠️ Installing Free5GC..."
cd "$TESTBED_DIR"
cd free5gc
# rmdir gtp5g
sudo make
sudo make webconsole

# Install GTP5G
echo "🛠️ Installing GTP5G..."
sudo git clone -b v0.8.7 https://github.com/free5gc/gtp5g.git
cd gtp5g
sudo make
sudo make install

echo "✅ Setup complete!"

