# Automatic Connect

A voice-enabled AI assistant "Breeze Automatic" for D2C business analytics, built with Pipecat AI and optimized for ESP32 WebRTC connections.

## Overview

This project provides real-time voice interaction with business analytics data through WebRTC, supporting both local development and production deployment on Google Cloud Platform (GCP).

## Features

- **Voice AI Assistant**: "Breeze Automatic" with Indian English conversational interface
- **Real-time Analytics**: 24 business intelligence functions for Juspay and Breeze platforms
- **ESP32 Optimized**: Custom SDP munging for ESP32 WebRTC limitations
- **Production Ready**: Automatic environment detection and NAT traversal support

## Quick Start

### Local Development

1. **Setup Environment**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run Server**
   ```bash
   python main.py --host YOUR_LOCAL_IP --port 7860 --verbose
   ```

### Production Deployment (GCP)

1. **Get GCP External IP**
   ```bash
   gcloud compute instances describe YOUR_INSTANCE_NAME \
       --zone=YOUR_ZONE \
       --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
   ```

2. **Configure GCP Firewall**
   ```bash
   # Allow WebRTC server traffic
   gcloud compute firewall-rules create allow-webrtc-server \
       --allow tcp:7860 \
       --source-ranges 0.0.0.0/0 \
       --description "Allow WebRTC server traffic"
   
   # Allow WebRTC UDP (optional)
   gcloud compute firewall-rules create allow-webrtc-udp \
       --allow udp:10000-20000 \
       --source-ranges 0.0.0.0/0 \
       --description "Allow WebRTC UDP traffic"
   ```

3. **Deploy Using Script**
   ```bash
   chmod +x deploy_production.sh
   ./deploy_production.sh YOUR_GCP_EXTERNAL_IP
   ```

   Or manually:
   ```bash
   python main.py --host YOUR_GCP_EXTERNAL_IP --port 7860 --verbose
   ```

## Environment Variables

Create `.env` file from `.env.example`:

```bash
# Azure OpenAI
AZURE_API_KEY="your_azure_api_key"
AZURE_ENDPOINT="your_azure_endpoint"
AZURE_MODEL="your_azure_model"

# Google Cloud
GOOGLE_CREDENTIALS_JSON="{your_service_account_json}"
```

## ESP32 Client Configuration

Your ESP32 client needs these changes for production:

1. **Update Server URL**
   ```cpp
   // Change from local to production
   const char* serverURL = "http://YOUR_GCP_IP:7860/api/offer";
   ```

2. **Add STUN Servers** (Critical for production)
   ```cpp
   PeerConfiguration peer_connection_config = {
       .ice_servers = {
           {"stun:stun.l.google.com:19302"},
           {"stun:stun1.l.google.com:19302"}
       },
       // ... other config
   };
   ```

3. **Increase Timeouts**
   ```cpp
   #define HTTP_TIMEOUT_MS 30000  // Increase for internet connections
   ```

## Architecture

### Server Components
- **FastAPI**: HTTP server for WebRTC signaling
- **Pipecat AI**: Voice pipeline framework
- **Azure OpenAI**: LLM service
- **Google Cloud**: STT/TTS services
- **SmallWebRTC**: ESP32-optimized WebRTC transport

### Key Features
- **Production-Aware SDP Munging**: Different ICE filtering for local vs production
- **STUN Server Support**: Automatic NAT traversal configuration
- **Comprehensive Logging**: Debug-friendly WebRTC connection tracking
- **Environment Detection**: Automatic production vs local detection

## Troubleshooting

### Connection Issues

1. **ESP32 Can't Connect to Production**
   - Verify GCP firewall rules allow port 7860
   - Check ESP32 has STUN servers configured
   - Ensure server is using external IP, not localhost

2. **WebRTC Negotiation Fails**
   - Check SDP munging logs for ICE candidate filtering
   - Verify ESP32 supports server-reflexive candidates
   - Enable verbose logging: `--verbose`

3. **Audio Not Working**
   - Check UDP firewall rules (ports 10000-20000)
   - Verify Google Cloud credentials for TTS/STT
   - Monitor VAD analyzer settings

### Debug Commands

```bash
# Test server accessibility
curl http://YOUR_GCP_IP:7860/api/offer

# Check firewall
nmap -p 7860 YOUR_GCP_IP

# Monitor logs
python main.py --host YOUR_GCP_IP --port 7860 --verbose
```

## Analytics Functions

The assistant provides 24 analytics functions:

### Juspay Payment Analytics
- Success rates (overall and by payment method)
- Transaction volumes and failure analysis
- GMV and average ticket sizes
- Daily and weekly variants

### Breeze Business Analytics
- Sales and order breakdowns
- Conversion rate tracking
- Payment success rates
- Ad spend and ROAS metrics

### Utility Functions
- Current time with timezone support

## Development

### File Structure
```
├── main.py              # Main server application
├── tools.py             # Analytics function definitions
├── dummy_data.py        # Mock analytics data
├── requirements.txt     # Python dependencies
├── deploy_production.sh # Production deployment script
├── .env.example        # Environment template
└── README.md           # This file
```

### Key Functions
- `smallwebrtc_sdp_munging()`: ESP32-specific SDP processing
- `is_production_environment()`: Environment detection
- `run_server()`: Main WebRTC server with ICE configuration

## License

BSD 2-Clause License - Copyright (c) 2024–2025, Daily
