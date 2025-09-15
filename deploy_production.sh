#!/bin/bash

# Production Deployment Script for GCP
# Usage: ./deploy_production.sh YOUR_GCP_EXTERNAL_IP

set -e

if [ $# -eq 0 ]; then
    echo "Usage: $0 <GCP_EXTERNAL_IP>"
    echo "Example: $0 34.123.45.67"
    exit 1
fi

GCP_IP=$1
PORT=7860

echo "🚀 Deploying Automatic Connect to production..."
echo "📍 GCP External IP: $GCP_IP"
echo "🔌 Port: $PORT"

# Validate IP format
if [[ ! $GCP_IP =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    echo "❌ Invalid IP format: $GCP_IP"
    exit 1
fi

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "❌ .env file not found. Please create it from .env.example"
    echo "Required variables:"
    echo "  - AZURE_API_KEY"
    echo "  - AZURE_ENDPOINT" 
    echo "  - AZURE_MODEL"
    echo "  - GOOGLE_CREDENTIALS_JSON"
    exit 1
fi

# Check if required environment variables are set
echo "🔍 Checking environment variables..."
source .env
if [ -z "$AZURE_API_KEY" ] || [ -z "$AZURE_ENDPOINT" ] || [ -z "$AZURE_MODEL" ]; then
    echo "❌ Missing Azure credentials in .env file"
    exit 1
fi

if [ -z "$GOOGLE_CREDENTIALS_JSON" ] || [ "$GOOGLE_CREDENTIALS_JSON" = "{}" ]; then
    echo "❌ Missing Google credentials in .env file"
    exit 1
fi

echo "✅ Environment variables validated"

# Install dependencies if needed
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

echo "📦 Installing dependencies..."
source venv/bin/activate
pip install -r requirements.txt

# GCP Firewall setup instructions
echo ""
echo "🔥 GCP Firewall Setup Required:"
echo "Run these commands in Google Cloud Shell:"
echo ""
echo "# Allow HTTP traffic on port $PORT"
echo "gcloud compute firewall-rules create allow-webrtc-server \\"
echo "    --allow tcp:$PORT \\"
echo "    --source-ranges 0.0.0.0/0 \\"
echo "    --description 'Allow WebRTC server traffic'"
echo ""
echo "# Allow UDP for WebRTC media (optional)"
echo "gcloud compute firewall-rules create allow-webrtc-udp \\"
echo "    --allow udp:10000-20000 \\"
echo "    --source-ranges 0.0.0.0/0 \\"
echo "    --description 'Allow WebRTC UDP traffic'"
echo ""

# Test connectivity
echo "🔍 Testing server connectivity..."
echo "Server will start on http://$GCP_IP:$PORT"
echo ""

# Start the server
echo "🚀 Starting production server..."
echo "Press Ctrl+C to stop"
echo ""

python main.py --host $GCP_IP --port $PORT --verbose