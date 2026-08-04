# Universal Hardware Runtime Platform

> **One Runtime. One API. Unlimited Hardware.**

An open-source platform that lets AI Agents and humans control ANY hardware board (ESP32, Arduino, STM32, Raspberry Pi, etc.) through a single unified API — exposed via MCP (Model Context Protocol) and a real-time Web Dashboard.

## Architecture

```
Web UI (Next.js) ─┐
AI Agent (MCP)  ──┼──► FastAPI Gateway ──► Runtime Core ──► Adapters ──► Hardware
CLI (Python)    ─┘                         Event Bus
                                           Plugin System
                                           Task Scheduler
                                           Device Registry
```

## Quick Start

```bash
# Clone and install
git clone https://github.com/your-org/universal-hardware-runtime.git
cd universal-hardware-runtime
python scripts/install.py

# Start the runtime
uhr-server

# Open dashboard at http://localhost:8765
```

## Supported Hardware

| Board | Adapter | Status |
|-------|---------|--------|
| ESP32 / ESP32-S2 / ESP32-S3 / ESP32-C3 | esptool | Planned |
| Arduino Uno / Mega / Leonardo | arduino-cli | Planned |
| Raspberry Pi Pico (RP2040/RP2350) | picotool | Planned |
| STM32 | OpenOCD | Planned |
| AVR (ATmega) | avrdude | Planned |

## Development Phases

- **Phase 0**: Environment & Research (current)
- **Phase 1**: Runtime Core
- **Phase 2**: MCP & REST API
- **Phase 3**: Transport & Device Discovery
- **Phase 4**: Hardware Adapters
- **Phase 5**: Web Dashboard
- **Phase 6**: Advanced Features
- **Phase 7**: Testing & QA
- **Phase 8**: Deployment & Release

## License

MIT — See [LICENSE](LICENSE)
