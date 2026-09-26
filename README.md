# Campus Lost & Found Platform

An interactive dashboard that reunites students with their belongings.

## Setup Instructions

Open your terminal and run the following commands to set up the virtual environment, install requirements, and run the server locally.

### Windows (PowerShell)

```powershell
# Navigate to the project directory
cd C:\Users\sandy\.gemini\antigravity\scratch\campus_lost_and_found

# Create a virtual environment
python -m venv venv

# Activate the virtual environment
.\venv\Scripts\Activate.ps1

# Install the dependencies
pip install -r requirements.txt

# Run the Flask development server
python app.py
```

### macOS/Linux

```bash
# Navigate to the project directory
cd path/to/campus_lost_and_found

# Create a virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Install the dependencies
pip install -r requirements.txt

# Run the Flask development server
python app.py
```

The application will be available at `http://127.0.0.1:5000/`.

## Features
- Interactive dashboard for lost and found items.
- Dynamic form toggling for "Lost" and "Found" submissions.
- Image uploads for found items.
- SQLite database integration.
- 30-Day rule indicator for stale data.
- Client-side category filtering.
