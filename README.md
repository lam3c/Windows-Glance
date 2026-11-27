# Windows Glance

<p align="center">
  <strong>A smarter way to experience your Windows desktop.</strong>
</p>

<p align="center">
  A modern, animated desktop companion inspired by Dynamic Island, bringing system information, live activities, media controls and intelligent interactions into one elegant interface.
</p>

---

## Overview

**Windows Glance** is a lightweight desktop application for Windows that transforms the traditional desktop experience through an interactive, animated interface.

Inspired by the Dynamic Island concept, Windows Glance provides contextual information and quick interactions without requiring users to interrupt their workflow.

From battery activity and media playback to system monitoring, notifications and voice interaction, Windows Glance brings useful desktop functionality into a compact, visually integrated experience.

The project is built with Python, PySide6 and native Windows capabilities.

## Features

### Dynamic Desktop Interface
- Animated, expandable desktop pill.
- Smooth transitions between compact and expanded states.
- Context-aware activity presentation.
- Custom-drawn interface components.
- Modern visual styling and animations.

### System Monitoring
- CPU and memory monitoring.
- System performance information.
- Battery percentage and charging status.
- Wi-Fi connection information.
- Bluetooth status.
- Background metric collection.

### Battery and Charging Activities
- Automatic charger connection detection.
- Charging and unplugging notifications.
- Animated battery percentage display.
- Temporary live activities.
- Battery-state monitoring.

### Media Integration
- Windows media session integration.
- Playback information.
- Media controls.
- Contextual media activity presentation.

### Notifications
- Notification management.
- Custom notification popups.
- Contextual notification presentation.
- Integrated notification storage.

### Voice Assistant
- Windows-native speech recognition.
- Text-to-speech functionality.
- Voice command recognition.
- Microphone activity visualization.
- Offline speech processing using Windows speech components.

### Weather Information
- Current weather conditions.
- Temperature and feels-like information.
- Daily high and low temperatures.
- Automatic location detection.
- City-based location configuration.

## Technology Stack

| Technology | Purpose |
|---|---|
| Python | Application logic |
| PySide6 | Desktop interface and event handling |
| Qt | Rendering, animations and timers |
| Windows APIs | Native system integration |
| Windows Runtime (WinRT) | Media, network and device integration |
| PowerShell | Native Windows speech services |
| Open-Meteo | Weather information |
| Multithreading / Asyncio | Background processing |

## Project Structure

```text
Windows-Glance/
│
├── WINDOWS GLANCE/
│   ├── main.py
│   │
│   ├── core/
│   │   ├── engine.py
│   │   ├── common.py
│   │   ├── system_worker.py
│   │   ├── system_monitor.py
│   │   ├── system_monitor_worker.py
│   │   ├── voice.py
│   │   ├── commands.py
│   │   ├── notification_store.py
│   │   └── weather_worker.py
│   │
│   ├── ui/
│   │   ├── painter.py
│   │   ├── charging.py
│   │   ├── assistant_view.py
│   │   ├── notification_popup.py
│   │   └── components.py
│   │
│   ├── integrations/
│   │
│   ├── assets/
│   │
│   └── requirements.txt
│
├── README.md
└── .gitignore
```

*The structure above illustrates the principal application modules; individual files and directories may vary between development versions.*

## Installation

### Requirements

- Windows 10 or Windows 11.
- Python 3.10 or newer.
- pip.
- Internet connection for weather information.
- Windows speech components for voice functionality.

### 1. Clone the repository

```powershell
git clone https://github.com/lam3c/Windows-Glance.git
```

### 2. Navigate to the application directory

```powershell
cd Windows-Glance
cd "WINDOWS GLANCE"
```

### 3. Create a virtual environment

```powershell
python -m venv .venv
```

### 4. Activate the environment

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### 5. Install dependencies

```powershell
pip install -r requirements.txt
```

### 6. Launch Windows Glance

```powershell
python main.py
```

## Development

Windows Glance is actively developed. Its architecture separates application logic, interface rendering, background workers and individual feature implementations.

This modular approach makes it easier to introduce new functionality while maintaining the existing application.

Developers can explore the source code, report issues and contribute improvements.

### Running the charging demonstration

To test the charging activity without physically connecting a charger:

```powershell
$env:GLANCE_DEMO = 1
python main.py
```

## Project Goals

Windows Glance aims to develop into a comprehensive desktop companion with:

- More intelligent contextual activities.
- Expanded desktop customization.
- Advanced system monitoring.
- Improved media and notification integration.
- A more capable voice assistant.
- Additional Windows integrations.
- Better performance and resource efficiency.

## Contributing

Contributions, suggestions and bug reports are welcome.

If you would like to contribute:

1. Fork the repository.
2. Create a feature branch.
3. Implement and test your changes.
4. Submit a pull request describing your contribution.

For significant changes, consider opening an issue first to discuss the proposed implementation.

## Project Status

**Active Development**

Windows Glance is an evolving project. Features, interface components and internal architecture may change as development progresses.

Not every planned feature is necessarily available or fully implemented in the current release.

## License

See the repository for licensing information.

---

<p align="center">
  <strong>Windows Glance</strong><br>
  Making the Windows desktop more informative, interactive and intuitive.
</p>

<p align="center">
  Developed by <a href="https://github.com/lam3c">Lam3c</a>
</p>
