# Windows Glance

**A modern Windows desktop monitoring and system intelligence application.**

Windows Glance is a Python-powered desktop application built with PySide6, designed to bring essential system information, monitoring tools, weather updates, and notifications into one unified interface.

The project aims to simplify desktop system awareness through a clean, intuitive dashboard and useful background services.

## Features

- **System Dashboard** – A centralized interface for viewing essential system information.
- **System Monitoring** – Monitor important system metrics and activity.
- **Weather Integration** – Access weather information directly from the desktop.
- **Notification Center** – Centralized notification management with persistent storage and notification history.
- **Background Processing** – Background workers for handling operations without blocking the user interface.
- **Custom UI Rendering** – A custom-painted interface designed for a consistent desktop experience.
- **Modern Desktop Experience** – A clean, organized interface built specifically for Windows.

## Technology Stack

| Technology | Purpose |
|---|---|
| Python | Core application development |
| PySide6 | Desktop GUI framework |
| Qt | Interface rendering and application architecture |
| JSON | Data persistence |
| Windows | Target operating system |

## Project Structure

The application follows a modular architecture, separating interface components, background services, and core application functionality.

```text
WINDOWS GLANCE/
│
├── core/
│   ├── notification_store.py
│   └── weather_worker.py
│
├── ui/
│   ├── painter.py
│   └── notification_popup.py
│
├── main.py
│
└── README.md
```

*The structure above highlights selected components and is not an exhaustive directory listing.*

## Installation

### Requirements

- Windows 10 or Windows 11
- Python 3.10 or later
- pip

### Setup

1. Clone the repository:

```bash
git clone YOUR_REPOSITORY_URL
```

2. Navigate into the project directory:

```bash
cd WINDOWS-GLANCE
```

3. Create a virtual environment:

```bash
python -m venv .venv
```

4. Activate the environment:

```powershell
.venv\Scripts\Activate.ps1
```

5. Install dependencies:

```bash
pip install PySide6
```

6. Launch the application:

```bash
python main.py
```

## Development Status

Windows Glance is actively under development. Its architecture, interface, and functionality are continuously evolving.

## Vision

The long-term goal is to develop Windows Glance into a comprehensive desktop intelligence platform that combines system awareness, monitoring, notifications, and useful contextual information in one application.

## License

License information will be provided in a future release.
