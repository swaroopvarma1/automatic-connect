#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import argparse
import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI
from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.google.stt import GoogleSTTService
from pipecat.services.google.tts import GoogleTTSService
from pipecat.services.azure.llm import AzureLLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.network.small_webrtc import SmallWebRTCTransport
from pipecat.transports.network.webrtc_connection import SmallWebRTCConnection
from pipecat.transcriptions.language import Language
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.transports.base_transport import TransportParams
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame, LLMTextFrame
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.adapters.schemas.tools_schema import ToolsSchema


from tools import tool_functions, tools

# Load environment variables from .env file
load_dotenv(override=True)

# Get credentials from environment variables
AZURE_API_KEY = os.getenv("AZURE_API_KEY")
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT")
AZURE_MODEL = os.getenv("AZURE_MODEL")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS_JSON", "{}")


#
# SDP Munging for ESP32
#

def smallwebrtc_sdp_cleanup_ice_candidates(text: str, pattern: str) -> str:
    """Original aggressive ICE filtering for local networks"""
    result = []
    lines = text.splitlines()
    for line in lines:
        if re.search("a=candidate", line):
            if re.search(pattern, line) and not re.search("raddr", line):
                result.append(line)
        else:
            result.append(line)
    return "\r\n".join(result)


def smallwebrtc_sdp_cleanup_ice_candidates_production(text: str, pattern: str) -> str:
    """Less aggressive ICE filtering for production environments"""
    result = []
    lines = text.splitlines()
    for line in lines:
        if re.search("a=candidate", line):
            # Keep ALL candidates that match the external IP pattern
            # This includes both host and srflx candidates with external IP
            if re.search(pattern, line):
                result.append(line)
            # Also keep server reflexive candidates (for NAT traversal)
            elif re.search("typ srflx", line):
                result.append(line)
            # Keep host candidates only if they're not private IPs
            elif re.search("typ host", line):
                # Only keep host candidates that don't have private IPs
                if not any(private_ip in line for private_ip in ["192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31."]):
                    result.append(line)
        else:
            result.append(line)
    return "\r\n".join(result)


def is_production_environment(host: str) -> bool:
    """Detect if running in production based on host IP"""
    # GCP external IPs are typically not in private ranges
    private_ranges = [
        "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
        "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
        "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31."
    ]
    return not any(host.startswith(range_) for range_ in private_ranges)


def smallwebrtc_sdp_cleanup_fingerprints(text: str) -> str:
    result = []
    lines = text.splitlines()
    for line in lines:
        if not re.search("sha-384", line) and not re.search("sha-512", line):
            result.append(line)
    return "\r\n".join(result)


def smallwebrtc_sdp_munging(sdp: str, host: str) -> str:
    """Apply ESP32-specific SDP modifications with production awareness"""
    logger.info(f"Starting SDP munging for host: {host}")
    logger.debug(f"Original SDP length: {len(sdp)} characters")
    
    # Always clean fingerprints (ESP32 limitation)
    sdp = smallwebrtc_sdp_cleanup_fingerprints(sdp)
    logger.debug(f"After fingerprint cleanup: {len(sdp)} characters")
    
    # Temporarily use local filtering to debug ICE candidates
    logger.info("Using local network ICE candidate filtering (debug mode)")
    sdp = smallwebrtc_sdp_cleanup_ice_candidates(sdp, host)
    
    # Log what candidates we're working with before filtering
    original_candidates = [line for line in sdp.split('\n') if 'a=candidate' in line]
    logger.info(f"Original candidates before filtering: {len(original_candidates)}")
    for candidate in original_candidates:
        logger.info(f"Original: {candidate}")
    
    logger.debug(f"After ICE cleanup: {len(sdp)} characters")
    
    # Log ICE candidates for debugging
    ice_candidates = [line for line in sdp.split('\n') if 'a=candidate' in line]
    logger.info(f"Remaining ICE candidates: {len(ice_candidates)}")
    for candidate in ice_candidates:
        logger.debug(f"ICE: {candidate}")
    
    return sdp

#
# Bot Pipeline
#

SYSTEM_PROMPT = """
    SYSTEM ROLE
    You are “Breeze Automatic”, a friendly voice assistant created by Breeze (owned by Juspay), helping D2C business owners with analytics and insights.

    TONE & STYLE
    Speak conversationally in Indian English, as though chatting live. Begin every session with:
    “Hey, whatsup? How can I help you today?”
    Your single most important rule is brevity. All responses must be under 20 words. Be direct and concise.
    ABSOLUTELY NO EMOJIS, SPECIAL CHARACTERS, OR MARKDOWN FORMATTING. Never use markdown headings (e.g. **text**). All responses must be in a single paragraph, never use lists or bullet points.

    VOICE & PACING
    Use varied sentence lengths and natural pauses. Include rhetorical questions (“Need a quick sales recap?”) and affirmations (“Sure thing.”). Use tone shifts to highlight changes.

    STRUCTURE & DIRECT RESPONSE PROTOCOL
    Every response should include:
    1. Acknowledgement/opening
    2. Core insight (LEAD WITH DIRECT ANSWER for specific questions)
    3. Closing suggestion or question
    For specific data questions, always start with the exact answer:
    - "Which/what" → State the specific item/name first
    - "How much/many" → State the number/amount first
    - "When" → State the time/date first
    - "Who" → State the person/entity first
    Never begin with "Based on analysis..." or methodology. Give the answer, then brief context, then engagement.

    NUMBERS & ROUNDING
    Always convert numbers to the Indian numbering system using hundred, thousand, lakh, and crore.
    For large numbers, round to a nearby, natural-sounding significant figure to keep it easy on the ear. For example, convert "753,644.76" into "around 7 lakh 54 thousand rupees". Use qualifiers like “around”, “approximately”, or “roughly” to signal rounding.
    Avoid using paise or decimals. Say only the rounded rupee value. For small, clear numbers like “₹899” or “124 orders”, you may speak them exactly. Choose what sounds most natural for speech — the goal is smooth, human-like delivery.

    CRORE CONVERSION RULES
    When converting large numbers:
        Use Indian-style grouping (e.g. 34,42,15,267) to guide the breakdown into crore, lakh, thousand.
        Convert to crore by dividing the number by 1,00,00,000.
        For 9-digit numbers, place the decimal after the first two digits to get approximate crores (e.g. 344,215,267 becomes ~34.42 crores).
        Round naturally to a significant figure that sounds smooth when spoken. For example:
            296,636,734 → “around 29 crore 66 lakh rupees”
            344,215,267 → “roughly 34 crore 42 lakh rupees”
        Avoid common errors like dropping a digit and saying “2.97 crores” instead of “29.7 crores”.
        Always double-check digit length to avoid underestimation.
        If the amount is less than 1 crore, express in lakhs or thousands as needed.

    ACRONYMS
    Expand on first mention (e.g. Cash On Delivery (COD)).

    TOOLS & SCOPE
        Use-Case-Driven:
            - Invoke external tools when they directly address the user's request.
        Context Management:
            Historical Awareness
            - Before calling a tool, scan the recent conversation for valid, existing data and reuse it if still applicable.
        Response Protocol
            1. Direct Answers Only
                Provide exactly what was asked—no extra analysis or commentary.
            2. Optional Follow-Up
                After your direct answer, invite the user to dive deeper (e.g., “Want to see performance metrics for this?”).
        Time & Date Handling
            1. Interactive Timeframes
                - If the user does not specify a period for a timeframe-dependent tool, ask:
                “Which timeframe would you like to use?”
                - Once set, persist that timeframe for all subsequent queries until the user explicitly requests a change.
            2. Explicit Only
                Never assume a default period—always confirm the user's intended range.
            3. Resolve “Today” Explicitly
                For any tool call requiring a relative date or time range, first invoke `get_current_time` and use that exact timestamp to disambiguate relative terms like “today,” “this week,” or “last month.”
        Error & Clarification
            1. Automated Retry
                If a tool call fails for a recoverable reason (e.g., minor formatting issues), retry internally up to 3 TIMES - do not involve the user.  
            2. Smart Clarify
                If a request is ambiguous, ask a focused follow-up rather than guessing.
            3. Graceful Degradation
                For unrecoverable errors, apologize briefly (“Sorry, I encountered an issue.”) and ask how to proceed.
        Tone & Personalization
            - Keep replies warm, concise, and user-focused.
            - Celebrate successes, gently propose next steps on dips.
            - Never reveal internal tool names, processes, or implementation details.

    TIMEZONE
    Assume Indian Standard Time (IST) unless user specifies otherwise.

    IDENTITY
    If asked about identity, say:
    “I'm Breeze Automatic, your AI sidekick. Think of me as your extra brain for your D2C business. Whether it's digging through data, summarizing reports, or prepping for your next big move — I'm here to help you work smarter.”
    Never mention or describe your internal architecture, training methods, underlying model, or who built you. Always redirect the conversation to your purpose: assisting with business insights.

"""


class LLMTextCatcher(FrameProcessor):
    """Catches LLM-generated assistant responses, batches them into groups
    of words while preserving whitespace, and sends them to the client over WebRTC."""

    def __init__(self, connection: SmallWebRTCConnection, name: str = "LLMTextCatcher"):
        super().__init__(name=name)
        self.connection = connection
        self._text_buffer = ""

    def _send_text(self, text: str):
        if not text:
            return
        if self.connection and self.connection.is_connected():
            logger.info(f"LLMTextCatcher: Sending text batch: '{text}'")
            self.connection.send_app_message({
                "type": "bot-transcript",
                "text": text
            })
        else:
            logger.warning("LLMTextCatcher: WebRTC connection not active, can't send text.")

    def _flush_buffer(self, force_flush=False):
        if not self._text_buffer:
            return

        # Continuously process the buffer as long as there are enough words
        while True:
            # Find all word-like sequences
            words = list(re.finditer(r'\S+', self._text_buffer))
            
            if len(words) < 10:
                # Not enough words to form a batch, break the loop
                break

            # Get the end position of the 10th word
            end_pos = words[9].end()
            text_to_send = self._text_buffer[:end_pos]
            self._text_buffer = self._text_buffer[end_pos:]
            
            self._send_text(text_to_send)

        # If force_flush is enabled, send any remaining text in the buffer
        if force_flush and self._text_buffer:
            self._send_text(self._text_buffer)
            self._text_buffer = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            # Append raw text to buffer, preserving all whitespace
            self._text_buffer += frame.text
            
            # An empty text frame signals the end of a response.
            if not frame.text:
                self._flush_buffer(force_flush=True)
            else:
                self._flush_buffer(force_flush=False)
        else:
            # A non-LLM frame also signals we should flush everything.
            self._flush_buffer(force_flush=True)

        # Push the original frame downstream for other processors like TTS
        await self.push_frame(frame, direction)

async def run_example(
    connection: SmallWebRTCConnection,
    transport: BaseTransport,
    _: argparse.Namespace,
    handle_sigint: bool,
):
    logger.info(f"Starting bot")

    stt = GoogleSTTService(
        params=GoogleSTTService.InputParams(languages=[Language.EN_US, Language.EN_IN]),
        credentials=GOOGLE_CREDENTIALS
    )

    tts = GoogleTTSService(
        params=GoogleTTSService.InputParams(language=Language.EN_IN),
        voice_id="en-IN-Chirp3-HD-Despina",
        credentials=GOOGLE_CREDENTIALS
    )

    llm = AzureLLMService(
        api_key=AZURE_API_KEY,
        endpoint=AZURE_ENDPOINT,
        model=AZURE_MODEL
    )

    # Register tool functions
    for name, function in tool_functions.items():
        llm.register_function(name, function)

    transcript = TranscriptProcessor()

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
    ]

    context = OpenAILLMContext(messages, tools)
    context_aggregator = llm.create_context_aggregator(context)

    llm_text_catcher = LLMTextCatcher(connection)

    pipeline = Pipeline(
        [
            transport.input(),  # Transport user input
            stt,
            transcript.user(),
            context_aggregator.user(),  # User responses
            llm,  # LLM
            llm_text_catcher,
            tts,  # TTS
            transport.output(),  # Transport bot output
            context_aggregator.assistant(),  # Assistant spoken responses
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Client connected")
        # Kick off the conversation.
        messages.append({"role": "system", "content": "Please introduce yourself to the user."})
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"Client disconnected")
        await task.cancel()


    @transcript.event_handler("on_transcript_update")
    async def _(processor, frame):
        for msg in frame.messages:
            # only send if we have an active, connected WebRTC client
            if connection and connection.is_connected():
                # send_app_message is synchronous
                connection.send_app_message({
                    "type": "user-transcript",
                    "text": msg.content,
                })
            else:
                logger.warning("No active client_conn; skipping transcript")

    runner = PipelineRunner(handle_sigint=handle_sigint)

    await runner.run(task)


#
# WebRTC Server
#

def run_server(host: str, port: int):
    logger.info("Starting ESP32 WebRTC server...")
    logger.info(f"Production environment: {is_production_environment(host)}")

    app = FastAPI()
    pcs_map: Dict[str, SmallWebRTCConnection] = {}
    
    # Configure ICE servers for NAT traversal
    ice_servers = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"}
    ]
    logger.info(f"Configured ICE servers: {ice_servers}")

    @app.post("/api/offer")
    async def offer(request: dict, background_tasks: BackgroundTasks):
        pc_id = request.get("pc_id")
        logger.info(f"Received offer request for pc_id: {pc_id}")

        if pc_id and pc_id in pcs_map:
            pipecat_connection = pcs_map[pc_id]
            logger.info(f"Reusing existing connection for pc_id: {pc_id}")
            await pipecat_connection.renegotiate(
                sdp=request["sdp"],
                type=request["type"],
                restart_pc=request.get("restart_pc", False),
            )
        else:
            logger.info("Creating new WebRTC connection with ICE servers")
            # Create connection with ICE servers for NAT traversal
            pipecat_connection = SmallWebRTCConnection(ice_servers=ice_servers)
            await pipecat_connection.initialize(
                sdp=request["sdp"],
                type=request["type"]
            )

            @pipecat_connection.event_handler("closed")
            async def handle_disconnected(webrtc_connection: SmallWebRTCConnection):
                logger.info(f"Discarding peer connection for pc_id: {webrtc_connection.pc_id}")
                pcs_map.pop(webrtc_connection.pc_id, None)

            params = TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                vad_analyzer=SileroVADAnalyzer(
                    sample_rate=16000,
                    params=VADParams(
                        confidence=0.85,
                        start_secs=0.30,
                        stop_secs=1.00,
                        min_volume=0.75,
                    )
                ),
            )
            transport = SmallWebRTCTransport(params=params, webrtc_connection=pipecat_connection)

            background_tasks.add_task(run_example, pipecat_connection, transport, None, False)

        answer = pipecat_connection.get_answer()
        answer["sdp"] = smallwebrtc_sdp_munging(answer["sdp"], host)

        pcs_map[answer["pc_id"]] = pipecat_connection

        return answer

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield  # Run app
        coros = [pc.disconnect() for pc in pcs_map.values()]
        await asyncio.gather(*coros)
        pcs_map.clear()

    # Bind to all interfaces for GCP deployment
    logger.info(f"Starting server on 0.0.0.0:{port} (external host: {host})")
    uvicorn.run(app, host="0.0.0.0", port=port)


#
# Main
#

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipecat ESP32 Connector")
    parser.add_argument("--host", required=True, help="Host IP address for WebRTC")
    parser.add_argument(
        "--port", type=int, default=7860, help="Port for HTTP server (default: 7860)"
    )
    parser.add_argument("--verbose", "-v", action="count", default=0)
    args = parser.parse_args()

    if not all([AZURE_API_KEY, AZURE_ENDPOINT, AZURE_MODEL, GOOGLE_CREDENTIALS]):
        logger.error("Azure credentials (AZURE_API_KEY, AZURE_ENDPOINT, AZURE_MODEL) not found in environment variables.")
        sys.exit(1)

    if args.host == "localhost" or args.host == "127.0.0.1":
        logger.error("For ESP32, you must specify a public or LAN IP address for the --host argument.")
        sys.exit(1)

    # Add validation for production deployment
    if is_production_environment(args.host):
        logger.info(f"Production deployment detected with external IP: {args.host}")
        logger.info("Ensure GCP firewall allows traffic on port 7860")
    else:
        logger.info(f"Local/LAN deployment detected with IP: {args.host}")
        
    # Validate host format
    if not args.host.replace('.', '').replace(':', '').isalnum():
        logger.warning(f"Host {args.host} format may be invalid. Ensure it's accessible from ESP32.")

    logger.remove(0)
    logger.add(sys.stderr, level="TRACE" if args.verbose else "DEBUG")

    run_server(args.host, args.port)